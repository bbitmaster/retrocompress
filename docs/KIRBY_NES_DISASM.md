# Kirby's Adventure (NES) — disassembly notes for compression data flow

Manual disassembly findings, focused on identifying every compressed-data
source and its pointer table. Built incrementally with `tools/disasm.py`
(minimal 6502 disassembler).

## Memory map / mapper

- iNES mapper 4 (**MMC3**), 512 KB PRG, 256 KB CHR
- PRG bank slots:
  - `$8000-$9FFF` = R6 (swappable)
  - `$A000-$BFFF` = R7 (swappable)
  - `$C000-$DFFF` = fixed to second-to-last bank (bank **62**)
  - `$E000-$FFFF` = fixed to last bank (bank **63**)

## Key routines (in fixed banks)

| CPU addr | File offset | What it does |
|---|---|---|
| `$C43A` | `0x7C44A` | **Decompressor.** Reads compressed bytes via `($16),Y`, writes via `($18),Y`. |
| `$F052` | `0x7F062` | `BankSet_R7_A`: switches R7 to bank in A. Writes $87 to $8000 then A to $8001. |
| `$F061` | `0x7F071` | Like F052 but uses bank value from RAM `$0576`. |
| `$C067` | `0x7C077` | VBlank wait (with two entry points at $C082 and $C086). |
| `$C0BE` | `0x7C0CE` | Pops 2 bytes from stack into `($16/$17)` — restores caller's src pointer. |

## Decompressor parameters

- `$16/$17` = source CPU address (16-bit, little-endian)
- `$18/$19` = destination CPU address (16-bit, little-endian)
- R7 ($A000-$BFFF) must be mapped to the bank containing the source data
  before the JSR. The decompressor doesn't switch banks itself.
- Common destinations: `$67EE`, `$68C8`, `$74C8` — all in banked WRAM
  (`$6000-$7FFF`).

## Map / tileset loader at `$E640`

The main asset loader in the fixed `$E000-$FFFF` bank. Decoded:

```
$E640:                       ; (entry from interrupt or boot?)
       SEI / .byte $A7 / JSR $F03B / JMP $F061

$E648:                       ; entry: "load fixed asset from bank #$13"
       LDA #$13 / JSR $F052  ; R7 = bank 19
       JMP $A000             ; transfer control into bank 19

$E650:                       ; entry: "load asset via category table"
       LDA #$38 / JSR $F052  ; R7 = bank 56 (where the pointer tables live)
       JSR $ACCA             ; helper (some setup)
       LDX $055E             ; X = asset index (low byte; 0..255)
       LDA $055F             ; "category" select
       BNE $E65D             ; nonzero -> category 2

       ; Category 1 (X is the direct index)
       LDA $88A6,X / PHA     ; lo from $88A6  (file 0x248B6)
       LDY $875F,X           ; hi from $875F  (file 0x2476F)
       LDA $84D1,X           ; bank from $84D1 (file 0x244E1)
       JMP $E667             ; -> common path

$E65D:                       ; Category 2 (X is the index minus 0x100)
       LDA $89A6,X / PHA     ; lo from $89A6
       LDY $885F,X           ; hi from $885F
       LDA $85D1,X           ; bank from $85D1

$E667:                       ; common path
       AND #$7F              ; mask top bit (engine flag)
       JSR $F052             ; R7 = bank from table
       PLA / STA $16 / STY $17   ; src = (hi,lo)
       LDA #$EE / STA $18 / LDA #$67 / STA $19   ; dst = $67EE
       JSR $C43A             ; decompress
       ; ... return path
```

### What "Category 2" actually is

It looks like a second table set, but the math says it's just an **8-bit-X
workaround for the single 327-entry table**. Each cat-2 address is exactly
0x100 higher than its cat-1 counterpart:

| Table | Cat 1 (CPU) | Cat 1 (file) | Cat 2 (CPU) | Cat 2 (file) |
|---|---|---|---|---|
| Bank | $84D1 | 0x244E1 | $85D1 | 0x245E1 |
| Hi | $875F | 0x2476F | $885F | 0x2486F |
| Lo | $88A6 | 0x248B6 | $89A6 | 0x249B6 |

The cat-1 bank table is 327 bytes (`$84D1..$8617`); the cat-2 bank table
starts at `$85D1` which is just `$84D1 + 0x100`. So cat 2's "index 0" equals
cat 1's "index 256" — it's the same physical table, viewed at offset +0x100
to access entries 256..326 when X can only carry 0..255.

So this loader covers exactly the 327 documented map entries. No new
compressed data hides here.

## Newly discovered: TABLE_Y_2 at `$AC28/$AC30`

Called from file 0x68BF0 (in PRG bank 52). 2-byte pointers (lo, hi)
without a bank byte — R7 is assumed to already be set to bank 52
(the same bank as the calling code).

Decoded with R7 = bank 52:

| Idx | CPU addr | File offset | Decomp size | Ratio |
|---|---|---|---|---|
| 0 | $AD0C | 0x68D1C | 1024 | 0.508 |
| 1 | $AF14 | 0x68F24 | 1024 | 0.438 |
| 2 | $B0D4 | 0x690E4 | 1024 | 0.488 |
| 3 | $B2C8 | 0x692D8 | 1024 | 0.422 |
| 4 | $B478 | 0x69488 | 1024 | 0.531 |
| 5 | $B698 | 0x696A8 | 1024 | 0.415 |
| 6 | $B841 | 0x69851 | 1024 | 0.414 |
| 7 | $B9E9 | 0x699F9 | 2048 | 0.390 |

**8 new compressed blobs** — likely CHR tile data (1024 bytes = 64 tiles).
All decompress cleanly and would round-trip through retrocompress.

## Newly discovered: TABLE_Y_2 at `$B531/$B55E`

Called from file 0x26FE8 (in PRG bank 19). 2-byte pointers (lo, hi). With
R7 also = bank 19 (calling code's bank), the table walks cleanly for
**45 entries** starting at Y=1. Entry Y=0 (`$CC74`) appears to be a
sentinel or special case — it points at fixed bank 62 and doesn't
decompress as a normal blob.

Table location: lo at file 0x27541, hi at file 0x2756E.

| Y range | Decomp size | Notes |
|---|---|---|
| 0 | (sentinel `$CC74`) | doesn't decompress |
| 1 | 256 | header/master blob? |
| 2..45 | 125 each | 44 tiny blobs (~16 tiles each, small graphics) |

Total: **45 valid blobs**, ~2750 bytes decompressed, ~1354 bytes compressed
(ratio ~0.49). Likely animated-tile sequences or font glyphs (125 bytes ≈
16 tiles).

The pattern of `LDA $B531,Y / LDA $B55E,Y` with Y as index, where each blob
is ~125 bytes — that's consistent with font characters or HUD elements
loaded one-at-a-time.

## Inventory updated

| Source | Count | Status |
|---|---|---|
| TCRF-documented maps + tilesets | 361 | Inventoried, walkable via pointer_walker_kirby_nes |
| TABLE_Y_2 `$AC28/$AC30` (bank 52) | 8 | NEW — walked successfully |
| TABLE_Y_2 `$B531/$B55E` (bank 19) | 45 | NEW — walked successfully |
| INLINE single-shot calls | 13 | NEW — bank resolution still needed |
| **Total identified compressed blobs** | **~427** | |

## Still pending

- **13 INLINE call sites** — bank is set by an ancestor caller. Requires
  cross-function call-graph tracing (find JSRs to each containing function,
  inspect each caller for `LDA #imm / JSR $F052` patterns).
- **Repacker** — once all blobs are inventoried, write a tool that
  recompresses each, places them back (respecting MMC3 bank boundaries),
  and updates the corresponding pointer-table entries and inline immediates.

## JSR $C43A call site summary

| Kind | Count | Coverage |
|---|---|---|
| COMPLEX (map/tileset loader path) | 5 | 361 documented map+tileset blobs |
| TABLE_Y_2 ($AC28/$AC30) | 1 | 8 new blobs (this doc) |
| TABLE_Y_2 ($B531/$B55E) | 1 | TBD (probably ~8 more) |
| INLINE single-shot | 13 | TBD per blob — bank resolution needed |
| **Total potential blobs** | **20** | **~390+ unique compressed sources** |
