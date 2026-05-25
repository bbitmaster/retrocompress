# Kirby's Adventure (NES) — compresch recompression experiment

ROM: `Kirby's Adventure (U).nes`
SHA-1: `e099d688760ff0ce114ca8a9fd083e31e41cfade` (786,448 bytes; 16B iNES + 512KB PRG + 256KB CHR; mapper 4 / MMC3)

Tool: Two scanners build on disch's `Compresch_Kirby` (2008/2013) library:
- `scanner` — brute-force walks every offset, tries a bounds-checked decompress
- `pointer_walker` — reads the documented pointer tables and decompresses only
  the real referenced blocks (the authoritative measurement)

## Pointer tables (per TCRF datacrystal ROM map)

| Table | Entries | Bank table | Hi table | Lo table |
|---|---|---|---|---|
| Maps | 0x147 (327) | $244E1 | $2476F | $248B6 |
| Tilesets | 0x031 (49) | $249FD | $24A2E | $24A5F |
| Enemies | 0x147 (327) | $24A90 | $24BD7 | $24D1E |

Pointer format: 3 parallel tables (bank, addr-hi, addr-lo). CPU addresses are
$A000-$BFFF. File offset = `0x10 + (bank & 0x7F) * 0x2000 + (addr - 0xA000)`.
Bit 7 of the bank byte appears to be an engine flag, not part of the bank
number (53 map entries have it set; bank&0x7F always lands on a valid block).

The "enemy" table doesn't point to compressed data — those are raw enemy-spawn
records (~6 bytes each: type, count, x, y, ...). 15 of 49 tileset entries are
zeroed-out unused slots.

## Authoritative results (`pointer_walker`)

| Block class | Entries | Valid | Skipped | Decompressed | Original csz | Compresch | Saved | % |
|---|---|---|---|---|---|---|---|---|
| Maps | 327 | 327 | 0 | 242,550 | 107,195 | 102,495 | +4,700 | **4.38%** |
| Tilesets | 49 | 34 | 15 (unused) | 45,696 | 28,366 | 27,654 | +712 | **2.51%** |
| **Combined** | **376** | **361** | **15** | **288,246** | **135,561** | **130,149** | **+5,412** | **3.99%** |

- **Zero roundtrip failures** across all 361 blocks
- **Zero blocks made worse** by compresch
- Net: compresch consistently matches or beats Nintendo's original encoder on every block, by ~4% overall

## Highlights from the per-block run

Best per-block wins live in the maps table. The biggest absolute savings:

| Map | Bank/addr | File off | Orig csz | New csz | Decomp | Saved | Ratio |
|---|---|---|---|---|---|---|---|
| (varies) | 0x07/B6A0 | 0xF6B0 | 617 | 613 | 986 | +4 | 0.994 |
| ... | ... | ... | ... | ... | ... | ... | ... |

(Brute-force scanner found bigger wins at offsets like 0xB6AD0 where 241 bytes →
12 bytes — but those were outside the documented map/tileset pointer tables, so
either they're inside other unscanned tables, in CHR ROM unrelated, or false
positives.)

## False-positive rate of the brute-force scanner

For reference, the original brute-force scanner found 1479 "valid" candidate
blocks across the whole ROM, claiming 13.4% savings. The pointer-walker shows
only **361 real compressed blocks**, so:

- ~76% of brute-force "hits" were false positives (random data that happened
  to decompress validly)
- The 13.4% inflated savings came mostly from re-encoding garbage that was
  never compressed in the original
- The honest figure is **~4%**

The lesson: roundtrip-validation by itself is *not* enough to distinguish real
compressed blocks from coincidental valid-looking byte sequences. Pointer-table
ground truth was essential.

## Caveats / next steps

- **Other compressed tables we haven't found yet.** TCRF only documents map +
  tileset + enemy tables. Kirby may have additional compressed data (title
  screens, intermission graphics, sprite tiles) referenced by tables that
  aren't on the wiki. Those would add to the savings total.
- **Reinsertion not done.** To produce an actually-smaller ROM, blocks must be
  recompressed in place AND the pointer tables updated. That's mechanical
  work but requires being careful about bank boundaries (a smaller block
  might still need to stay within the same 8KB bank to keep the bank byte
  valid).

## optkirby: provably-optimal DP encoder

We built a true optimal-parsing encoder using DP shortest-path: at each output
position i, only forward "shortcut" edges from i are emitted; intermediate
lengths fall through to raw-extend. Block costs in the Kirby format are
almost-constant in length within a header regime (1B header for len≤32, 2B for
33..1024), so only the max length per type at each i is interesting. See
`optkirby/optkirby.cpp`.

### Result: optimal vs compresch on the documented Kirby blocks

| Encoder | Total bytes | Saved vs original | % |
|---|---|---|---|
| Nintendo (ROM) | 135,561 | — | — |
| compresch (greedy/tree-prune) | 130,149 | +5,412 | 3.99% |
| **optkirby (DP-optimal)** | **130,142** | **+5,419** | **4.00%** |

| optkirby vs compresch | wins | losses | ties | opt worse than original |
|---|---|---|---|---|
| 361 blocks | **4** | **0** | **357** | **0** |

**Compresch is essentially optimal** on this format. The DP-optimal encoder ties on 357/361 blocks (98.9%) and only beats compresch by 7 bytes total across 4 blocks:

| Block | File offset | compresch | optkirby | Saved |
|---|---|---|---|---|
| Map 285 | 0x16A57 | 417 | 413 | -4 |
| Map 292 | 0x21301 | 92 | 91 | -1 |
| Map 306 | 0xECD2 | 614 | 613 | -1 |
| Tileset 6 | 0x4C88 | 1008 | 1007 | -1 |

This is itself an interesting finding about the Kirby format: because each
block-type's cost is constant in length (within a header regime), the locally
greedy "always take the longest match" decision is almost always globally
optimal. The DP only finds wins where some clever sub-32 split lets the next
block start at a meaningfully better position.

### Speed comparison

| Encoder | Time to compress all 361 blocks | Total output |
|---|---|---|
| compresch (greedy tree-prune) | 488 ms | 130,149 |
| optkirby — naive O(n²) | 977 ms | 130,142 |
| optkirby — prefix-doubling SA + sliding window + constrained LPF | 218 ms | 130,142 |
| **optkirby — SA-IS + sliding window + constrained LPF** | **123 ms** | **130,142** |

The optimized optkirby is **4× faster than compresch** while producing
strictly-better output. Identical compressed output across all three optkirby
versions confirms the optimizations preserved correctness.

### Asymptotic complexity (final optkirby)

- SA construction (LZ-copy, LZ-bitrev source, LZ-rev source): **O(n) via SA-IS**
- Kasai LCP: O(n)
- LPF via sparse-table RMQ: O(n log n) preprocess + O(1) query — could go true
  O(n) with Bender-Farach-Colton RMQ
- Constrained LPF for LZ-bitrev / LZ-rev: O(n log n) via `std::set` keyed on
  SA-rank — could go true O(n α(n)) with union-find on a sorted-by-rank linked
  list of active sources
- Sliding-window-min for raw blocks: O(n)
- DP shortest path: O(n) (each position emits ≤ 12 outgoing edges)

Overall: **O(n log n)** in this build, dominated by sparse-table RMQ and
`std::set`. SA-IS removed the previous bottleneck (SA construction).

## Reproducing

```
cd ~/claude_code_dir/retro_compression/scanner
make
./pointer_walker ../reference/rom/kirby.nes              # authoritative
./pointer_walker ../reference/rom/kirby.nes -v           # per-block detail
./scanner ../reference/rom/kirby.nes                     # brute-force
./scanner ../reference/rom/kirby.nes --start 0 --end 0x80000   # PRG only
```
