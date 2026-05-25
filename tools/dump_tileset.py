#!/usr/bin/env python3
"""Decompress a Kirby's Adventure NES tileset, or dump the map->tileset table.

Pointer tables:
  MAP_TILESET_TBL  @ file $24628  (CPU $8618 in bank 18)  — 327 bytes
  TILESET_BANK_TBL @ file $249FD  (CPU $89ED)
  TILESET_HI_TBL   @ file $24A2E  (CPU $8A1E)
  TILESET_LO_TBL   @ file $24A5F  (CPU $8A4F)

Each map slot stores an index into the tileset tables. There are 49
tilesets (0..0x30).

Usage:
    dump_tileset.py <rom.nes> --tileset N [--out OUT]
    dump_tileset.py <rom.nes> --for-map M
    dump_tileset.py <rom.nes> --list
"""
import sys, argparse
from dump_map import decompress, file_off_for, INES, BANK_SIZE

MAP_TILESET_TBL  = 0x24628
TILESET_BANK_TBL = 0x249FD
TILESET_HI_TBL   = 0x24A2E
TILESET_LO_TBL   = 0x24A5F
TILESET_N        = 0x31  # 49
MAP_N            = 0x147  # 327

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom')
    ap.add_argument('--tileset', type=lambda x: int(x, 0))
    ap.add_argument('--for-map', type=lambda x: int(x, 0))
    ap.add_argument('--out', default='/tmp/tileset_dump.bin')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    with open(args.rom, 'rb') as f:
        rom = f.read()

    if args.list:
        print(f'{"idx":>4} {"bank":>4} {"cpu":>5} {"file":>7}')
        for i in range(TILESET_N):
            bb = rom[TILESET_BANK_TBL + i] & 0x7F
            hi = rom[TILESET_HI_TBL + i]
            lo = rom[TILESET_LO_TBL + i]
            addr = (hi << 8) | lo
            fo = file_off_for(bb, addr) if 0xA000 <= addr <= 0xBFFF else 0
            print(f'{i:>4} {bb:>4} ${addr:04X} {fo:>7X}')
        print()
        print(f'\nMap -> tileset index (first 16 maps):')
        for m in range(min(16, MAP_N)):
            ts_idx = rom[MAP_TILESET_TBL + m]
            print(f'  map {m:>3}: tileset {ts_idx:>3} (${ts_idx:02X})')
        return

    if args.for_map is not None:
        m = args.for_map
        ts_idx = rom[MAP_TILESET_TBL + m]
        print(f'Map {m} uses tileset {ts_idx} (${ts_idx:02X})')
        idx = ts_idx
    elif args.tileset is not None:
        idx = args.tileset
    else:
        print('Specify --tileset N OR --for-map M OR --list')
        return

    bb = rom[TILESET_BANK_TBL + idx] & 0x7F
    hi = rom[TILESET_HI_TBL + idx]
    lo = rom[TILESET_LO_TBL + idx]
    addr = (hi << 8) | lo
    fo = file_off_for(bb, addr)
    print(f'Tileset {idx}: bank=${bb:02X} cpu=${addr:04X} file=${fo:X}')

    dec, csz = decompress(rom, fo)
    print(f'Compressed size: {csz} bytes')
    print(f'Decompressed:    {len(dec)} bytes')
    with open(args.out, 'wb') as f:
        f.write(dec)
    print(f'Wrote {args.out}')

    print('\nFirst 256 decompressed bytes:')
    for row in range(0, min(256, len(dec)), 16):
        h = ' '.join(f'{b:02X}' for b in dec[row:row+16])
        print(f'  {row:04X}: {h}')

if __name__ == '__main__':
    main()
