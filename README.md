# retrocompress

Provably-optimal compressor for the 3-bit-command / 5-bit-length compression
format family used by many 4th- and 5th-generation Nintendo, Capcom, Konami,
and HAL Laboratory titles, including:

- **Super Mario World**
- **Kirby's Adventure (NES)** and **Kirby Super Star (SNES)**
- **Super Metroid (SNES)**
- **The Legend of Zelda: A Link to the Past**
- **Castlevania IV**
- **Pokemon Gold / Silver**
- many other Capcom and Konami SNES titles using the "Konami block" variant

## What's it do

Encodes data using a true **O(n) dynamic-programming shortest-path** parser
that makes provably-optimal block choices for any given source bytes. The
implementation runs in linear time via SA-IS suffix arrays, Kasai LCP,
sliding-window-min for raw blocks, and constrained LPF queries for the
LZ-variant types. On real game data it matches or beats existing community
encoders by a few percent versus the original ROM.

See [docs/NOTES.md](docs/NOTES.md) for the algorithm details, the journey,
and the (interesting!) story of why compresch was 7 bytes off on Kirby's
Adventure.

See [docs/KIRBY_NES_RESULTS.md](docs/KIRBY_NES_RESULTS.md) for full numbers
on Kirby's Adventure (NES) — compresch vs retrocompress on 361 documented
compressed blocks.

## Supported games

| Game | Decoder | Optimal encoder | Pointer-table walker |
|---|---|---|---|
| Kirby's Adventure (NES) | yes | yes | yes — 427 blobs inventoried, full repacker (`repacker_kirby_nes`) produces a verified valid ROM with all pointer tables updated, 5.7 KB / 3.9% saved |
| Super Metroid (SNES) | yes | not yet | tilesets only (87/87 decode) |
| Other family members | reachable | not yet | not yet |

Adding a new game requires:
1. Identify the family variant (which LZ sub-types it uses).
2. Plug in the right block-type / payload-cost tables.
3. (For the walker) point at the game's pointer table addresses.

## Building

```sh
make core    # binaries with no external deps:
             #   test_basic, walker_kirby_nes, walker_super_metroid
make         # also builds the comparison binaries:
             #   bench, diff_one, scanner — these link against
             #   disch's compresch, which is vendored as a zip under
             #   third_party/compresch/ and auto-extracted on first build.
```

Override the compresch source dir if you want a different version:

```sh
make COMPRESCH_DIR=/path/to/your/compresch/src
```

## Running

```sh
./test_basic                                                 # unit tests
./walker_kirby_nes path/to/kirby.nes                         # Kirby NES analysis
./walker_super_metroid path/to/super_metroid.smc             # SM analysis

# The full Kirby NES repacker — reads a ROM, recompresses every blob with
# retrocompress, repacks within each PRG bank, patches all pointer tables
# and inline immediates, and writes an output ROM that plays identically.
./repacker_kirby_nes path/to/kirby.nes -o kirby_repacked.nes --verify --verbose
```

## History

This repo started as an ancient 2013 experiment, then sat dormant for years.
The 2013 attempt is preserved on branch `original-2013` (and tag
`pre-retrocompress-2013`). The current code is a complete rewrite that brings
the project to a modern working state, with full source in [src/](src/) and
writeups in [docs/](docs/).

```sh
git checkout original-2013   # to see the 2013 code
git checkout master          # back to current
```

## License

Code: MIT (intent — license file not yet committed).
Disch's compresch (a dependency for the comparison binaries) is Artistic
License 2.0 and is not vendored — point `COMPRESCH_DIR` at your own copy if
you want those binaries.
