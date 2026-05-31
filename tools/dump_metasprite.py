#!/usr/bin/env python3
"""Decode a Kirby's Adventure metasprite from ROM.

Format (RE'd from $1F:DA89 blit + $1F:CD33 dispatcher, see
docs/KIRBY_NES_DISASM.md "Sprite engine"):

    metasprite:
        [ count : u8 ]
        [ dx : i8, dy : i8, tile : u8, attr : u8 ] * count

    dx, dy are signed offsets added to the character's screen X/Y.
    tile is the OAM tile index (engine then XORs bit 0 — selects sprite
        pattern table $0000 vs $1000 — when writing to OAM byte 1).
    attr is the OAM attribute byte (palette + flip + priority).

The engine has 64 hardware sprites and double-buffers OAM at $0200/$0300.

Usage:
    dump_metasprite.py <rom.nes> --bank BB --addr AAAA
    dump_metasprite.py <rom.nes> --file 0xXXXXX
"""
import argparse


INES = 16


def file_off(bank, cpu):
    """8KB-bank file offset for `bank` mapped at $A000-$BFFF (R7 typical)."""
    return INES + bank * 0x2000 + (cpu - 0xA000)


def decode(rom, fo):
    count = rom[fo]
    entries = []
    for i in range(count):
        b = fo + 1 + i * 4
        dx = rom[b]
        dy = rom[b+1]
        tile = rom[b+2]
        attr = rom[b+3]
        if dx >= 0x80: dx -= 0x100  # i8
        if dy >= 0x80: dy -= 0x100
        entries.append((dx, dy, tile, attr))
    return count, entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom')
    ap.add_argument('--bank', type=lambda x: int(x, 0))
    ap.add_argument('--addr', type=lambda x: int(x, 0),
                    help='CPU address (used with --bank, assumes $A000-$BFFF slot)')
    ap.add_argument('--file', type=lambda x: int(x, 0),
                    help='Direct file offset into ROM')
    args = ap.parse_args()

    with open(args.rom, 'rb') as f:
        rom = f.read()

    if args.file is not None:
        fo = args.file
    elif args.bank is not None and args.addr is not None:
        fo = file_off(args.bank & 0x7F, args.addr)
    else:
        ap.error('specify --file OR (--bank AND --addr)')

    count, entries = decode(rom, fo)
    print(f'Metasprite @ file ${fo:X}  ({count} hardware sprite{"s" if count != 1 else ""}):')
    print(f'{"  idx":>5}  {"dx":>4} {"dy":>4}  tile  attr  (flip palette OAM-tile)')
    for i, (dx, dy, tile, attr) in enumerate(entries):
        oam_tile = tile ^ 0x01
        hflip = 'H' if attr & 0x40 else '-'
        vflip = 'V' if attr & 0x80 else '-'
        prio  = 'B' if attr & 0x20 else 'F'  # behind/front of BG
        pal   = attr & 0x03
        print(f'  [{i:2d}]  {dx:+4d} {dy:+4d}   ${tile:02X}   ${attr:02X}  ({hflip}{vflip} {prio} pal{pal}  oam=${oam_tile:02X})')


if __name__ == '__main__':
    main()
