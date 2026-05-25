# retrocompress: notes, algorithm, and findings

Algorithmic ideas, design choices, and takeaways from building the
retrocompress engine.

## Project at a glance

retrocompress is a provably-optimal compressor for the 3-bit-command /
5-bit-length compression format family used by many 4th- and 5th-generation
Nintendo and third-party titles. The format is documented in Parasyte 2004.

Result on Kirby's Adventure (NES, 361 pointer-validated compressed blocks):
- 130,142 bytes encoded; 4.00% smaller than the original ROM.
- O(n) asymptotic complexity (SA-IS + sliding-window-min + constrained LPF).
- 4× faster than disch's compresch reference encoder.
- 7 bytes smaller than compresch (compresch isn't quite optimal — see
  "the 7-byte mystery" below).

## The format (one-paragraph recap)

Seven block types: raw, RLE, 2-byte RLE, RLE++ (incrementing), LZ-copy,
bit-reversing LZ-copy, LZ-reverse-copy. Length 1..32 encodes as 1-byte
header; length 33..1024 encodes as 2-byte "extension" header. Stream
terminates on `0xFF`. Payload after the header: 0 bytes for nothing,
1 byte (RLE / RLE++), 2 bytes (2-byte RLE pair, or LZ address), or
N bytes (raw). LZ addresses are absolute offsets into the output buffer
being decompressed.

Super Metroid uses the same skeleton with different LZ variants (commands
4–7 are absolute/relative × EOR-FF or not).

## The DP-shortest-path framing

The core algorithmic insight: optimal parsing of LZ-style compression is
**shortest path through a DAG** where:
- Nodes are output positions `0..N`.
- Edges are "if I emit a block of type T length L starting at i, I arrive
  at i+L (or i+2L for type 2) and pay cost = header_size(L) + payload(T)".
- Each position emits at most O(1) outgoing edges per compressed type
  (only the max-length-per-header-regime per type matters; intermediate
  lengths are dominated within a regime since payload size is constant
  in L for the compressed types).
- Raw blocks are the only type whose cost depends on L; those need
  special handling.

At each position i, we relax edges going *out* of i (to i+L) rather than
searching backward from i for edges coming in. Both directions give the
same answer, but the forward formulation makes the constant-time per-edge
nature obvious — and with linear-time match-finding preprocessing, gets
to true O(n).

## Why this is provably optimal

Each output position has a single best cost (no state beyond position).
Costs are additive over the path. The graph is a DAG (edges always go
forward). The DP relaxation finds the minimum-cost path, by standard
shortest-path-on-DAG correctness.

This is the textbook approach (Storer-Szymanski, "Data Compression via
Textual Substitution", JACM 1982) applied to this specific block-type
catalog.

## Making it actually O(n)

Three things have to be linear-time for the whole thing to be O(n):

### 1. Raw block edges — sliding-window min

At each i, raw blocks emit edges to i+L for L=1..1024. Naively O(n*1024).
Trick: best raw arrival at position t is
`t + min(min over j in [t-32, t-1] of (best[j] - j) + 1,
        min over j in [t-1024, t-33] of (best[j] - j) + 2)`.
Maintain two monotonic-deque sliding-window minimums over `f(j) = best[j] - j`.
Each j is pushed once and evicted once: O(n) amortized.

### 2. LZ-copy match-finding — SA-IS + Kasai LCP + LPF

We need, for each i, the longest k such that some j < i has
`s[j..j+k-1] = s[i..i+k-1]`. This is the **Longest Previous Factor (LPF)**
problem.

Standard O(n) recipe:
- Build the suffix array via SA-IS (Nong, Zhang, Chan 2009) — true O(n).
- Compute LCP array via Kasai (O(n)).
- Compute LPF via stack-sweep over SA (O(n) given SA + LCP + O(1) RMQ).

Our implementation uses sparse-table RMQ which is O(n log n) preprocess
for O(1) query; that's the only sub-O(n) thing in the pipeline. Could
swap for Bender-Farach-Colton for true O(n) but the log n factor is
sub-millisecond on typical block sizes.

### 3. LZ-bitrev / LZ-rev — constrained LPF via concatenated SA

These variants need cross-string matches with position constraints:
- LZ-bitrev: for each i in s, longest k such that `s[i..i+k-1] =
  bitrev(s[j..j+k-1])` for some j < i.
- LZ-rev: same but `s[i..i+k-1] = (s[addr], s[addr-1], ..., s[addr-k+1])`
  for addr in [k-1, i-1].

Approach: build SA of the concatenation `t = source + sep + s` (where
`source` is the appropriately-transformed string — bit-reversed or
reversed). For each target position in s, find the best match against
positions in the source portion using "constrained LPF":
- Process targets in i ascending order.
- Maintain a `std::set<sa_rank>` of currently-active source positions.
- At target i, query predecessor + successor in SA-rank order; the best
  LCP between target and any active source is at one of those two
  (LCP queries via sparse-table RMQ).
- After query, activate the new sources that become eligible at i+1.

This is O(n log n) with std::set; true O(n) would replace set with
union-find on a sorted-by-rank doubly-linked list of "next active".

## Result (Kirby's Adventure NES, 361 pointer-validated blocks)

| Encoder | Output | vs original ROM |
|---|---|---|
| Nintendo (original ROM) | 135,561 | — |
| compresch (disch 2008/2013, greedy/tree-prune) | 130,149 | -3.99% |
| **retrocompress (DP-shortest-path, SA-IS)** | **130,142** | **-4.00%** |

Savings versus Nintendo's encoder are modest (4%) — Nintendo's encoder is
already pretty good, and the format's cost structure (per-block payload
is constant in L within a header regime) means even locally-greedy choices
are usually globally optimal.

## The 7-byte mystery and what compresch was actually doing

The 4-block, 7-byte gap between retrocompress and compresch was at first
attributed to "compresch is a heuristic, retrocompress is optimal — this
is the gap." That turned out to be wrong.

Reading compresch's source revealed its algorithm is not greedy at all.
It's a **dominance-pruned DP** over partial chains:

1. Generate all candidate compressed blocks of each type (the maximal
   runs / matches).
2. `KillPointlessBlocks`: drop candidate blocks that are subsumed by
   other candidates with smaller body sizes (a candidate-level filter).
3. `CrunchTree::Crunch`: walk the candidate list in start-position order.
   Maintain a forest of "chains" (sequences of compressed blocks) ending
   at various positions. For each candidate block, fork each active chain
   with the option of attaching this block. Periodically prune chains
   that are "dominated" by others.
4. After processing all candidates and padding to end-of-data, select
   the cheapest surviving chain.

This is the right framework. It's **structurally equivalent to plain
DP-shortest-path** for this problem — both find the optimum on the same
DAG. The difference is bookkeeping: plain DP collapses everything into
`best[i]`; compresch maintains a forest of partial solutions.

The pruning rules are dominance tests: "drop chain B if some chain A
provably can't be caught by B no matter what comes next." Two rules:

- **Case 1** (in `PadAndCleanChains`): two chains end at the same
  position with different costs; keep the cheaper. **Sound** — same
  position means downstream futures are identical, lower cost wins.
- **Case 2** (in `PadAndCleanChains`): two chains share a `padstop`
  (after raw-padding to current scan position) but end at *different*
  positions; if one is at least `blocksize+1` bytes cheaper, prune the
  other. **Unsound.** The "more expensive" chain often ends at an
  earlier real position and can still attach a block starting at a spot
  the cheaper chain has padded past. That alignment opportunity is
  exactly where the optimal solution hides.

`KillPointlessBlocks` is similarly over-aggressive: it removes candidate
blocks that another block "subsumes" with smaller body size, but a
"subsumed" block can still be the *start* of a chain that has no
counterpart in the chain starting from the dominating block.

Experimentally confirmed: with `KillPointlessBlocks` disabled and case 2
of `PadAndCleanChains` disabled (case 1 kept), compresch produces
byte-identical output to retrocompress across all 361 blocks. So the
framework is correct; the gap is in the pruning rules.

## When is this "competitive chain DP" pattern actually useful?

For Kirby compression specifically, the chain forest is overkill — the
state is just "position in output", costs are additive, plain `best[i]`
DP covers everything. But the pattern *is* the right tool for harder
problems where DP state can't collapse to a single index:

**1. Multi-dimensional DP state.** When cost-to-reach depends on more
than just position. Examples:
- Sequence alignment with affine gap penalties (Smith-Waterman) — 3
  parallel DP arrays for "in match" / "in gap-A" / "in gap-B" is
  effectively three competing chains per position.
- Adaptive arithmetic coding — each block updates the encoder model
  state, which affects future block costs. The plain `best[i]` doesn't
  capture the model state.
- Robot motion planning with kinematic constraints — state is
  (position, velocity, fuel).

**2. Multi-objective / Pareto-frontier optimization.** When "better"
isn't a single number.
- Knapsack with multiple constraints (weight + volume + cost).
- Network routing with latency + reliability + cost.
- Compiler instruction scheduling (minimize cycles subject to register
  pressure).

**3. Mode-switching cost models.** When the cost of the next operation
depends on what the previous one was.
- Hardware-aware compression (decode-speed-aware, cache-friendly block
  ordering).
- Algorithms with "warm-up" costs that amortize across runs of same-type
  operations.

**4. Interactive / online optimization.** When you want to answer
"what's the best solution containing X?" or update incrementally as new
data arrives. Plain DP throws away the alternatives; the chain forest
retains them.

**5. Heuristic-bounded search.** When optimality is too expensive and
you want a tunable speed/quality trade-off via aggressive pruning. Beam
search, A*, branch-and-bound — these are all the same shape as the
chain-forest framework, just with different pruning criteria.

The general principle: when DP state is **structured** (more than just a
position index) or the cost function has **memory** beyond what `best[i]`
captures, dominance-pruned chain forests are the right shape. Plain DP
is for the special case where state and cost both collapse to the
simplest possible thing.

## Naming the pattern

There isn't a standard textbook name for this exact pattern. Possible
labels:
- "competing chains DP" — informal, captures the flavor
- "dominance-pruned DP" — descriptive
- "Pareto-frontier DP" — emphasizes the optimality criterion
- "branch-and-bound with dominance pruning over partial DAG paths" —
  formal but wordy

It's closest in spirit to **branch-and-bound**, but the search direction
is forward (DAG topological order) rather than depth-first or best-first.

## The format family

The 3-bit-command + 5-bit-length-with-extension structure is essentially
the standard tile compression of the 4th–5th gen era. Same skeleton,
different codes-to-block-type mappings, sometimes different block types
added or removed. Games known to use a variant:

- Super Mario World
- Super Metroid
- Castlevania IV
- The Legend of Zelda: A Link to the Past
- Pokemon Gold / Silver
- Kirby's Adventure (NES) / Kirby Super Star (SNES)
- many other Capcom and Konami SNES titles using the "Konami block" variant

The retrocompress DP engine is format-agnostic: the match-finding
(SA-IS / LPF / sliding-window-min) and the DP loop don't care which
3-bit codes mean what — they only need a list of
`(block_type, payload_size, max_length, valid_lengths_at_position_i)`
callbacks. Each new game needs a small adapter for its specific LZ
sub-types and command codes.

## Code map

```
retrocompress/
├── README.md
├── Makefile
├── docs/
│   ├── NOTES.md           — this document
│   └── KIRBY_NES_RESULTS.md
├── src/
│   ├── retrocompress.{h,cpp}     — core: SA-IS, LPF, DP, decoders
│   ├── walker_kirby_nes.cpp      — Kirby NES analysis
│   ├── walker_super_metroid.cpp  — Super Metroid analysis (tilesets)
│   ├── test_basic.cpp            — unit tests
│   ├── scanner.cpp               — brute-force ROM scanner
│   ├── bench.cpp                 — encode-speed benchmark
│   └── diff_one.cpp              — side-by-side token diff
└── third_party/compresch/        — vendored disch's compresch (Artistic 2.0)
```

The 2013 experiment is preserved on branch `original-2013` and tag
`pre-retrocompress-2013`.

## Open threads

- **Full O(n)**: swap sparse-table RMQ for Bender-Farach-Colton, swap
  `std::set` for union-find-on-sorted-linked-list. Marginal practical
  gain on typical block sizes; completes the asymptotic story.
- **Reinsertion**: turn savings into actually-shorter ROMs. Requires
  pointer-table updating respecting bank boundaries.
- **Super Metroid encoder**: the decoder works; the optimal encoder
  needs SM-specific LZ match-finders for the EOR variants and 1-byte
  relative distances.
- **More games**: walkers for SMW, LttP, Castlevania IV, Pokemon
  Gold/Silver.
- **Patch compresch**: removing `KillPointlessBlocks()` and case 2 of
  `PadAndCleanChains` makes it produce identical output to retrocompress.
