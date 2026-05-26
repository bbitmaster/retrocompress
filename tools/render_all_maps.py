#!/usr/bin/env python3
"""Render every Kirby's Adventure map to a directory of PNGs.

For each of the 327 maps:
  - decompresses the map + tileset
  - derives CHR banks (R0..R5) from header bytes 2 and 4
  - derives the 32-byte BG palette from ROM tables (header bytes 3 and 5)
  - writes PNG to <out_dir>/map_NNN.png

Names include the width/height in screens so you can quickly find
overworlds vs horizontal stages vs single-screen rooms.

Usage:
    render_all_maps.py <rom.nes> --out maps_gallery/ [--scale 2] [--start 0] [--end 327]
"""
import sys, os, argparse, struct, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import render_map_from_rom as R
from dump_map import decompress, file_off_for, MAP_BANK_TBL, MAP_HI_TBL, MAP_LO_TBL, MAP_N
from dump_tileset import MAP_TILESET_TBL, TILESET_BANK_TBL, TILESET_HI_TBL, TILESET_LO_TBL


def png_from_rgb(rgb, w, h):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    raw = b''
    for y in range(h):
        raw += b'\x00' + rgb[y*w*3:(y+1)*w*3]
    idat = zlib.compress(raw, 6)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


def render_one(rom, chr_rom, m, scale=1):
    # Decompress map
    bb = rom[MAP_BANK_TBL + m] & 0x7F
    hi = rom[MAP_HI_TBL + m]; lo = rom[MAP_LO_TBL + m]
    map_fo = file_off_for(bb, (hi << 8) | lo)
    if not (0 <= map_fo < len(rom)):
        return None, "bad map pointer"
    try:
        map_data, _ = decompress(rom, map_fo)
    except Exception as e:
        return None, f"map decomp failed: {e}"
    if len(map_data) < 218 + 192:
        return None, "map too small"

    # Decompress tileset
    ts_idx = rom[MAP_TILESET_TBL + m]
    ts_bb = rom[TILESET_BANK_TBL + ts_idx] & 0x7F
    ts_hi = rom[TILESET_HI_TBL + ts_idx]
    ts_lo = rom[TILESET_LO_TBL + ts_idx]
    ts_fo = file_off_for(ts_bb, (ts_hi << 8) | ts_lo)
    try:
        ts_data, _ = decompress(rom, ts_fo)
    except Exception as e:
        return None, f"tileset decomp failed: {e}"
    if len(ts_data) < 1344:
        return None, "tileset too small"

    # CHR banks + palette
    chr_banks = R.derive_chr_banks(rom, m, map_data)
    palette = R.derive_palette(rom, m, map_data)

    # Geometry
    HEADER = 218
    SCREEN_W = 16
    SCREEN_H = map_data[7] if map_data[7] else 12
    SCREEN_BYTES = SCREEN_W * SCREEN_H
    grid_bytes = map_data[HEADER:]
    W_SCREENS = map_data[0] if map_data[0] else 1
    H_SCREENS = map_data[1] if map_data[1] else 1
    seq_len = W_SCREENS * H_SCREENS
    physical = len(grid_bytes) // SCREEN_BYTES
    if seq_len == 0 or seq_len > 32 or physical < 1:
        return None, f"bad layout W={W_SCREENS} H={H_SCREENS} phys={physical}"

    cols, rows = SCREEN_W * W_SCREENS, SCREEN_H * H_SCREENS

    def at(c, r):
        col_slot = c // SCREEN_W
        row_slot = r // SCREEN_H
        local_c = c % SCREEN_W
        local_r = r % SCREEN_H
        seq_idx = row_slot * W_SCREENS + col_slot
        if seq_idx >= seq_len:
            return 0
        screen_id = map_data[8 + seq_idx]
        if screen_id >= physical:
            return 0
        return grid_bytes[screen_id * SCREEN_BYTES + local_r * SCREEN_W + local_c]

    def metatile_tiles(mt):
        return ts_data[mt*4 : mt*4 + 4]

    def metatile_palette(mt):
        b = ts_data[1024 + (mt >> 2)]
        shift = (3 - (mt & 3)) * 2
        return (b >> shift) & 0x03

    # Tile grid
    tile_cols = cols * 2
    tile_rows = rows * 2
    tile_grid = bytearray(tile_cols * tile_rows)
    pal_grid = bytearray(tile_cols * tile_rows)
    for r in range(rows):
        for c in range(cols):
            mt = at(c, r)
            t = metatile_tiles(mt)
            p = metatile_palette(mt)
            for sub_idx, (sy, sx) in enumerate([(0,0),(0,1),(1,0),(1,1)]):
                tile_grid[(r*2+sy) * tile_cols + (c*2+sx)] = t[sub_idx]
                pal_grid[(r*2+sy) * tile_cols + (c*2+sx)] = p

    # CHR window
    def chr_off(b, kb):
        if kb == 2: return (b & 0xFE) * 1024
        return b * 1024
    chr_map = bytearray(0x2000)
    chr_map[0x0000:0x0800] = chr_rom[chr_off(chr_banks[0], 2):chr_off(chr_banks[0], 2)+0x800]
    chr_map[0x0800:0x1000] = chr_rom[chr_off(chr_banks[1], 2):chr_off(chr_banks[1], 2)+0x800]
    chr_map[0x1000:0x1400] = chr_rom[chr_off(chr_banks[2], 1):chr_off(chr_banks[2], 1)+0x400]
    chr_map[0x1400:0x1800] = chr_rom[chr_off(chr_banks[3], 1):chr_off(chr_banks[3], 1)+0x400]
    chr_map[0x1800:0x1C00] = chr_rom[chr_off(chr_banks[4], 1):chr_off(chr_banks[4], 1)+0x400]
    chr_map[0x1C00:0x2000] = chr_rom[chr_off(chr_banks[5], 1):chr_off(chr_banks[5], 1)+0x400]

    # Render
    pt_base = 0x1000
    W = tile_cols * 8
    H = tile_rows * 8
    img = bytearray(W * H * 3)
    for ty in range(tile_rows):
        for tx in range(tile_cols):
            tile_idx = tile_grid[ty*tile_cols + tx]
            pal_idx = pal_grid[ty*tile_cols + tx]
            t_off = pt_base + tile_idx * 16
            p0 = chr_map[t_off:t_off+8]
            p1 = chr_map[t_off+8:t_off+16]
            for row in range(8):
                a, b = p0[row], p1[row]
                for col in range(8):
                    bit = 7 - col
                    c = ((a >> bit) & 1) | (((b >> bit) & 1) << 1)
                    pal_byte = palette[0] if c == 0 else palette[pal_idx*4 + c]
                    r, g, bl = R.NES_PALETTE[pal_byte & 0x3F]
                    px = (ty*8 + row)*W + (tx*8 + col)
                    img[px*3] = r; img[px*3+1] = g; img[px*3+2] = bl

    if scale > 1:
        s = scale
        W2, H2 = W*s, H*s
        out = bytearray(W2*H2*3)
        for y in range(H):
            for x in range(W):
                base = (y*W + x) * 3
                rgb = img[base:base+3]
                for sy in range(s):
                    for sx in range(s):
                        o = ((y*s+sy)*W2 + (x*s+sx))*3
                        out[o:o+3] = rgb
        img = out
        W, H = W2, H2

    return png_from_rgb(bytes(img), W, H), {
        'W_screens': W_SCREENS, 'H_screens': H_SCREENS,
        'tileset': ts_idx, 'cols': cols, 'rows': rows, 'w_px': W, 'h_px': H,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom')
    ap.add_argument('--out', required=True)
    ap.add_argument('--scale', type=int, default=1)
    ap.add_argument('--start', type=int, default=0)
    ap.add_argument('--end', type=int, default=MAP_N)
    args = ap.parse_args()

    with open(args.rom, 'rb') as f:
        rom = f.read()
    chr_off = 16 + 0x80000
    chr_rom = rom[chr_off:chr_off + 0x40000]

    os.makedirs(args.out, exist_ok=True)
    summary_path = os.path.join(args.out, 'index.txt')
    summary = open(summary_path, 'w')

    n_ok = 0; n_err = 0
    for m in range(args.start, args.end):
        png, meta = render_one(rom, chr_rom, m, scale=args.scale)
        if png is None:
            n_err += 1
            summary.write(f'{m:03d}  FAIL  {meta}\n')
            print(f'  map {m:3d}: FAIL ({meta})')
            continue
        n_ok += 1
        name = f'map_{m:03d}_ts{meta["tileset"]:02X}_{meta["W_screens"]}x{meta["H_screens"]}.png'
        path = os.path.join(args.out, name)
        with open(path, 'wb') as fh:
            fh.write(png)
        summary.write(f'{m:03d}  OK    ts={meta["tileset"]:02X}  {meta["W_screens"]}x{meta["H_screens"]} screens  {meta["w_px"]}x{meta["h_px"]} px  {name}\n')
        if (m - args.start) % 20 == 0:
            print(f'  map {m:3d}: {meta["W_screens"]}x{meta["H_screens"]} screens, {meta["w_px"]}x{meta["h_px"]} px -> {name}')

    summary.close()
    print()
    print(f'Done: {n_ok} ok, {n_err} failed.  Index: {summary_path}')


if __name__ == '__main__':
    main()
