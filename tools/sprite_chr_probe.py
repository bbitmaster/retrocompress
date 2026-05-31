#!/usr/bin/env python3
"""Render a metasprite with every plausible R0/R1 sprite-CHR combo.

Useful because Kirby's Adventure resolves per-character sprite CHR from
the level globals at draw time (`$0042/$0043`), not from any per-class
table. Once we know a character appears on some level, that level's
header[4] gives the right R1. This tool lets us visually search for
the right CHR set when we don't yet know which level a character belongs to.

Usage:
    sprite_chr_probe.py <rom.nes> --file 0xXXXXX --out sheet.png
    sprite_chr_probe.py <rom.nes> --table-file 0xXXXXX --table-chunk 0xXX \\
                                  --table-idx 3 --out sheet.png

Renders one metasprite (--file or one pointer-table entry --table-idx)
with all 18 unique R1 values from the per-map CHR tables. R0 is held at
$80 (the only value observed across all stages).
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import render_metasprite as RM
from dump_metasprite import decode

# 18 unique header[4] values across all 327 maps.
ALL_R1 = [0x3C, 0x9A, 0x9C, 0x9E, 0xA0, 0xA2, 0xA8, 0xAA, 0xAC, 0xAE,
          0xC4, 0xCA, 0xCC, 0xD2, 0xD6, 0xE8, 0xEC, 0xEE]


def render_one_with_label(chr_rom, palette, entries, r0, r1, max_w, max_h):
    """Return rgba+w+h for a render under-padded to (max_w, max_h)."""
    chr_map = RM.load_sprite_chr(chr_rom, r0, r1)
    res = RM.render_metasprite(chr_map, palette, entries, 8)
    if not res:
        return None
    rgba, w, h, _, _ = res
    return rgba, w, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom')
    ap.add_argument('--file', type=lambda x: int(x, 0))
    ap.add_argument('--table-file', type=lambda x: int(x, 0))
    ap.add_argument('--table-chunk', type=lambda x: int(x, 0))
    ap.add_argument('--table-idx', type=int, default=0)
    ap.add_argument('--r0', type=lambda x: int(x, 0), default=0x80)
    ap.add_argument('--palette', default='2137202A212A1909212A1A0F213727072135250F213037172136260F2120250F',
                    help='32-byte combined BG+sprite palette (default: stage 1)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--scale', type=int, default=4)
    ap.add_argument('--per-row', type=int, default=6)
    args = ap.parse_args()

    with open(args.rom, 'rb') as f:
        rom = f.read()
    chr_rom = rom[16 + 0x80000 : 16 + 0xC0000]

    if args.file is not None:
        fo = args.file
    elif args.table_file is not None:
        if args.table_chunk is None:
            ap.error('--table-file requires --table-chunk')
        lo = rom[args.table_file + args.table_idx * 2]
        hi = rom[args.table_file + args.table_idx * 2 + 1]
        ptr = (hi << 8) | lo
        fo = 0x10 + args.table_chunk * 0x2000 + (ptr - 0x8000)
        print(f'Resolved table[{args.table_idx}] -> ${ptr:04X} -> file ${fo:X}')
    else:
        ap.error('specify --file OR (--table-file AND --table-chunk)')

    count, entries = decode(rom, fo)
    print(f'Metasprite at file ${fo:X}: count={count}')
    palette = bytes.fromhex(args.palette.replace(' ', ''))

    # Pre-render to find max dimensions
    renders = []
    max_w = max_h = 0
    for r1 in ALL_R1:
        res = render_one_with_label(chr_rom, palette, entries, args.r0, r1, 0, 0)
        if res:
            rgba, w, h = res
            renders.append((r1, rgba, w, h))
            max_w = max(max_w, w)
            max_h = max(max_h, h)

    # Lay out
    cell_w = max_w + 8
    cell_h = max_h + 4
    per_row = args.per_row
    rows = (len(renders) + per_row - 1) // per_row
    sheet_w = per_row * cell_w
    sheet_h = rows * cell_h
    bg = (60, 60, 70, 255)
    sheet = bytearray(sheet_w * sheet_h * 4)
    for i in range(sheet_h * sheet_w):
        sheet[i*4:i*4+4] = bytes(bg)
    for idx, (r1, rgba, w, h) in enumerate(renders):
        cx = (idx % per_row) * cell_w + (cell_w - w) // 2
        cy = (idx // per_row) * cell_h + (cell_h - h) // 2
        for ry in range(h):
            for rx in range(w):
                src = (ry * w + rx) * 4
                if rgba[src + 3] == 0:
                    continue
                dst = ((cy + ry) * sheet_w + (cx + rx)) * 4
                sheet[dst:dst+4] = rgba[src:src+4]
        print(f'  R0=${args.r0:02X} R1=${r1:02X}  -> {w}x{h}')

    scaled, fw, fh = RM.scale_rgba(sheet, sheet_w, sheet_h, args.scale)
    with open(args.out, 'wb') as f:
        f.write(RM.png_from_rgba(bytes(scaled), fw, fh))
    print(f'Wrote {args.out} ({fw}x{fh} px, {len(renders)} R1 banks)')


if __name__ == '__main__':
    main()
