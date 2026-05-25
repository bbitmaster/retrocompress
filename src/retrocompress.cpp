// optkirby.cpp - optimal Kirby-format encoder via DP shortest-path.
//
// Algorithm:
//   At each output position i in 0..N, best[i] = minimum compressed bytes
//   needed to produce output[0..i-1]. We process i in order and RELAX edges
//   that LEAVE i, going to i+L. Within each header regime (1B header for
//   len≤32, 2B for 33..1024), block cost is constant in L for the compressed
//   types, so only max-length per type per regime is interesting.
//
// Complexity (this implementation):
//   - DP loop: O(n) — each i emits O(1) edges per compressed type
//   - Raw blocks: O(n) via sliding-window-min over f(j) = best[j] - j
//     (two windows: [i-32, i-1] for 1B header, [i-1024, i-33] for 2B header)
//   - LZ-copy match finding: O(n log n) via prefix-doubling SA + Kasai LCP + LPF
//     (true O(n) achievable by swapping in SA-IS; deferred)
//   - LZ-bitrev / LZ-rev: still O(n^2) for now (constrained LPF queries are
//     more involved; see TODO at bottom)
//
// Overall: O(n log n) average, O(n^2) worst case dominated by LZ-bitrev/LZ-rev
// match finding. For typical Kirby blocks (1-2 KB) this runs in ~tens of us.

#include "retrocompress.h"
#include <cstring>
#include <vector>
#include <algorithm>
#include <deque>
#include <set>

namespace Retrocompress {

enum Type { T_RAW=0, T_RLE=1, T_RLE2=2, T_RLEINC=3, T_LZ=4, T_LZBR=5, T_LZREV=6 };

struct Step { int prev; int type; int enc_len; int addr; };
struct LZRef { int len; int addr; };

static inline int header_size(int enc_len) { return enc_len <= 32 ? 1 : 2; }

static inline u8 bitrev_u8(u8 b) {
    u8 r = 0;
    for (int k = 0; k < 8; k++) { r = (r << 1) | (b & 1); b >>= 1; }
    return r;
}

int worst_compress_size(int srclen) {
    int chunks = (srclen + 1023) / 1024;
    return chunks * 2 + srclen + 1 + 16;
}

// --- Precomputations -- O(n) -----------------------------------------------

static void precompute_rle1(const u8* s, int N, std::vector<int>& out) {
    out.assign(N + 1, 0);
    if (N == 0) return;
    out[N-1] = 1;
    for (int i = N - 2; i >= 0; --i)
        out[i] = (s[i] == s[i+1]) ? std::min(out[i+1] + 1, 1024) : 1;
}

static void precompute_rleinc(const u8* s, int N, std::vector<int>& out) {
    out.assign(N + 1, 0);
    if (N == 0) return;
    out[N-1] = 1;
    for (int i = N - 2; i >= 0; --i)
        out[i] = ((u8)(s[i] + 1) == s[i+1]) ? std::min(out[i+1] + 1, 1024) : 1;
}

// 2-byte RLE: at position i, longest encoded length L such that
// s[i+2j..i+2j+1] = (s[i], s[i+1]) for j=0..L-1.
// Linear-time via period-2 extension scan from right to left.
static void precompute_rle2(const u8* s, int N, std::vector<int>& out) {
    out.assign(N + 1, 0);
    if (N < 2) return;
    // ext[i] = max number of bytes from position i forming a period-2 pattern
    //         with character pair (s[i], s[i+1]); always >= 2.
    std::vector<int> ext(N, 0);
    ext[N-2] = 2; ext[N-1] = 1;
    for (int i = N - 3; i >= 0; --i) {
        if (s[i+2] == s[i]) {
            // Either ext[i+1] starts the same period-2 pattern (s[i+1], s[i+2]=s[i]),
            // in which case it's the (b,a,b,a,...) pattern == our (a,b,a,b,...) shifted,
            // so ext[i] = ext[i+1] + 1.
            // BUT we also need s[i+3] == s[i+1] which is what ext[i+1] >= 2 + 1 = 3 means.
            // Simpler: extend by 2 if pair (s[i], s[i+1]) matches (s[i+2], s[i+3]).
            ext[i] = 2 + (i + 3 < N && s[i+3] == s[i+1] ? ext[i+2] : 0);
        } else {
            ext[i] = 2;
        }
    }
    // Cap and translate to encoded length L = floor(ext[i] / 2), max 1024.
    for (int i = 0; i < N; ++i) out[i] = std::min(ext[i] / 2, 1024);
}

// --- Suffix array via SA-IS -- O(n) ----------------------------------------
// SA[k] = starting position of the k-th smallest suffix of s[0..N-1].
//
// Implementation of Nong, Zhang, Chan (2009) "Linear Suffix Array Construction
// by Almost Pure Induced-Sorting". Bytes are shifted to 1..256 and a 0 byte
// is appended as sentinel; the sentinel position (always SA[0]=N) is then
// dropped before returning.
//
// Internal routine works on int alphabet so it can recurse on the reduced
// string of LMS names.

static void sais_get_buckets(const int* T, int* bkt, int n, int K, bool end) {
    for (int i = 0; i < K; ++i) bkt[i] = 0;
    for (int i = 0; i < n; ++i) ++bkt[T[i]];
    int sum = 0;
    for (int i = 0; i < K; ++i) { sum += bkt[i]; bkt[i] = end ? sum : sum - bkt[i]; }
}

static void sais_induce_L(const std::vector<char>& t, int* SA, const int* T, int* bkt, int n, int K) {
    sais_get_buckets(T, bkt, n, K, /*end=*/false);
    for (int i = 0; i < n; ++i) {
        int j = SA[i] - 1;
        if (j >= 0 && !t[j]) SA[bkt[T[j]]++] = j;
    }
}

static void sais_induce_S(const std::vector<char>& t, int* SA, const int* T, int* bkt, int n, int K) {
    sais_get_buckets(T, bkt, n, K, /*end=*/true);
    for (int i = n - 1; i >= 0; --i) {
        int j = SA[i] - 1;
        if (j >= 0 && t[j]) SA[--bkt[T[j]]] = j;
    }
}

// Returns true if two LMS-substrings starting at p1 and p2 are identical.
static bool sais_lms_equal(const int* T, const std::vector<char>& t, int n, int p1, int p2) {
    if (p1 == p2) return true;
    for (int d = 0; ; ++d) {
        if (p1 + d >= n || p2 + d >= n) return false;
        if (T[p1+d] != T[p2+d] || t[p1+d] != t[p2+d]) return false;
        if (d > 0) {
            bool a_lms = (t[p1+d] && !t[p1+d-1]);
            bool b_lms = (t[p2+d] && !t[p2+d-1]);
            if (a_lms || b_lms) return a_lms && b_lms;
        }
    }
}

static void sa_is_impl(const int* T, int* SA, int n, int K) {
    if (n == 1) { SA[0] = 0; return; }
    if (n == 2) {
        if (T[0] < T[1]) { SA[0] = 0; SA[1] = 1; }
        else { SA[0] = 1; SA[1] = 0; }
        return;
    }

    // Type classification: t[i]=1 means S-type, 0 means L-type.
    std::vector<char> t(n);
    t[n-1] = 1;
    for (int i = n - 2; i >= 0; --i)
        t[i] = (T[i] < T[i+1]) || (T[i] == T[i+1] && t[i+1]) ? 1 : 0;

    std::vector<int> bkt(K);

    // Place LMS positions at the tail of their buckets.
    std::fill(SA, SA + n, -1);
    sais_get_buckets(T, bkt.data(), n, K, /*end=*/true);
    for (int i = 1; i < n; ++i)
        if (t[i] && !t[i-1]) SA[--bkt[T[i]]] = i;
    sais_induce_L(t, SA, T, bkt.data(), n, K);
    sais_induce_S(t, SA, T, bkt.data(), n, K);

    // Compact LMS positions to the start of SA (in their SA-induced order).
    int n1 = 0;
    for (int i = 0; i < n; ++i) {
        int p = SA[i];
        if (p > 0 && t[p] && !t[p-1]) SA[n1++] = p;
    }
    for (int i = n1; i < n; ++i) SA[i] = -1;

    // Name LMS substrings; store names in SA[n1..n-1] at offset pos/2.
    int name = 0;
    int prev = -1;
    for (int i = 0; i < n1; ++i) {
        int pos = SA[i];
        if (prev == -1 || !sais_lms_equal(T, t, n, pos, prev)) {
            ++name; prev = pos;
        }
        SA[n1 + pos / 2] = name - 1;
    }
    // Compact names contiguously into SA[n - n1 .. n - 1].
    int k = n - 1;
    for (int i = n - 1; i >= n1; --i)
        if (SA[i] >= 0) SA[k--] = SA[i];

    int* SA1 = SA;
    int* T1 = SA + n - n1;

    if (name < n1) {
        // Recurse on reduced string of LMS names.
        sa_is_impl(T1, SA1, n1, name);
    } else {
        // All names distinct - SA1 is the inverse of T1.
        for (int i = 0; i < n1; ++i) SA1[T1[i]] = i;
    }

    // Translate reduced-SA indices back to LMS positions in the original.
    std::vector<int> p1(n1);
    int idx = 0;
    for (int i = 1; i < n; ++i)
        if (t[i] && !t[i-1]) p1[idx++] = i;
    for (int i = 0; i < n1; ++i) SA1[i] = p1[SA1[i]];
    for (int i = n1; i < n; ++i) SA[i] = -1;

    // Final induced sort using the now-sorted LMS positions.
    sais_get_buckets(T, bkt.data(), n, K, /*end=*/true);
    for (int i = n1 - 1; i >= 0; --i) {
        int p = SA[i];
        SA[i] = -1;
        SA[--bkt[T[p]]] = p;
    }
    sais_induce_L(t, SA, T, bkt.data(), n, K);
    sais_induce_S(t, SA, T, bkt.data(), n, K);
}

static std::vector<int> build_sa(const u8* s, int N) {
    if (N == 0) return {};
    if (N == 1) return {0};
    // Shift bytes to 1..256, append 0 as unique smallest sentinel.
    std::vector<int> T(N + 1);
    for (int i = 0; i < N; ++i) T[i] = (int)s[i] + 1;
    T[N] = 0;
    std::vector<int> SA(N + 1);
    sa_is_impl(T.data(), SA.data(), N + 1, 257);
    // SA[0] is the sentinel position (= N); drop it.
    return std::vector<int>(SA.begin() + 1, SA.end());
}

// Kasai's LCP -- O(n)
// lcp[i] = LCP(sa[i-1], sa[i]); lcp[0] = 0.
static std::vector<int> build_lcp(const u8* s, int N, const std::vector<int>& sa) {
    std::vector<int> rank(N), lcp(N, 0);
    for (int i = 0; i < N; ++i) rank[sa[i]] = i;
    int h = 0;
    for (int i = 0; i < N; ++i) {
        if (rank[i] == 0) { h = 0; continue; }
        int j = sa[rank[i] - 1];
        while (i + h < N && j + h < N && s[i + h] == s[j + h]) ++h;
        lcp[rank[i]] = h;
        if (h > 0) --h;
    }
    return lcp;
}

// LPF[i] = longest match of s[i..] in s starting at some j < i, along with that j.
// Uses SA + LCP. O(n) via stack-based sweep.
static void build_lpf(const u8* s, int N, std::vector<LZRef>& lpf) {
    lpf.assign(N + 1, {0, 0});
    if (N == 0) return;
    auto sa = build_sa(s, N);
    auto lcp = build_lcp(s, N, sa);
    std::vector<int> rank(N);
    for (int k = 0; k < N; ++k) rank[sa[k]] = k;

    // For each suffix at SA-rank r, find prev_lt[r] = largest q < r with sa[q] < sa[r],
    // and next_lt[r] = smallest q > r with sa[q] < sa[r]. LPF[sa[r]] is the max LCP
    // along the SA-path from r to prev_lt[r] (or next_lt[r]).
    // Standard implementation: two-pass with stacks.
    std::vector<int> prev_lt(N, -1), next_lt(N, -1);
    {
        std::vector<int> st; st.reserve(N);
        for (int r = 0; r < N; ++r) {
            while (!st.empty() && sa[st.back()] >= sa[r]) st.pop_back();
            prev_lt[r] = st.empty() ? -1 : st.back();
            st.push_back(r);
        }
        st.clear();
        for (int r = N - 1; r >= 0; --r) {
            while (!st.empty() && sa[st.back()] >= sa[r]) st.pop_back();
            next_lt[r] = st.empty() ? -1 : st.back();
            st.push_back(r);
        }
    }
    // To compute "min lcp along SA-path from r to q" in O(1), build a sparse-table
    // RMQ on lcp[]. For simplicity, use O(log n) per query — total O(n log n).
    // For our sizes this is plenty fast.
    int LOG = 1;
    while ((1 << LOG) <= N) ++LOG;
    std::vector<std::vector<int>> st(LOG, std::vector<int>(N, 0));
    st[0] = lcp;
    for (int j = 1; j < LOG; ++j)
        for (int i = 0; i + (1 << j) <= N; ++i)
            st[j][i] = std::min(st[j-1][i], st[j-1][i + (1 << (j-1))]);
    auto rmq = [&](int l, int r) -> int { // [l, r), l < r, returns min lcp
        if (l >= r) return 0;
        int k = 0; while ((1 << (k + 1)) <= r - l) ++k;
        return std::min(st[k][l], st[k][r - (1 << k)]);
    };

    for (int i = 0; i < N; ++i) {
        int r = rank[i];
        int best_len = 0, best_addr = 0;
        if (prev_lt[r] >= 0) {
            int q = prev_lt[r];
            int L = rmq(q + 1, r + 1);
            if (L > best_len) { best_len = L; best_addr = sa[q]; }
        }
        if (next_lt[r] >= 0) {
            int q = next_lt[r];
            int L = rmq(r + 1, q + 1);
            if (L > best_len) { best_len = L; best_addr = sa[q]; }
        }
        if (best_len > 1024) best_len = 1024;
        lpf[i] = {best_len, best_addr};
    }
}

// --- Constrained LPF for LZ-bitrev and LZ-rev -- O(n log n) ----------------
//
// Common pattern: we build SA + LCP + RMQ over a 2-string concatenation
// t = source + sep + target. For each target position i (in 0..N-1), the
// allowed source positions are some monotonically-growing set as i increases.
// We process targets in i ascending order, maintaining a std::set of active
// source positions keyed by SA-rank. For each target, the best match is at
// either the predecessor or successor in rank order (LCP via RMQ).
//
// LZ-bitrev: source = br_s = bitrev(s); allowed source j in [0, i-1].
// LZ-rev:    source = rev_s = reverse(s); allowed source p in [N-i, N-1]
//            (which corresponds to original-string addr in [0, i-1]).

struct ConcatSA {
    int N;                          // length of source = length of target = N
    int total;                      // = 2*N + 1
    std::vector<int> sa, rank, lcp;
    std::vector<std::vector<int>> sparse;
    int log_total;

    void build(const u8* src_str, const u8* tgt_str, int n) {
        N = n;
        total = 2 * N + 1;
        std::vector<u8> t(total);
        for (int i = 0; i < N; ++i) t[i] = src_str[i];
        t[N] = 0; // separator (smallest byte). Real bytes are 0..255 but the
                  // separator being equal to some byte is OK because we use
                  // explicit position constraints in queries (sources are in
                  // t[0..N-1] only).
        for (int i = 0; i < N; ++i) t[N + 1 + i] = tgt_str[i];

        sa = build_sa(t.data(), total);
        rank.assign(total, 0);
        for (int i = 0; i < total; ++i) rank[sa[i]] = i;
        lcp = build_lcp(t.data(), total, sa);

        log_total = 1;
        while ((1 << log_total) <= total) ++log_total;
        sparse.assign(log_total, std::vector<int>(total, 0));
        sparse[0] = lcp;
        for (int j = 1; j < log_total; ++j)
            for (int i = 0; i + (1 << j) <= total; ++i)
                sparse[j][i] = std::min(sparse[j-1][i], sparse[j-1][i + (1 << (j-1))]);
    }

    int rmq(int l, int r) const { // min of lcp[l..r-1]
        if (l >= r) return 0;
        int k = 0; while ((1 << (k + 1)) <= r - l) ++k;
        return std::min(sparse[k][l], sparse[k][r - (1 << k)]);
    }

    // LCP of two suffixes (by their start positions in t)
    int lcp_pos(int a, int b) const {
        if (a == b) return total - a;
        int ra = rank[a], rb = rank[b];
        if (ra > rb) std::swap(ra, rb);
        return rmq(ra + 1, rb + 1);
    }
};

// Generic constrained-LPF query for both LZ-bitrev and LZ-rev.
// add_source_for_target[i] = source position to add when processing target i.
// max_match_len_at(src_pos, i) limits the length to satisfy any extra constraint.
// remap_addr translates internal source position to the addr that gets written
// into the compressed stream.
static void compute_constrained_lpf(
    const ConcatSA& csa, int N,
    int max_total_len,
    const std::vector<int>& add_source_for_target, // length N
    int (*remap_addr)(int src_pos, int N),
    std::vector<LZRef>& out)
{
    out.assign(N, {0, 0});

    // ranks_active: SA-ranks of currently-active source positions.
    std::set<std::pair<int,int>> active; // (sa_rank, src_pos)

    for (int i = 0; i < N; ++i) {
        // Activate the source designated for this i.
        int src_to_add = add_source_for_target[i];
        if (src_to_add >= 0 && src_to_add < N) {
            active.insert({csa.rank[src_to_add], src_to_add});
        }

        if (active.empty()) { out[i] = {0, 0}; continue; }

        // Target suffix in t starts at position csa.N + 1 + i.
        int tgt_pos = N + 1 + i;
        int tgt_rank = csa.rank[tgt_pos];

        int best_len = 0, best_addr = 0;

        auto consider = [&](int src_pos) {
            int len = csa.lcp_pos(src_pos, tgt_pos);
            // Cap by max_total_len and available bytes (don't run past source end).
            int avail_src = N - src_pos;
            int avail_tgt = N - i;
            len = std::min({len, avail_src, avail_tgt, max_total_len});
            if (len > best_len) { best_len = len; best_addr = remap_addr(src_pos, N); }
        };

        // Predecessor in active by SA-rank.
        auto it = active.lower_bound({tgt_rank, -1});
        if (it != active.begin()) { auto pred = std::prev(it); consider(pred->second); }
        if (it != active.end())   { consider(it->second); }

        out[i] = {best_len, best_addr};
    }
}

static int remap_identity(int src_pos, int /*N*/) { return src_pos; }
static int remap_revsrc(int p, int N) { return N - 1 - p; }

static void precompute_lzbr(const u8* s, int N, std::vector<LZRef>& out) {
    if (N == 0) { out.clear(); return; }
    std::vector<u8> br(N);
    for (int i = 0; i < N; ++i) br[i] = bitrev_u8(s[i]);
    ConcatSA csa; csa.build(br.data(), s, N);
    // LZ-bitrev: at target i, allowed source j in [0, i-1]; we add source j = i-1
    // when entering iteration i. (We skip target i=0 because no source < 0.)
    std::vector<int> add(N, -1);
    for (int i = 1; i <= N; ++i) if (i - 1 < N) add[i - 1] = -1; // (handled below)
    // The natural schedule: when target i is processed, sources [0, i-1] should be active.
    // Equivalently, at iteration i we add source j = i-1 (if i-1 >= 0).
    // But we want at i=0: no sources active (matches naive: starts at i=1 in old loop).
    // So at iteration i, add source j = i - 1 (which equals -1 for i=0, i.e., no add).
    for (int i = 0; i < N; ++i) add[i] = i - 1;
    compute_constrained_lpf(csa, N, 1024, add, remap_identity, out);
}

static void precompute_lzrev(const u8* s, int N, std::vector<LZRef>& out) {
    if (N == 0) { out.clear(); return; }
    std::vector<u8> rev(N);
    for (int i = 0; i < N; ++i) rev[i] = s[N - 1 - i];
    ConcatSA csa; csa.build(rev.data(), s, N);
    // LZ-rev: at target i in s, allowed source p in rev_s with p in [N-i, N-1],
    // which means original-string addr = N-1-p in [0, i-1].
    // Schedule: at iteration i, add source p = N - i (the new lower bound). For i=0
    // that would be p = N which is out of range; we skip.
    std::vector<int> add(N, -1);
    for (int i = 1; i < N; ++i) add[i] = N - i;
    // Output addr should be the s-position. remap_revsrc translates rev_s position to s addr.
    compute_constrained_lpf(csa, N, 1024, add, remap_revsrc, out);
}

// --- Sliding window min for raw block edges -- O(n) ------------------------
// At each i+1..N, the optimal raw arrival is:
//   best_via_raw(t) = min over j in [t-1024, t-1] of best[j] + h(t-j) + (t-j)
//                   = t + min(min_{j in [t-32, t-1]}   (best[j] - j) + 1,
//                              min_{j in [t-1024, t-33]} (best[j] - j) + 2)
// Maintain two monotonic deques for f(j) = best[j] - j, one for window size 32,
// one for window size 992 = 1024 - 32. As we process j in order, we feed f(j)
// into both queues with appropriate delays.

struct MonoMinDeque {
    // Window contains indices [oldest_idx_in_window..most_recent_idx_in_window].
    // f(idx) values are inserted at index idx, removed when idx falls out of window.
    std::deque<std::pair<int,int>> q; // (idx, value)
    int win_size;
    explicit MonoMinDeque(int w) : win_size(w) {}
    void push(int idx, int v) {
        while (!q.empty() && q.back().second >= v) q.pop_back();
        q.emplace_back(idx, v);
    }
    void evict_before(int min_idx) {
        while (!q.empty() && q.front().first < min_idx) q.pop_front();
    }
    bool empty() const { return q.empty(); }
    int min_val() const { return q.front().second; }
    int min_idx() const { return q.front().first; }
};

// --- DP and emit -----------------------------------------------------------

int compress(const u8* src, int N, u8* dst) {
    if (N == 0) { dst[0] = 0xFF; return 1; }

    std::vector<int> rle1, rle2, rleinc;
    std::vector<LZRef> lz, lzbr, lzrev;
    precompute_rle1(src, N, rle1);
    precompute_rle2(src, N, rle2);
    precompute_rleinc(src, N, rleinc);
    build_lpf(src, N, lz);
    precompute_lzbr(src, N, lzbr);
    precompute_lzrev(src, N, lzrev);

    const int INF = 1 << 29;
    std::vector<int> best(N + 1, INF);
    std::vector<Step> step(N + 1);
    best[0] = 0;

    // Sliding window mins for raw edges arriving at t.
    // Short window: j in [t-32, t-1] -> length L = t-j in [1, 32], cost = 1 + L
    // Long window:  j in [t-1024, t-33] -> L in [33, 1024], cost = 2 + L
    // A value f(j) = best[j] - j enters the short window at t = j+1 and
    // graduates to the long window at t = j+33.
    MonoMinDeque ms2(32), ml2(992);

    auto relax = [&](int to, int cost, int prev, int type, int enc_len, int addr) {
        if (to <= N && cost < best[to]) {
            best[to] = cost;
            step[to] = {prev, type, enc_len, addr};
        }
    };

    auto try_compressed2 = [&](int i, int Lmax_enc, int Lmax_out, int payload, int type, int addr) {
        if (Lmax_enc < 1) return;
        int Lshort_enc = std::min(Lmax_enc, 32);
        int Lshort_out = (type == T_RLE2) ? Lshort_enc * 2 : Lshort_enc;
        relax(i + Lshort_out, best[i] + 1 + payload, i, type, Lshort_enc, addr);
        if (Lmax_enc > 32) {
            relax(i + Lmax_out, best[i] + 2 + payload, i, type, Lmax_enc, addr);
        }
    };

    for (int t = 0; t <= N; ++t) {
        // Push f(t-33) into long-window deque at the start of iteration t (if valid).
        if (t - 33 >= 0 && best[t - 33] < INF) {
            ml2.push(t - 33, best[t - 33] - (t - 33));
        }

        // Raw arrivals at t.
        if (t >= 1) {
            ms2.evict_before(t - 32);
            ml2.evict_before(t - 1024);
            if (!ms2.empty()) {
                int cand = ms2.min_val() + 1 + t;
                if (cand < best[t]) {
                    int js = ms2.min_idx();
                    best[t] = cand;
                    step[t] = {js, T_RAW, t - js, 0};
                }
            }
            if (!ml2.empty()) {
                int cand = ml2.min_val() + 2 + t;
                if (cand < best[t]) {
                    int jl = ml2.min_idx();
                    best[t] = cand;
                    step[t] = {jl, T_RAW, t - jl, 0};
                }
            }
        }

        // Push f(t) into short deque after best[t] is finalized.
        if (t < N && best[t] < INF) ms2.push(t, best[t] - t);

        // Emit compressed edges from t.
        if (t < N && best[t] < INF) {
            try_compressed2(t, rle1[t],   rle1[t],     1, T_RLE,   0);
            try_compressed2(t, rle2[t],   rle2[t] * 2, 2, T_RLE2,  0);
            try_compressed2(t, rleinc[t], rleinc[t],   1, T_RLEINC,0);
            if (lz[t].len > 0)    try_compressed2(t, lz[t].len,    lz[t].len,    2, T_LZ,    lz[t].addr);
            if (lzbr[t].len > 0)  try_compressed2(t, lzbr[t].len,  lzbr[t].len,  2, T_LZBR,  lzbr[t].addr);
            if (lzrev[t].len > 0) try_compressed2(t, lzrev[t].len, lzrev[t].len, 2, T_LZREV, lzrev[t].addr);
        }
    }

    if (best[N] >= INF) return -1;

    // Traceback
    std::vector<Step> chain;
    for (int cur = N; cur > 0; cur = step[cur].prev)
        chain.push_back(step[cur]);
    std::reverse(chain.begin(), chain.end());

    // Emit bytes
    int pos = 0, src_pos = 0;
    for (const Step& s : chain) {
        int h = header_size(s.enc_len);
        if (h == 1) {
            dst[pos++] = (s.type << 5) | ((s.enc_len - 1) & 0x1F);
        } else {
            int Lm1 = s.enc_len - 1;
            dst[pos++] = 0xE0 | (s.type << 2) | ((Lm1 >> 8) & 0x03);
            dst[pos++] = Lm1 & 0xFF;
        }
        switch (s.type) {
            case T_RAW:
                std::memcpy(dst + pos, src + src_pos, s.enc_len);
                pos += s.enc_len; src_pos += s.enc_len; break;
            case T_RLE:
                dst[pos++] = src[src_pos]; src_pos += s.enc_len; break;
            case T_RLE2:
                dst[pos++] = src[src_pos];
                dst[pos++] = src[src_pos + 1];
                src_pos += 2 * s.enc_len; break;
            case T_RLEINC:
                dst[pos++] = src[src_pos]; src_pos += s.enc_len; break;
            case T_LZ:
            case T_LZBR:
            case T_LZREV:
                dst[pos++] = (s.addr >> 8) & 0xFF;
                dst[pos++] = s.addr & 0xFF;
                src_pos += s.enc_len; break;
        }
    }
    dst[pos++] = 0xFF;
    return pos;
}

// --- Super Metroid decompressor (different LZ variants than Kirby) --------
// Mirrors the hardware decoder at $80:B119 in PJBoy's disassembly:
//   cmd 4 = LZ absolute 2-byte addr
//   cmd 5 = LZ absolute 2-byte addr, EOR FF each byte
//   cmd 6 = LZ relative 1-byte distance
//   cmd 7 = LZ relative 1-byte distance, EOR FF each byte
int decompress_sm(const u8* src, int srclen, u8* dst) {
    int i = 0, j = 0;
    while (i < srclen) {
        u8 ctrl = src[i++];
        if (ctrl == 0xFF) return j;
        int cmd = ctrl >> 5, len;
        if (cmd == 7) {
            if (i >= srclen) return -1;
            cmd = (ctrl >> 2) & 7;
            len = (((ctrl & 3) << 8) | src[i++]) + 1;
        } else len = (ctrl & 0x1F) + 1;
        switch (cmd) {
            case 0:
                if (i + len > srclen) return -1;
                if (dst) std::memcpy(dst + j, src + i, len);
                i += len; j += len; break;
            case 1: {
                if (i >= srclen) return -1;
                u8 b = src[i++];
                if (dst) std::memset(dst + j, b, len);
                j += len; break;
            }
            case 2: {
                if (i + 1 >= srclen) return -1;
                u8 a = src[i++], b = src[i++];
                for (int k = 0; k < len; k++) { if (dst) { dst[j]=a; dst[j+1]=b; } j += 2; }
                break;
            }
            case 3: {
                if (i >= srclen) return -1;
                u8 b = src[i++];
                for (int k = 0; k < len; k++) { if (dst) dst[j] = b; ++b; ++j; }
                break;
            }
            case 4: case 5: {
                if (i + 1 >= srclen) return -1;
                int addr = src[i] | (src[i+1] << 8); i += 2;
                if (addr >= j) return -1;
                bool invert = (cmd == 5);
                for (int k = 0; k < len; k++) {
                    u8 b = dst ? dst[addr] : 0;
                    if (invert) b ^= 0xFF;
                    if (dst) dst[j] = b;
                    ++j; ++addr;
                }
                break;
            }
            case 6: case 7: {
                if (i >= srclen) return -1;
                int dist = src[i++];
                if (dist == 0 || dist > j) return -1;
                int addr = j - dist;
                bool invert = (cmd == 7);
                for (int k = 0; k < len; k++) {
                    u8 b = dst ? dst[addr] : 0;
                    if (invert) b ^= 0xFF;
                    if (dst) dst[j] = b;
                    ++j; ++addr;
                }
                break;
            }
        }
    }
    return -1;
}

// --- Decompressor (Kirby format) -----------------------------------------

int decompress(const u8* src, int srclen, u8* dst) {
    int i = 0, j = 0;
    while (i < srclen) {
        u8 ctrl = src[i++];
        if (ctrl == 0xFF) return j;
        int cmd = ctrl >> 5, len;
        if (cmd == 7) {
            if (i >= srclen) return -1;
            cmd = (ctrl >> 2) & 7;
            len = (((ctrl & 3) << 8) | src[i++]) + 1;
        } else len = (ctrl & 0x1F) + 1;
        if (cmd == 7) cmd = 4;
        switch (cmd) {
            case 0:
                if (i + len > srclen) return -1;
                if (dst) std::memcpy(dst + j, src + i, len);
                i += len; j += len; break;
            case 1: {
                if (i >= srclen) return -1;
                u8 b = src[i++];
                if (dst) std::memset(dst + j, b, len);
                j += len; break;
            }
            case 2: {
                if (i + 1 >= srclen) return -1;
                u8 a = src[i++], b = src[i++];
                for (int k = 0; k < len; k++) { if (dst) { dst[j]=a; dst[j+1]=b; } j += 2; }
                break;
            }
            case 3: {
                if (i >= srclen) return -1;
                u8 b = src[i++];
                for (int k = 0; k < len; k++) { if (dst) dst[j] = b; ++b; ++j; }
                break;
            }
            case 4: case 5: case 6: {
                if (i + 1 >= srclen) return -1;
                int addr = (src[i] << 8) | src[i+1]; i += 2;
                if (addr >= j) return -1;
                if (cmd == 4) {
                    for (int k = 0; k < len; k++) { if (dst) dst[j] = dst[addr]; ++j; ++addr; }
                } else if (cmd == 5) {
                    for (int k = 0; k < len; k++) {
                        u8 b = dst ? dst[addr] : 0;
                        if (dst) dst[j] = bitrev_u8(b);
                        ++j; ++addr;
                    }
                } else {
                    if (addr - (len - 1) < 0) return -1;
                    for (int k = 0; k < len; k++) { if (dst) dst[j] = dst[addr]; ++j; --addr; }
                }
                break;
            }
        }
    }
    return -1;
}

} // namespace Retrocompress

// TODO for true O(n) overall:
//   - Replace prefix-doubling SA with SA-IS (linear-time SA construction).
//   - LZ-bitrev: build SA over concatenation br_s + '$' + s; compute constrained
//     LPF where the source position must lie in the br_s portion AND before i.
//   - LZ-rev: similarly with rev_s + '$' + s; constraint is more involved
//     because source index transformation depends on match length.
