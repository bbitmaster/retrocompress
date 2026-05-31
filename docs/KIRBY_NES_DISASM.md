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
| `[0]`     | Screens **horizontally** (W) — e.g. `$04` for stage 1 (4 screens wide), `$02` for the Vegetable Valley world map |
| `[1]`     | Screens **vertically** (H) — `$01` for purely horizontal stages, `$02` for the 2×2 world map (`$00` is treated as 1) |
| `[2]`     | CHR config index — indexes `$9418/$9518/$9618` tables to set R3/R4/R5 |
| `[3]`     | sub-config (sprite-palette setup at `$09:A6CA` reads it via `LDY $67F1`) |
| `[4]`     | R1 sprite CHR bank for stage maps (`map_idx >= 8`) — read at `$09:A600..A603` |
| `[5]`     | approx start position (needs more verification) |
| `[6]`     | misc |
| `[7]`     | Screen height override (`$0C` = 12 rows; `$00` falls back to default 12) |
| `[8..N]`  | Sequence table: `W*H` entries giving the physical screen ID at each grid slot. Layout row-major: `seq_idx = row_slot * W + col_slot`. Map 43 = `00 01 02 03` (4×1). Map 0 (overworld) = `00 01 02 03` (2×2). Allows screen reuse |
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

## Per-map CHR bank & palette setup

When a map loads, two distinct paths set up the 6 CHR bank registers
R0..R5 (zero-page shadow at `$0042..$0047`) and the 32-byte BG palette
(WRAM `$6000..$601F`).

### CHR bank resolution

| Reg | Source | Notes |
|---|---|---|
| R0 (`$42`) | `#$80` constant | Sprite CHR low. Effectively always `$80` for gameplay |
| R1 (`$43`) | **map < 8**: `#$D8` hardcoded at `$1C:A01B/A01D` (overworld/menu)<br>**map >= 8**: `header[4]` via `$09:A600..A603` (`LDA $67F2; STA $43`) | Sprite CHR mid |
| R2 (`$44`) | `#$00` (forced to 0 if map >= 7) at `$09:A6B4/A6B6` | BG CHR 1st 1KB |
| R3 (`$45`) | `R3_TBL[header[2]]` at `$09:A6B8` (`LDA $9418,Y` with Y=`$67F0`) | BG CHR 2nd 1KB |
| R4 (`$46`) | `R4_TBL[header[2]]` at `$09:A6BD` (`LDA $9518,Y`) | BG CHR 3rd 1KB |
| R5 (`$47`) | `R5_TBL[header[2]]` at `$09:A6C2` (`LDA $9618,Y`); animated runtime | BG CHR 4th 1KB |

The R3/R4/R5 tables live in **R6 = chunk `$05`** (set by `JSR $F02C` with
A=`$05` at `$09:A6A0`). File offsets:

| Table | CPU | File |
|---|---|---|
| `R3_TBL` (BG CHR mid) | `$9418` | `$B428` |
| `R4_TBL` (BG CHR mid-high) | `$9518` | `$B528` |
| `R5_TBL` (BG CHR high, animated) | `$9618` | `$B628` |

49+ entries each. Indexed by `header[2]` of the map. Verified entries:
`table[7]` = `(0E, 0F, 1C)` (stage 1 base, R5 animates `$1C`→`$1F`),
`table[40]` = `(F0, F1, FD)` (Vegetable Valley world map).

### Per-map palette pointer table

At `$09:A656..A66C` (stage map case):

```
A656  LDY $055E      ; Y = map index
A659  LDA $055F      ; sub-map flag
A65C  BNE $A66D      ; sub-map path -> $8B80/$8E0E/$8CC7 tables
A65E  LDA $8A80,Y    ; bank   = PAL_BANK_TBL[map]    -> $057D
A664  LDA $8D0E,Y    ; src_lo = PAL_PTR_LO_TBL[map]  -> $16
A667  LDX $8BC7,Y    ; src_hi = PAL_PTR_HI_TBL[map]  -> $17 (via $A669)
```

Tables live in R6 = chunk `$12` (set by some prior bank switch).
File offsets:

| Table | CPU | File |
|---|---|---|
| `PAL_BANK_TBL` (main map path) | `$8A80` | `$24A90` |
| `PAL_PTR_HI_TBL` | `$8BC7` | `$24BD7` |
| `PAL_PTR_LO_TBL` | `$8D0E` | `$24D1E` |
| `SUB_PAL_BANK_TBL` (sub-map path) | `$8B80` | (sub-map cases) |
| `SUB_PAL_PTR_HI_TBL` | `$8CC7` | |
| `SUB_PAL_PTR_LO_TBL` | `$8E0E` | |

For map 43: bank=`$11`, ptr=`$A63F`. Bytes at `$11:A63F` start with
`$04` (= 4 palette frames), then encoded payload (NOT a raw 32-byte
palette — there's an unpacker at `$1F:E684+` that interprets it and
writes the final 32 bytes into WRAM `$6000..$601F`).

Once unpacked, the BG palette gets written to PPU `$3F00` by the loop
at `$1F:C282..C2AC`:

```
C282  LDA #$3F / STA $2006
C287  LDA #$00 / STA $2006   ; PPU = $3F00
C28E  LDA $6000 / STA $2007  ; universal BG color (always written)
C294  LDA $6001,Y / STA $2007
C29A  LDA $6002,Y / STA $2007
C2A0  LDA $6003,Y / STA $2007
       INY * 4, CPY #$20, loop
```

### Practical render path (ROM-only)

For any stage map N (no trace needed for CHR setup):

1. Decompress map → read header bytes 2 and 4.
2. R0 = `$80`; R1 = (`$D8` if N < 8) else `header[4]`; R2 = `$00`.
3. R3 = rom[`$B428` + header[2]]; R4 = rom[`$B528` + header[2]]; R5 = rom[`$B628` + header[2]].
4. Palette: still requires implementing the unpacker (or use a captured per-tileset palette for known cases).

### Universal sprite palette block

In addition to BG, sprite palette bytes are written to `$0182..$019E`
via the routine following `STA $45/46/47` — these are the OAM `attr`
byte slots and get further refined by `STA $0197=$30; STA $0198=$37;
STA $0199=$17` etc. immediately after the R1 write. Not currently
relevant for static map rendering.

## JSR $C43A call site summary

| Kind | Count | Coverage |
|---|---|---|
| COMPLEX (map/tileset loader path) | 5 | 361 documented map+tileset blobs |
| TABLE_Y_2 ($AC28/$AC30) | 1 | 8 new blobs (this doc) |
| TABLE_Y_2 ($B531/$B55E) | 1 | TBD (probably ~8 more) |
| INLINE single-shot | 13 | TBD per blob — bank resolution needed |
| **Total potential blobs** | **20** | **~390+ unique compressed sources** |

## Sprite engine

Kirby's Adventure uses a per-character metasprite engine — each game
character / enemy / projectile is a list of hardware sprites at signed
(dx, dy) offsets from a single (X, Y) anchor position. OAM is
double-buffered at WRAM `$0200..$02FF` and `$0300..$03FF`, and gets
DMA'd to the PPU once per frame via `STA $4014` at `$1F:C360` (A
alternates between `$02` and `$03`).

### Metasprite definition format

```
metasprite:
    [ count : u8 ]
    [ dx : i8, dy : i8, tile : u8, attr : u8 ] * count
```

- `dx`, `dy` are signed offsets added to the character's anchor X / Y
  (zp `$6A`/`$6B` = X, `$6C`/`$6D` = Y).
- `tile` is the source tile index; the blit writes `tile XOR $01` to
  OAM byte 1 — this effectively pairs adjacent tiles (low bit toggles
  between halves of 8×16 sprite mode, or between sprite-pattern-table
  halves depending on PPU config).
- `attr` is the standard NES OAM byte 2: bit 7 V-flip, bit 6 H-flip,
  bit 5 BG-priority, bits 0-1 palette.

Sprites that would land off-screen vertically are hidden by writing
`Y = $F0` instead of the computed Y.

### Blit routine — `$1F:DA89`

```
DA89  STA $16            ; src_lo  (caller passes A=lo, X=hi)
DA8B  STX $17            ; src_hi
DA8D  LDY #$00
DA8F  LDX $68            ; OAM cursor (advances across multiple blit calls)
DA91  LDA ($16),Y        ; A = count
DA93  STA $00
DA95  INY
DA96  LDA $603D / LSR / BCS $DAEE   ; check OAM-page flag → write to $0200 or $0300

(per-entry loop at DA9C — verified against trace)
       LDA ($16),Y → ADC $6A → STA $0203,X    ; X position
       LDA ($16),Y → ADC $6C → STA $0200,X    ; Y position
       LDA ($16),Y → EOR #$01 → STA $0201,X   ; tile (XOR'd bit 0)
       LDA ($16),Y → STA $0202,X              ; attr
       INX ×4                                  ; next OAM slot
       DEC $00 / BNE                           ; loop count
```

A parallel copy at `$DAEE..$DB4F` mirrors the same code but writes to
`$0300,X` instead — that's the second OAM buffer.

### Dispatcher / animation lookup — `$1F:CD33`

Iterates over the character slots, computing the metasprite pointer
from the current animation frame and the per-character animation
table:

```
CD33  LDA $6182,X        ; CHAR_DRAW_LO[slot] -> $6038
CD36  STA $6038          ; (low byte of indirect call thunk)
CD39  LDA $6194,X        ; CHAR_DRAW_HI[slot] -> $6039
CD3C  STA $6039          ; usually = $DA89 (the generic blit)

CD3F  LDA $62C6,X        ; CHAR_ANIM_FRAME[slot]
CD42  CMP #$FF           ; $FF = "no sprite this frame"
CD44  BEQ skip

CD48  ASL                ; A = frame * 2 (2-byte ptr per anim entry)
       ...               ; with carry handling for >128 frames
CD4F  ADC $62D8,X        ; + CHAR_ANIM_TBL_LO[slot]
CD52  STA $16
CD56  ADC $62EA,X        ; + CHAR_ANIM_TBL_HI[slot]
CD59  STA $17
                         ; ($16/$17) -> entry in animation table

CD5D  LDA ($16),Y=1      ; A = metasprite_hi
CD5F  TAX
CD60  DEY
CD61  LDA ($16),Y=0      ; A = metasprite_lo
CD63  JSR $D7AB          ; -> JMP ($6038) -> $DA89 (or per-character)
```

### Per-character WRAM state slots

(All indexed by slot `X` — Kirby is presumably slot 0, then enemies)

| Address | Purpose |
|---|---|
| `$6182,X` / `$6194,X` | LO / HI of draw routine (typically `$DA89`) |
| `$62C6,X` | current animation frame number |
| `$62D8,X` / `$62EA,X` | LO / HI of animation table |
| `$6A`/`$6B` | scratch: X position (set per draw) |
| `$6C`/`$6D` | scratch: Y position (set per draw) |
| `$68` | OAM cursor (next free sprite slot * 4) |

### Tools

- `tools/dump_metasprite.py` — decode a metasprite from ROM by file
  offset or `(bank, CPU_addr)`. Verified against the live blit captured
  in `kirb_door1.log` (1-sprite particle at file `$3959A` and a 6-sprite
  enemy at file `$395C4`).

### Character architecture

Each character is a **bytecode VM script**, not a flat-table entry.
Per-slot VM state in WRAM:

| Address | Meaning |
|---|---|
| `$6380,X` / `$63A0,X` | script PC (lo / hi) |
| `$63C0,X` | script PRG bank |
| `$6360,X` | per-instruction delay countdown |
| `$6340,X` | call-stack pointer (per-character stack at `$6400 + slot*16`) |

VM step loop at `$3E:CDBF` (file `$7CDBF`):
1. Load PC into `$1E/$1F`, swap R7 to `$63C0,X` via `JSR $D796`.
2. `LDA ($1E),Y`; if opcode `>= $50` → wait (low nibble → `$6360,X`),
   else dispatch via handler-pointer tables at `$CE31/$CE70` and
   `JMP ($6038)`.

Notable opcodes (handler addresses in bank `$3E`):

| Op | Addr | Action |
|---|---|---|
| `$03` | `$CF40` | far CALL: read `[lo,hi,bank]`, push current PC+bank, jump to that script |
| `$05`/`$06` | `$CFD2/$CFE2` | RETURN: pop bank+PC from call stack |
| `$08` | `$D39E` | set 3-byte ref at `$627E/$6290/$62A2` |
| `$0D` | `$D482` | generic set per-slot var (var_idx + value); var bases at `$D49D/$D4A7` table — 10 of them: `$61CA, $61DC, $61EE, $6200, $6212, $6224, $6236, $6248, $625A, $626C` |
| `$15` | `$D518` | set anim-table pointer + frame (writes `$62D8,X / $62EA,X / $62FC,X`) |

So the per-character init isn't a static table — every character's
script *itself* writes the `$62D8/$62EA` (anim-table ptr), `$6182/$6194`
(draw-routine ptr), etc. as side effects of running its bytecode.

### Sprite CHR association

There are 3 metasprite-blit variants — choice is per-character (via
which routine the script wrote to `$6182,X/$6194,X`):

| Routine | Behaviour |
|---|---|
| `$DA89` | Plain blit; metasprite = `[count][dx,dy,tile,attr]*N`. CHR comes from level-globals (R0=`$0042`, R1=`$0043`). Chunk `$1C` (Kirby) uses this. |
| `$DDB3` | Reads byte 0 of metasprite as the **new R0** (sprite CHR low 2KB), then falls through to blit. |
| `$DDE9` | Reads byte 0 of metasprite as the **new R1** (sprite CHR high 2KB), then falls through to blit. |

Plus `$3D:AA52` is a one-off bulk CHR loader (R1..R4 from constants)
likely used by a specific cutscene/boss.

So most characters share the level's sprite CHR (so all the things on
screen in stage 1 use stage 1's R0=`$80`/R1=`$9A`). When a character
needs its own CHR, the `$DDB3`/`$DDE9` variants let each metasprite
specify its first byte. Verified per-character draw-routine table at
`$16:B9FD` (file `$2D9FD`) selected by `$62C7 / 2` — confirms the
pattern.

### PPU nametable upload buffer at `$74C8`

The buffer at `$74C8..$75C7` is a PPU upload buffer (nametable tile
stream). `$1F:C1CC: LDA ($16),Y / STA $2007 (PPUDATA) / INY / DEX /
BNE $C1CC` reads from `$74C8` and pipes to PPU. The bytes
(`$81/$82/$DA/$CF/...`) are PPU tile indices.

The compressed source `$13:AE7A` (file `$26E8A`) holds PPU-stream
content. The per-map 16-byte mini-header at `$13:AF2B + map*16` holds
graphics-pipe parameters.

Front-end loader at `$13:AE08`/`$13:AE15` picks the source, then
`$13:AF9B` LZ-decompresses to `$74C8`. Helper `$13:B516` is the
"decompress(src=A:X, dst=`$74C8`)" wrapper.

### Enemy spawn routine — `$3E:CBA2`

The high-volume spawn routine is **`$3E:CBA2`** — accounts for nearly
all enemy spawns. It is invoked `JSR $CBA2` with:
- `A` = spawn ID (an index into the 112-entry table in chunk `$18`)
- `X` = X position
- `Y` = Y position
- `$602F`/`$6030` = slot-range bounds for the allocator
- `$6023..$602F` = caller-set sprite state (already populated)

```
$CBA2  PHA / TYA PHA / TXA PHA        ; stash A, Y, X on stack
       JSR $D622                       ; find_eligible_slot_in_range
       BCC $CBB2                       ; ok -> alloc
       PLA / PLA / PLA / A=$FF / RTS   ; fail
$CBB2  JSR $D6EE                       ; alloc VM slot (Y = new slot)
       (initialise per-slot draw routines $61xx, position arrays
        $61CA-$6248 from $6023..$602A, draw fn = $DA89 etc.)
       (continues at $CC11..$CC47)
$CC47  PLA / JMP $CC63                 ; *** falls into $CCA7's
                                       *** spawn-by-ID dispatcher
$CC63  STA $6128,X                     ; the saved A = spawn_id
       (bank-switch R7=$18; look up A0A7/A117/A187[A])
       JSR $CCC4                       ; write PC/bank into new slot
```

`$CBA2` allocates and initialises a sprite slot and uses the 112-entry
table at `$18:A0A7/A117/A187`. The full table is extracted to
`docs/spawn_table.json`.

`$3E:CC4B` (direct ID-only spawn, no sprite-init) is rarely used.

**Allocator primitive `$3E:D6EE`** is the freelist pop. Caller mix:
- `$CBA2` (in-game enemy/sprite spawn) — the dominant path
- `$CCA7` (state-machine transitions for Kirby, etc.)
- Bytecode opcode `$07` (`$3E:D35E`)
- `$CC4B` (effectively unused)

The enemy catalog — every static `JSR $CBA2` site with its resolved
(spawn_id, target_pc, target_bank) — is in `docs/enemy_catalog.json`.
28 sites; 20 resolve statically. The other 8 pass A in dynamically.

Important: a "spawn ID" is NOT a per-character identity. Multiple
sites can spawn the same ID (e.g. ID `$02` → `$16:$AA3A` is called
from three different chunks). And the table only covers 16/112 IDs
that are reached statically; the rest live in the table for use by
boss-specific or scripted paths.

### Bytecode VM opcode `$07` — spawn-child (rare)

```
$3E:D35E (op $07)
   JSR $D6EE              ; alloc VM slot (Y = new index)
   BCS skip
   (link new slot into list)
   read PC.lo/hi from script operand bytes 1, 2
   bank inherits from parent's $63C0,Y
$D391  A=$03 / JMP $CE13    ; PC += 3
```

Only 3 invocations in stage 1. Used for specialised child scripts that
need to inherit the parent's PRG bank.

### Per-chunk bytecode opcode handlers (open)

The VM dispatcher in chunk `$3E` (`$CDBF`) handles common opcodes
`$00..$3F`. But individual PRG chunks can hold their OWN script-VM
opcode handlers that get invoked via the `JMP ($6038)` indirect inside
`$3E:CE00` — when a script's PC sits in a chunk-specific bytecode and
the operand bytes index into that chunk's local handler table.

Example: chunk `$22` has at least one local spawn opcode at
`$22:$AC5F`:
```
$22:$AC5F  LDY #1 / LDA ($1E),Y / PHA   ; arg1 from script = spawn_id
$22:$AC64  INY    / LDA ($1E),Y / TAY   ; arg2 = Y_pos
$22:$AC68  PLA                           ; A = spawn_id (back from stack)
$22:$AC69  JSR $A612                     ; trampoline -> $CBA2 spawn
$22:$AC6C  LDY #2 / JMP $8C9D            ; script PC += 2, resume VM
```

The trampoline at `$22:$A612` re-packs per-slot state ($6074..$60CE,X)
into globals ($602B..$602E, $602F=$09, $6030=$12) that `$CBA2` reads.

This explains the residual "stack-passed" `$CBA2` sites in the enemy
catalog — each is the tail of a per-chunk bytecode spawn opcode, with
the actual ID coming from the script's operand bytes (which the static
catalog cannot resolve without simulating chunk-specific bytecode
streams).

### Spawn-ID dispatch ($3E:CC4B → tables in chunk $18)

Two distinct VM-slot creation paths, both ending in `$3E:CCA7` (the
"raw" VM-slot setup that writes per-slot PC/bank to `$6380,Y/$63A0,Y/
$63C0,Y` and clears delay/callstack):

**Main path — `$3E:CC4B(slot=X, spawn_id=A)`** (file `$7CC5B`):
- bail if `$6128,X` already occupied
- alloc new VM index via `$D6B9`/`$D6EE`, store at `$614C,X`
- write spawn_id into `$6128,X`, copy to Y
- save current R7 (`$49`), switch R7 to chunk `$18` via `$D796`
- clear per-slot state arrays (`$60E0/$60F2/$6104/$6116/$625A/$626C/$62C6`)
- look up the spawn ID in three parallel tables now mapped at `$A0xx`:

| File offset | Table | What it holds |
|---|---|---|
| `$300B7` (`$18:A0A7`) | spawn_lo[id] | PC low byte |
| `$30127` (`$18:A117`) | spawn_hi[id] | PC high byte |
| `$30197` (`$18:A187`) | spawn_bank[id] | R7 chunk for the script |

- `JSR $CCC4` (inner half of `CCA7`) writes those into the new VM slot
- restore R7, RTS

There are `$70` (112) entries — IDs 0 through 6F. Distribution by bank
(see `docs/spawn_table.json` for the full table):

| Bank | # IDs | Notes |
|---|---|---|
| `$24` | 21 | biggest — generic enemies |
| `$25` | 13 | |
| `$26` | 11 | |
| `$28` | 8 | |
| `$2D` | 8 | |
| `$27` | 7 | |
| `$29` | 7 | |
| `$2C` | 6 | |
| `$19` | 4 | spawn IDs 4, 5, 7, 6E (one is the `JMP $CC4B` hardcoded ID 6E) |
| `$2A` | 4 | |
| `$2B` | 4 | |
| `$2F` | 3 | |
| `$3D` | 3 | |
| `$16`, `$23`, `$3B` | 2 each | |
| `$14`, `$18`, `$2E`, `$34`, `$36`, `$38`, `$3C` | 1 each | |

Verified caller: `$22:AE56` does `LDA #$6E / LDX #$01 / JMP $CC4B` —
hardcoded particle/effect spawn for slot 1.

**Alternate path — `$25:A135`** (file `$4A145`):
Uses a separate small table at `$25:A15C/A164/A16C/A174` (8 entries
each — looks like in-bank child characters: bosses or stage-specific
variants). Same dispatch pattern, but lo/hi/bank fetched locally with
`LDY $6200,X` as the index. Ends with `JMP $CCA7`.

The bytes from the `$74C8` grid → dispatch-ID mapping is **not yet**
extracted — the engine scans the grid as the camera scrolls into a new
column. That column-scan routine is still open.

### Bytecode VM dispatcher — `$3E:CDBF` fully RE'd

Step loop:
```
$CDBF  set up zp pointer ($1E/$1F) to current PC, $20/$21 to per-slot
       state base ($6400 + slot*$20)
$CDDF  LDA ($1E),Y; CMP #$50; BCC $CE03 -> table dispatch
   else (>= $50): high nibble = wait-subkind, low nibble = delay
$CE03  TAX
       LDA $CE31,X / LDA $CE70,X -> handler addr
       JMP ($6038)
$CE13  PC += A (advance for the handler); then if delay > 0 yield, else loop
```

Two handler tables in chunk `$3E`:
- opcode handlers `$CE31` (lo) / `$CE70` (hi) — 64 entries (op `$00..$3F`)
- wait-subkind handlers `$CEAF` (lo) / `$CEB8` (hi) — 16 entries

Key opcode handlers (decoded, all in chunk `$3E`):

| Op | Handler | Behaviour |
|---|---|---|
| `$03` | `$CF40` | **far JMP** — `[lo][hi][bank]`. Bank-switch, PC = (hi<<8)\|lo. (NOT a CALL; the slot's previous PC is gone.) |
| `$18` | `$CF5F` | **near CALL** — `[lo][hi]`. Pushes return PC onto per-slot stack (`$6340,X`), then jumps. |
| `$1A` | `$D522` | **set metasprite table** — `[lo][hi][bank]`. Writes anim table ptr+bank to `$62D8,X` (lo), `$62EA,X` (hi), `$62FC,X` (bank). |
| `$1B` | `$D53C` | **set `$63E0,X`** — `[byte]` (some per-slot flag, used by `$15` skip). |
| `$15` | `$D518` | **conditional-skip** if `$63E0,X != 0`. |
| `>= $50` | wait — low nibble of opcode = delay frames, high nibble = wait variant (mostly sub `$08`/`$0D` in practice). |

Spawn instructions: scripts can directly emit raw 6502 `JMP $CCA7` /
`JSR $CCA7` (which only works inside fixed-bank-callable code, because
`$3E:CCA7` is mapped at `$C000-$DFFF`). 281 such sites exist across
the ROM — see `docs/spawn_census.json`.

### Stage-entry path

When the player enters stage 1, code at `$0A:A7E8` does `LDX #$00 /
JMP $21:8CE8`. That routine indexes three parallel tables at
`$21:8CFF` (lo) / `$21:8DEE` (hi) / `$21:8EDD` (bank) — file
`$42D0F/$42DFE/$42EED` — 32 entries, ALL on bank `$14`. Entry 0 is
`$14:A5AE`, Kirby's idle script. The 32-entry table is **Kirby's
state machine** (idle / jump / inhale / etc.), not a stage spawn list.

Enemies are not staged by any central list. They come into being via:
- Scripts whose first instructions are `JMP $CCA7` patterns (each
  character may spawn its own children — projectiles, sub-sprites).
- The narrow `$3E:CC4B` ID-table path (only one verified caller).
- Initial spawn of `slot $01` (Kirby) at stage entry.

### Open questions

- No static "stage N has enemies X, Y, Z" table exists. To answer that
  you'd have to simulate scripts forward from each stage entry,
  following bytecode opcodes `$03/$18` (control flow) and recording
  every `JMP/JSR $CCA7` encountered.
- The first WAIT-subkind handler `$D006` (sub `$00`) is unanalyzed.
- Sprite CHR sub-banking — R0/R1 cover 4 KB but Kirby + enemies share
  this; MMC3 IRQ might swap sprites mid-frame the same way it does for
  the HUD/playfield BG split.
