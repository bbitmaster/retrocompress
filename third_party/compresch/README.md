# compresch (vendored)

Original unmodified copies of disch's **compresch** compression library,
preserved here because it isn't easily found online anywhere else.

## Files

| File | What it is |
|---|---|
| `Compresch.rar` | 2013 release — most complete, adds Castlevania IV, "Konami block", LttP support on top of Kirby + Pokemon. Visual Studio project (.sln/.vcproj) plus a VS6 variant (.dsp/.dsw). |
| `compresch_rel2.zip` | Release 2 (Jan 2008). Adds Pokemon Gold/Silver and the hybrid LZ blocks. Includes `docs/format_*.txt`. |
| `compresch_2008.zip` | Original first release (Jan 2008) — Kirby format only. |
| `LICENSE.txt` | Artistic License 2.0 (extracted from the 2013 release). |

## License

Artistic License 2.0 (see `LICENSE.txt`). In short: you may use and
distribute the unmodified library; modifications are themselves open
source; programs using the unmodified library have no licensing
restrictions imposed by it.

## Build

Originally a Visual Studio project. The C++ library code itself compiles
fine on g++ — only `main.cpp` is Windows-specific (`conio.h`, `_getch`,
`gets`). The Makefile at the repo root auto-extracts `compresch_rel2.zip`
into `third_party/compresch/extracted/` and builds the comparison
binaries against that.

To override (e.g., to point at the more complete 2013 source after
extracting `Compresch.rar` yourself):

```sh
make COMPRESCH_DIR=/path/to/your/compresch/src
```
