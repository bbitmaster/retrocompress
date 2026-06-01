// retrocompress.cpp - optimal LZ encoder for the HAL/Nintendo "3-bit cmd + 5-bit
// length" compression family (Kirby NES/SNES, SMW LC_LZ2, etc.).
//
// Algorithm:
//   At each output position i in 0..N, best[i] = minimum compressed bytes
//   needed to produce output[0..i-1]. We process i in order and RELAX edges
//   that LEAVE i, going to i+L. Within each header regime (1B header for
//   len≤32, 2B for 33..1024), block cost is constant in L for the compressed
//   types, so only max-length per type per regime is interesting.
//
// Match finding: one suffix array over s || rev(s) || bitrev(s) (built with
// libsais), then on-demand SA-neighbor walks inside the DP loop pick up best
// LZ + bit-reverse + reverse-order matches in a single pass per position.
// Raw blocks use sliding-window-min for O(n) amortized handling.

#include "retrocompress.h"
#include <climits>
#include <cstdint>
#include <cstring>
#include <vector>
#include <algorithm>
#include <deque>
extern "C" {
#include "libsais.h"
}

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

// --- Suffix array via libsais ----------------------------------------------
// libsais (Ilya Grebnov, MIT) — modern actively-tuned SA-IS implementation
// with SIMD-friendly inner loops. Typically 2-3x faster than libdivsufsort
// (the SA library snes-squish uses) on byte-alphabet inputs of our size.
// SA[k] = starting position of the k-th smallest suffix of s[0..N-1].

static std::vector<int> build_sa(const u8* s, int N) {
    if (N == 0) return {};
    if (N == 1) return {0};
    std::vector<int> SA(N);
    libsais(s, SA.data(), (int32_t)N, /*fs=*/0, /*freq=*/nullptr);
    return SA;
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

// --- ConcatSA3: one SA over s || rev(s) || bitrev(s) ---------------------
// Holds the suffix array, rank table, and LCP array. No precomputed LPF, no
// sparse-table RMQ, no active-source set. Match finding is done on demand by
// walking SA neighbors of the target rank and tracking running min-LCP — the
// same shape as snes-squish's find_backref (lib.rs:299-405).

struct ConcatSA3 {
    int N;       // length of original s
    int total;   // = 3 * N (no separators)
    std::vector<int> sa, rank, lcp;

    void build(const u8* s, int n) {
        N = n;
        total = 3 * N;
        if (total == 0) return;
        std::vector<u8> t(total);
        for (int i = 0; i < N; ++i) t[i] = s[i];                      // s
        for (int i = 0; i < N; ++i) t[N + i] = s[N - 1 - i];          // rev(s)
        for (int i = 0; i < N; ++i) t[2*N + i] = bitrev_u8(s[i]);     // bitrev(s)
        // No separator. Region overruns in LCP are harmless: the match-finder
        // explicitly clamps match length by `available bytes in source region`
        // and `available bytes in target`.
        sa = build_sa(t.data(), total);
        rank.assign(total, 0);
        for (int i = 0; i < total; ++i) rank[sa[i]] = i;
        lcp = build_lcp(t.data(), total, sa);
    }
};

// Best (length, addr) pair per backref kind, found at needle position in s.
struct AltMatches {
    int lz_len, lz_addr;
    int bitrev_len, bitrev_addr;
    int rev_len, rev_addr;
};

// On-demand match finder. Walks SA neighbors of rank[needle] upward and
// downward, tracking running min-LCP. For each neighbor, classifies by which
// region of the concat it belongs to (s / rev / bitrev), maps back to the
// original-source address that the Kirby decoder will read, validates the
// source comes from already-emitted data, then records the best per type.
//
// Stops walking once min-LCP drops below 3 (no backref shorter than 3 bytes
// can beat a raw block in the Kirby format).
static AltMatches find_alt_matches(const ConcatSA3& csa, int N, int needle) {
    AltMatches m{0, 0, 0, 0, 0, 0};
    if (csa.total == 0) return m;
    const int MIN_MATCH = 3;
    const int MAX_MATCH = 1024;
    int start_rank = csa.rank[needle];

    auto consider = [&](int sa_pos, int matchlen) {
        int typ;        // 0=LZ, 1=rev, 2=bitrev
        int orig_addr;  // address written into the compressed stream
        int avail_src;  // bytes the decompressor can read from this source
        if (sa_pos < N) {
            typ = 0; orig_addr = sa_pos; avail_src = N - sa_pos;
        } else if (sa_pos < 2*N) {
            typ = 1;
            int rev_pos = sa_pos - N;       // index into rev(s)
            orig_addr = N - 1 - rev_pos;    // original-s position of the LAST byte (the byte the decoder reads first)
            avail_src = orig_addr + 1;      // decoder reads backward, so source available = orig_addr+1 bytes
        } else {
            typ = 2;
            orig_addr = sa_pos - 2*N;
            avail_src = N - orig_addr;
        }
        if (orig_addr >= needle) return; // not yet emitted at this point in DP
        int eff = matchlen;
        if (avail_src < eff)   eff = avail_src;
        if (N - needle < eff)  eff = N - needle;
        if (MAX_MATCH < eff)   eff = MAX_MATCH;
        if (eff < MIN_MATCH) return;
        switch (typ) {
            case 0: if (eff > m.lz_len)     { m.lz_len     = eff; m.lz_addr     = orig_addr; } break;
            case 1: if (eff > m.rev_len)    { m.rev_len    = eff; m.rev_addr    = orig_addr; } break;
            case 2: if (eff > m.bitrev_len) { m.bitrev_len = eff; m.bitrev_addr = orig_addr; } break;
        }
    };

    int matchlen = INT_MAX;
    for (int r = start_rank + 1; r < csa.total; ++r) {
        if (csa.lcp[r] < matchlen) matchlen = csa.lcp[r];
        if (matchlen < MIN_MATCH) break;
        consider(csa.sa[r], matchlen);
    }
    matchlen = INT_MAX;
    for (int r = start_rank; r > 0; --r) {
        if (csa.lcp[r] < matchlen) matchlen = csa.lcp[r];
        if (matchlen < MIN_MATCH) break;
        consider(csa.sa[r - 1], matchlen);
    }
    return m;
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

int compress(const u8* src, int N, u8* dst, Format fmt) {
    if (N == 0) { dst[0] = 0xFF; return 1; }
    const bool alt_backrefs = (fmt == Format::KIRBY);

    std::vector<int> rle1, rle2, rleinc;
    precompute_rle1(src, N, rle1);
    precompute_rle2(src, N, rle2);
    precompute_rleinc(src, N, rleinc);

    ConcatSA3 csa;
    csa.build(src, N);

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
            // On-demand match-find: one SA-neighbor walk picks up best LZ +
            // bit-reverse + reverse-order backrefs in a single pass. The two
            // alt-backrefs are only relaxed when the format supports them.
            AltMatches m = find_alt_matches(csa, N, t);
            if (m.lz_len > 0) try_compressed2(t, m.lz_len, m.lz_len, 2, T_LZ, m.lz_addr);
            if (alt_backrefs) {
                if (m.bitrev_len > 0) try_compressed2(t, m.bitrev_len, m.bitrev_len, 2, T_LZBR,  m.bitrev_addr);
                if (m.rev_len > 0)    try_compressed2(t, m.rev_len,    m.rev_len,    2, T_LZREV, m.rev_addr);
            }
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

// --- Decompressor ---------------------------------------------------------

int decompress(const u8* src, int srclen, u8* dst, Format fmt) {
    const bool alt_backrefs = (fmt == Format::KIRBY);
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
        if (!alt_backrefs && (cmd == 5 || cmd == 6)) return -1;
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

} // namespace Retrocompress

// TODO for true O(n) overall:
//   - Sparse-table RMQ over the 3N concat is O(n log n) preprocessing; swap
//     for Bender-Farach-Colton to get true O(n).
//   - Active-set predecessor/successor uses std::set (O(log n) per query);
//     swap for union-find on a sorted-by-rank doubly-linked list for O(α(n)).
