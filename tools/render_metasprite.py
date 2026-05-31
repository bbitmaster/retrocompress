#!/usr/bin/env python3
"""Render Kirby's Adventure metasprite(s) to a PNG using ROM CHR + palette.

A metasprite is a list of (dx, dy, tile, attr) entries — see
docs/KIRBY_NES_DISASM.md "Sprite engine". This tool resolves each entry
into 8x8 pixels using the given CHR-bank window and the 32-byte palette
(positions 16..31 = sprite palettes $3F10-$3F1F), and composites them
onto a transparent (color $0F-by-default) background.

Usage:
    # Single metasprite, default CHR/palette = stage 1 captured
    render_metasprite.py rom.nes --file 0x395C4 --out /tmp/foo.png

    # Whole pointer table -> contact sheet of every metasprite it references
    render_metasprite.py rom.nes --table-file 0x3957A --table-chunk 0x1C \\
                                 --out /tmp/sheet.png
"""
import sys, os, argparse, struct, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import render_map_from_rom as R
from dump_metasprite import decode

# Defaults captured from the kirb_door1.log trace (stage 1, R0=$80 R1=$9A,
# sprite palettes = positions [16..31] of derived BG+sprite palette)
DEFAULT_R0 = 0x80
DEFAULT_R1 = 0x9A
# 32-byte stage 1 palette (BG sub0..3, then sprite sub0..3)
DEFAULT_PALETTE_HEX = '2137202A212A1909212A1A0F213727072135250F213037172136260F2120250F'

NES_PALETTE = R.NES_PALETTE


def png_from_rgba(rgba, w, h):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    raw = b''
    for y in range(h):
        raw += b'\x00' + rgba[y*w*4:(y+1)*w*4]
    idat = zlib.compress(raw, 6)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


def load_sprite_chr(chr_rom, R0, R1):
    """Build 4 KB sprite pattern table ($0000-$0FFF) from MMC3 R0/R1 (2KB each)."""
    chr_map = bytearray(0x1000)
    chr_map[0x0000:0x0800] = chr_rom[(R0 & 0xFE) * 0x400 : (R0 & 0xFE) * 0x400 + 0x800]
    chr_map[0x0800:0x1000] = chr_rom[(R1 & 0xFE) * 0x400 : (R1 & 0xFE) * 0x400 + 0x800]
    return chr_map


def render_tile(chr_map, palette, tile_idx, sub_pal):
    """Render one 8x8 NES tile to a 64-pixel RGBA buffer.

    palette is the 32-byte BG+sprite combined palette; sprite sub-palettes
    occupy offsets 16-31, so sub_pal (0..3) -> palette[16 + sub_pal*4 .. +3].
    Color 0 of every sub-palette is transparent on sprites.
    """
    t_off = tile_idx * 16
    plane0 = chr_map[t_off:t_off+8]
    plane1 = chr_map[t_off+8:t_off+16]
    pal_base = 16 + sub_pal * 4
    out = bytearray(64 * 4)
    for row in range(8):
        p0 = plane0[row]
        p1 = plane1[row]
        for col in range(8):
            bit = 7 - col
            c = ((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1)
            px = row * 8 + col
            if c == 0:
                # transparent
                out[px*4 + 3] = 0
            else:
                pal_byte = palette[pal_base + c]
                rr, gg, bb = NES_PALETTE[pal_byte & 0x3F]
                out[px*4] = rr; out[px*4+1] = gg; out[px*4+2] = bb; out[px*4+3] = 255
    return out


def render_metasprite(chr_map, palette, entries, sprite_size=8,
                      normalize_facing=False):
    """Render a metasprite. Returns (rgba, width, height, anchor_x, anchor_y).

    Anchor is positioned so the leftmost/topmost pixel is at (0,0). The
    anchor_x/anchor_y returned tell you where the character's (X, Y)
    origin sits within the bitmap.

    If normalize_facing is True, the metasprite is rendered as a
    "facing-right" version when the majority of tiles have HFLIP set:
    each entry's dx is negated (around the bounding-box centre) and
    each tile's HFLIP bit is toggled. This makes side-facing characters
    appear consistent in contact sheets.
    """
    if not entries:
        return None
    # Whether to horizontally mirror the final bitmap (decided below,
    # applied at the very end).
    mirror_after = False
    if normalize_facing:
        hflip_count = sum(1 for e in entries if e[3] & 0x40)
        if hflip_count * 2 > len(entries):
            mirror_after = True
    # Compute bounding box
    min_dx = min(e[0] for e in entries)
    min_dy = min(e[1] for e in entries)
    max_dx = max(e[0] for e in entries) + 8
    max_dy = max(e[1] for e in entries) + sprite_size
    W = max_dx - min_dx
    H = max_dy - min_dy
    anchor_x = -min_dx
    anchor_y = -min_dy

    rgba = bytearray(W * H * 4)

    for dx, dy, tile, attr in entries:
        # The engine XORs the stored tile with $01 before writing to OAM
        oam_tile = tile ^ 0x01
        sub_pal = attr & 0x03
        hflip = bool(attr & 0x40)
        vflip = bool(attr & 0x80)
        # In 8x16 sprite mode (sprite_size==16), the rendered tile pair is
        # (oam_tile & $FE) top + ((oam_tile & $FE) | 1) bottom. Bit 0 of
        # the OAM tile selects pattern table ($0000 or $1000), not the
        # second tile.
        if sprite_size == 16:
            top_tile = oam_tile & 0xFE
            bot_tile = top_tile | 0x01
            top_rgba = render_tile(chr_map, palette, top_tile, sub_pal)
            bot_rgba = render_tile(chr_map, palette, bot_tile, sub_pal)
        else:
            top_rgba = render_tile(chr_map, palette, oam_tile, sub_pal)
            bot_rgba = None
        # Place at (dx - min_dx, dy - min_dy)
        px = dx - min_dx
        py = dy - min_dy
        for ry in range(sprite_size):
            # Choose top or bottom tile, then row within that tile.
            if vflip:
                src_row_logical = sprite_size - 1 - ry
            else:
                src_row_logical = ry
            if sprite_size == 16:
                src_rgba = top_rgba if src_row_logical < 8 else bot_rgba
                src_y = src_row_logical & 7
            else:
                src_rgba = top_rgba
                src_y = src_row_logical
                if src_y >= 8:
                    continue
            for rx in range(8):
                src_x = 7 - rx if hflip else rx
                src_off = (src_y * 8 + src_x) * 4
                if src_rgba[src_off + 3] == 0:
                    continue  # transparent
                dst_x = px + rx
                dst_y = py + ry
                if 0 <= dst_x < W and 0 <= dst_y < H:
                    dst_off = (dst_y * W + dst_x) * 4
                    rgba[dst_off:dst_off+4] = src_rgba[src_off:src_off+4]
    if mirror_after:
        # Horizontally flip the rendered bitmap so majority-HFLIP'd
        # metasprites appear in the same facing direction as their
        # non-flipped peers.
        mirrored = bytearray(W * H * 4)
        for ry in range(H):
            for rx in range(W):
                src_off = (ry * W + rx) * 4
                dst_off = (ry * W + (W - 1 - rx)) * 4
                mirrored[dst_off:dst_off+4] = rgba[src_off:src_off+4]
        rgba = mirrored
        anchor_x = W - anchor_x
    return rgba, W, H, anchor_x, anchor_y


def scale_rgba(rgba, w, h, s):
    if s == 1:
        return rgba, w, h
    W2, H2 = w * s, h * s
    out = bytearray(W2 * H2 * 4)
    for y in range(h):
        for x in range(w):
            base = (y * w + x) * 4
            rgba_px = rgba[base:base+4]
            for sy in range(s):
                for sx in range(s):
                    o = ((y*s + sy) * W2 + (x*s + sx)) * 4
                    out[o:o+4] = rgba_px
    return out, W2, H2


def background_fill(rgba, w, h, fill_rgba=(80, 80, 96, 255)):
    """Replace transparent (alpha=0) pixels with a solid fill color."""
    out = bytearray(rgba)
    for i in range(0, len(out), 4):
        if out[i+3] == 0:
            out[i:i+4] = bytes(fill_rgba)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom')
    ap.add_argument('--file', type=lambda x: int(x, 0),
                    help='Render a single metasprite at this file offset')
    ap.add_argument('--table-file', type=lambda x: int(x, 0),
                    help='Walk a pointer table starting at this file offset')
    ap.add_argument('--table-chunk', type=lambda x: int(x, 0),
                    help='8 KB chunk index the table\'s pointers resolve into (R6 slot $8000-$9FFF)')
    ap.add_argument('--chr', nargs=2, type=lambda x: int(x, 0),
                    help='MMC3 R0 R1 (2KB sprite CHR banks). Default: stage 1 ($80 $9A)')
    ap.add_argument('--palette', help='32 hex bytes (BG+sprite combined)')
    ap.add_argument('--sprite-size', type=int, choices=[8, 16], default=8)
    ap.add_argument('--normalize-facing', action='store_true',
                    help='Mirror metasprites whose tiles are majority-HFLIPped, to show all sprites in a consistent facing direction')
    ap.add_argument('--out', required=True)
    ap.add_argument('--scale', type=int, default=4)
    ap.add_argument('--bg', default='505060', help='Sheet background (hex RRGGBB)')
    args = ap.parse_args()

    with open(args.rom, 'rb') as f:
        rom = f.read()
    chr_rom = rom[16 + 0x80000 : 16 + 0xC0000]

    R0, R1 = (args.chr if args.chr else (DEFAULT_R0, DEFAULT_R1))
    chr_map = load_sprite_chr(chr_rom, R0, R1)
    pal_hex = args.palette or DEFAULT_PALETTE_HEX
    palette = bytes.fromhex(pal_hex.replace(' ', ''))
    bg_rgba = tuple(bytes.fromhex(args.bg)) + (255,)

    if args.file is not None:
        # Single metasprite
        count, entries = decode(rom, args.file)
        print(f'Decoded metasprite at file ${args.file:X}: {count} sprite(s)')
        rgba, w, h, ax, ay = render_metasprite(chr_map, palette, entries, args.sprite_size, normalize_facing=args.normalize_facing)
        rgba = background_fill(rgba, w, h, bg_rgba)
        rgba, w, h = scale_rgba(rgba, w, h, args.scale)
        with open(args.out, 'wb') as f:
            f.write(png_from_rgba(bytes(rgba), w, h))
        print(f'Wrote {args.out} ({w}x{h} px)')
        return

    if args.table_file is not None:
        if args.table_chunk is None:
            ap.error('--table-file requires --table-chunk')
        chunk = args.table_chunk
        # Walk pointers — stops when a pointer leaves the chunk range or
        # when we'd be reading past the first pointer's target (since data
        # follows the table in the same chunk).
        ptrs = []
        i = 0
        min_ptr = 0xFFFF
        while i < 256:
            lo = rom[args.table_file + i*2]
            hi = rom[args.table_file + i*2 + 1]
            ptr = (hi << 8) | lo
            if not (0x8000 <= ptr <= 0xBFFF):
                break
            min_ptr = min(min_ptr, ptr)
            ptrs.append(ptr)
            i += 1
            next_tbl_cpu = 0x8000 + (args.table_file - 0x10 - chunk*0x2000) + i*2
            if next_tbl_cpu >= min_ptr:
                break
        print(f'Pointer table @ file ${args.table_file:X} has {len(ptrs)} entries:')

        # Render each
        rendered = []
        max_w = 0; max_h = 0
        for p in ptrs:
            fo = 0x10 + chunk * 0x2000 + (p - 0x8000)
            count, entries = decode(rom, fo)
            res = render_metasprite(chr_map, palette, entries, args.sprite_size, normalize_facing=args.normalize_facing)
            if res:
                rgba, w, h, ax, ay = res
                rendered.append((p, count, rgba, w, h))
                max_w = max(max_w, w); max_h = max(max_h, h)

        # Tight layout: cells sized to MAX dimension across all sprites (so
        # everything aligns), but small enough to actually fit on screen.
        # 10 per row for compactness.
        per_row = 10
        cell_w = max_w + 4
        cell_h = max_h + 4
        rows = (len(rendered) + per_row - 1) // per_row
        sheet_w = per_row * cell_w
        sheet_h = rows * cell_h
        sheet = bytearray(sheet_w * sheet_h * 4)
        for i in range(sheet_h * sheet_w):
            sheet[i*4:i*4+4] = bytes(bg_rgba)

        for idx, (p, count, rgba, w, h) in enumerate(rendered):
            cx = (idx % per_row) * cell_w + (cell_w - w) // 2
            cy = (idx // per_row) * cell_h + (cell_h - h) // 2
            for ry in range(h):
                for rx in range(w):
                    src = (ry * w + rx) * 4
                    dst = ((cy + ry) * sheet_w + (cx + rx)) * 4
                    sheet[dst:dst+4] = rgba[src:src+4]
            print(f'  [{idx:2d}] ${p:04X} count={count:2d}  ({w}x{h})')

        sheet_rgba, fw, fh = scale_rgba(sheet, sheet_w, sheet_h, args.scale)
        with open(args.out, 'wb') as f:
            f.write(png_from_rgba(bytes(sheet_rgba), fw, fh))
        print(f'Wrote {args.out} ({fw}x{fh} px, {len(rendered)} metasprites)')


if __name__ == '__main__':
    main()
