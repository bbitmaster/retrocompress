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

## INLINE call sites — resolved via the same-bank rule

Empirically, **Kirby's inline JSR sites always have their source data in
the same PRG bank as the calling code.** This is the "code-and-data
co-located" pattern: when control enters bank N, both R6 (code at $8000)
and R7 (data at $A000) are set to bank N. The inline call then reads
compressed data from R7 = same bank as the code currently executing.

All 13 inline call sites resolve cleanly with this rule:

| JSR offset | bank | src CPU | file_off | dec_sz | csz |
|---|---|---|---|---|---|
| 0x5C655 | 46 | $A691 | 0x5C6A1 | 1024 | 301 |
| 0x5C67D | 46 | $A7BE | 0x5C7CE | 1024 | 429 |
| 0x6C7B4 | 54 | $A85E | 0x6C86E | 1024 | 559 |
| 0x6CDD0 | 54 | $B10B | 0x6D11B | 3072 | 713 |
| 0x7656E | 59 | $A881 | 0x76891 | 1024 | 320 |
| 0x765C5 | 59 | $A9C1 | 0x769D1 | 1024 | 446 |
| 0x77360 | 59 | $BA82 | 0x77A92 | 1024 | 332 |
| 0x773B1 | 59 | $BBCE | 0x77BDE | 2048 | 853 |
| 0x781E9 | 60 | $A2C2 | 0x782D2 | 1024 | 316 |
| 0x78204 | 60 | $A7C9 | 0x787D9 | 2048 | 515 |
| 0x7823B | 60 | $A50B | 0x7851B | 1024 | 396 |
| 0x7A3AD | 61 | $AAE3 | 0x7AAF3 | 1024 | 106 |
| 0x7A3DC | 61 | $AB4D | 0x7AB5D | 1024 | 568 |

Total: 17,408 bytes decompressed from 5,854 bytes compressed
(ratio 0.336). All blobs are 1024 / 2048 / 3072 byte multiples — exactly
matches CHR tile-page sizes (1 page = 1024 bytes = 64 8×8 tiles).

## Final inventory

| Source | Count | Compressed bytes | Decompressed bytes |
|---|---|---|---|
| TCRF-documented maps + tilesets | 361 | 135,561 | 288,246 |
| TABLE_Y_2 `$AC28/$AC30` (bank 52) | 8 | 3,692 | 9,216 |
| TABLE_Y_2 `$B531/$B55E` (bank 19, Y=1..45) | 45 | ~1,354 | ~5,756 |
| INLINE same-bank | 13 | 5,854 | 17,408 |
| **Total** | **~427** | **~146,461** | **~320,626** |

So Kirby's Adventure has roughly **427 compressed blobs totaling ~146 KB
compressed / ~320 KB decompressed** — about 19% of the 786 KB ROM is
compressed data.

## Still pending

- **5 COMPLEX call sites** — these all route through the map/asset loader
  at `$E640` or one of its variants. They cover the 361 already-documented
  blobs via the parallel-array pointer tables. Already inventoried via
  `pointer_walker_kirby_nes`.
- **Repacker** — once all blobs are inventoried (DONE), write a tool that
  recompresses each, places them back (respecting MMC3 bank boundaries),
  and updates the corresponding pointer-table entries and inline immediates.

## Map blob payload structure (after decompression) — VERIFIED

Verified by `tools/dump_map.py` + disassembly of the column-pointer
routine `$1F:EBAB` and column-fill loop `$09:8190`, plus direct
inspection of the byte values (row-major signature: a uniform ground
row + brick checker row + uniform dirt row at the bottom of every
screen).

Decompressed payload (typical size 986 bytes for a 4-screen map):

```
Offset  0..217   (218 bytes)  : HEADER  (padded to land screen data at WRAM $68C8)
Offset  218..end (N*192 bytes): N physical screens, each 16 cols x 12 rows ROW-MAJOR
```

### Header fields

| Byte | Meaning |
|---|---|
| `[0]`     | Sequence length — number of scroll-slots the player walks through |
| `[1..6]`  | Misc metadata; `[5]` ≈ start position (needs more verification) |
| `[7]`     | Screen height in metatile rows (`$0C` = 12 for horizontal stages) |
| `[8..N]`  | Sequence table: physical screen IDs in play order. Map 43 has `00 01 02 03` (linear); allows reuse / non-linear arrangements |
| `[26..217]` | Padding so screen data starts at WRAM `$68C8` (`$67EE + $DA`). Engine hardcodes this via base-pointer tables at file `$7ED55`/`$7ED65` |

### Per-screen format

Each screen is **192 bytes = 16 metatile-cols × 12 metatile-rows, ROW-MAJOR**:

```
screen[col, row] = decomp[218 + screen_id*192 + row*16 + col]
```

Width 16 metatiles = 256 px = one NES nametable width. Height 12
metatiles = 192 px = playfield area below the 16 px HUD. Adjacent
screens stitch seamlessly in sequence order — ground line, walls and
patterns line up across screen boundaries because the data IS the
contiguous level, just chopped into 16-wide blocks.

### Engine indexing (`$1F:EBAB` and `$09:8190`)

```
EBAB  LDY $67F6,X          ; Y = screen_id = sequence_table[X] (X = scroll slot)
EBAE  AND #$F0             ; A from caller = row*16 (low nibble is sub-row scroll)
EBB0  CLC
EBB1  ADC $ED55,Y          ; ptr_lo = base_lo[screen_id] + row*16
EBB4  STA $16              ; src_lo
EBB6  LDA $ED65,Y
EBB9  ADC #$00
EBBB  STA $17              ; src_hi
EBBD  RTS
```

Then `$09:8190` reads 16 contiguous bytes via `LDA ($16),Y` for Y=0..15
— that's one full row of 16 cols. The "16 metatiles" the loop consumes
per pass is a ROW, not a column. (Earlier sessions misread this as
column-major; the byte values clinched it: row 9 of screen 0 in map
43 is all `$58`, row 10 is the brick checker `$30/$20`, row 11 is all
`$28` dirt — uniform horizontal lines, the unmistakable signature of
row-major.)

Base-pointer tables for screen-id → WRAM offset live at:

| Table | CPU | File |
|---|---|---|
| `BASE_LO[screen_id]` | `$ED55` | `$7ED55` |
| `BASE_HI[screen_id]` | `$ED65` | `$7ED65` |

16 entries, spacing `$C0` = 192 bytes/screen.

### Map → tileset lookup

`MAP_TILESET_TBL @ file $24628` (bank 18 = R6, CPU `$8618`). 327 bytes,
one byte per map. Map 0 → tileset 21 (Vegetable Valley world map).
Map 43 → tileset 7 (Vegetable Valley Stage 1-1).

### Tileset payload (1344 bytes)

```
0..1023     : 256 metatiles × 4 tile indices (TL, TR, BL, BR)
              unpacked to RAM $7A00 (TL), $7B00 (TR), $7C00 (BL), $7D00 (BR)
1024..1087  : 64 bytes of packed 2-bit palette indices, MSB-FIRST
              (mt 0 = bits 6-7, mt 1 = bits 4-5, mt 2 = bits 2-3, mt 3 = bits 0-1).
              Unpacker at $1C:AD2C uses LSR chains of 6,4,2,0.
              Unpacked to $7E00.
1088..1343  : 256 bytes of metatile collision / property flags
```

### Tooling

- `tools/dump_map.py` — decompress any of the 327 maps (`--map N` or
  `--bank/--addr`).
- `tools/dump_tileset.py` — decompress any tileset, look up
  tileset-for-map.
- `tools/render_map_from_rom.py` — full ROM-only renderer using the
  format above; per-tileset CHR/palette defaults captured from FCEUX
  traces.
- `tools/render_first_room.py` — trace-driven render that serves as
  ground-truth reference.

## JSR $C43A call site summary

| Kind | Count | Coverage |
|---|---|---|
| COMPLEX (map/tileset loader path) | 5 | 361 documented map+tileset blobs |
| TABLE_Y_2 ($AC28/$AC30) | 1 | 8 new blobs (this doc) |
| TABLE_Y_2 ($B531/$B55E) | 1 | TBD (probably ~8 more) |
| INLINE single-shot | 13 | TBD per blob — bank resolution needed |
| **Total potential blobs** | **20** | **~390+ unique compressed sources** |
