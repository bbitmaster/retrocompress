#!/usr/bin/env python3
"""Render a Kirby's Adventure map directly from the ROM.

Pipeline:
  1) Decompress map blob (gives 26-byte header + 16x60 metatile grid)
  2) Decompress tileset blob (gives metatile definitions: 4 tile indices per metatile)
  3) Look up the metatile -> tile expansion to build a tile-index grid
  4) Render the tile grid using CHR ROM (banks taken from arguments) + a palette

This is the eventual goal: render maps without an FCEUX trace.

Usage:
    render_map_from_rom.py <rom.nes> <map_index> --chr R0 R1 R2 R3 R4 R5 \\
      --palette HEX32 --out out.png [--layout row|col] [--scale N]
"""
import sys, argparse, zlib, struct
from dump_map import decompress, file_off_for, MAP_BANK_TBL, MAP_HI_TBL, MAP_LO_TBL
from dump_tileset import MAP_TILESET_TBL, TILESET_BANK_TBL, TILESET_HI_TBL, TILESET_LO_TBL

NES_PALETTE = [
    (84,84,84),(0,30,116),(8,16,144),(48,0,136),(68,0,100),(92,0,48),(84,4,0),(60,24,0),
    (32,42,0),(8,58,0),(0,64,0),(0,60,0),(0,50,60),(0,0,0),(0,0,0),(0,0,0),
    (152,150,152),(8,76,196),(48,50,236),(92,30,228),(136,20,176),(160,20,100),(152,34,32),(120,60,0),
    (84,90,0),(40,114,0),(8,124,0),(0,118,40),(0,102,120),(0,0,0),(0,0,0),(0,0,0),
    (236,238,236),(76,154,236),(120,124,236),(176,98,236),(228,84,236),(236,88,180),(236,106,100),(212,136,32),
    (160,170,0),(116,196,0),(76,208,32),(56,204,108),(56,180,204),(60,60,60),(0,0,0),(0,0,0),
    (236,238,236),(168,204,236),(188,188,236),(212,178,236),(236,174,236),(236,174,212),(236,180,176),(228,196,144),
    (204,210,120),(180,222,120),(168,226,144),(152,226,180),(160,214,228),(160,162,160),(0,0,0),(0,0,0),
]

def png_from_rgb(rgb_bytes, w, h):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    raw = b''
    for y in range(h):
        raw += b'\x00' + rgb_bytes[y*w*3:(y+1)*w*3]
    idat = zlib.compress(raw, 9)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')

def chr_bank_offset(bank_byte, granularity_kb):
    if granularity_kb == 2:
        return (bank_byte & 0xFE) * 1024
    return bank_byte * 1024

# Known-good CHR bank + palette per tileset, captured from FCEUX traces.
# Keys are tileset indices (0..48). To expand: run the level in FCEUX,
# capture a trace, and read off MMC3 R0..R5 + active palette.
TILESET_DEFAULTS = {
    # Tileset 21 = Vegetable Valley world map (map 0).
    # From trace line 2700000.
    21: {
        'chr': (0xD0, 0xD8, 0x00, 0xF0, 0xF1, 0xFD),
        'palette': '203121122039290920392921203727072035250F2030371720202C0F2037280F',
    },
    # Tileset 7 = Vegetable Valley stage 1 (map 43).
    # R0/R1 = $82/$9A (sprite CHR pair from L5.5M), R2-R5 = $00 $0E $0F $1F
    # (BG CHR quad during playfield rendering). Default BG pattern table
    # is $1000 so R2..R5 drive the visuals.
    7: {
        'chr': (0x82, 0x9A, 0x00, 0x0E, 0x0F, 0x1F),
        'palette': '2137202A212A1909212A1A0F2137270721352F0F2130371721262E0F2127280F',
    },
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom')
    ap.add_argument('map_index', type=lambda x: int(x, 0))
    ap.add_argument('--chr', nargs=6, type=lambda x: int(x, 0),
                    help='MMC3 R0..R5 (CHR banks). R0/R1 are 2KB each, R2-R5 are 1KB each. '
                         'Overrides the tileset default.')
    ap.add_argument('--palette', type=str,
                    help='32 hex bytes (no spaces). Overrides the tileset default.')
    ap.add_argument('--out', required=True)
    ap.add_argument('--layout', choices=['sequence','physical'], default='sequence',
                    help='sequence: follow the screen-sequence table at decomp[8..] (default — '
                         'the order the player walks through); '
                         'physical: show physical screens in storage order')
    ap.add_argument('--scale', type=int, default=2)
    ap.add_argument('--pt-base', type=lambda x: int(x, 0), default=0x1000,
                    help='Pattern table base (0x0000 or 0x1000). default: 0x1000')
    args = ap.parse_args()

    with open(args.rom, 'rb') as f:
        rom = f.read()
    chr_rom_offset = 16 + 0x80000
    chr_rom = rom[chr_rom_offset:chr_rom_offset + 0x40000]
    assert len(chr_rom) == 0x40000

    # 1) Decompress map
    m = args.map_index
    bb = rom[MAP_BANK_TBL + m] & 0x7F
    hi = rom[(MAP_HI_TBL := 0x2476F) + m]
    lo = rom[(MAP_LO_TBL := 0x248B6) + m]
    map_fo = file_off_for(bb, (hi << 8) | lo)
    map_data, _ = decompress(rom, map_fo)
    print(f'Map {m}: {len(map_data)} bytes from file ${map_fo:X}')

    # 2) Decompress tileset
    ts_idx = rom[MAP_TILESET_TBL + m]
    ts_bb = rom[TILESET_BANK_TBL + ts_idx] & 0x7F
    ts_hi = rom[TILESET_HI_TBL + ts_idx]
    ts_lo = rom[TILESET_LO_TBL + ts_idx]
    ts_fo = file_off_for(ts_bb, (ts_hi << 8) | ts_lo)
    ts_data, _ = decompress(rom, ts_fo)
    print(f'Tileset {ts_idx}: {len(ts_data)} bytes from file ${ts_fo:X}')

    # 2a) Resolve CHR banks and palette from known defaults, or args
    defaults = TILESET_DEFAULTS.get(ts_idx, {})
    chr_banks = args.chr or defaults.get('chr')
    pal_hex = args.palette or defaults.get('palette')
    if chr_banks is None or pal_hex is None:
        print(f'ERROR: no CHR/palette defaults for tileset {ts_idx}; pass --chr and --palette.')
        sys.exit(2)
    print(f'CHR banks (R0..R5): {" ".join(f"${b:02X}" for b in chr_banks)}')
    print(f'Palette: {pal_hex}')

    # 3) Tileset layout (confirmed by reverse-engineering the unpacker
    # at $1C:AD04..AD16 (tile tables) and $1C:AD2C..AD56 (palette table)):
    #   bytes 0..1023     = 256 metatiles, 4 tile indices each (TL, TR, BL, BR)
    #                       unpacked to RAM $7A00 (TL), $7B00 (TR), $7C00 (BL), $7D00 (BR)
    #   bytes 1024..1087  = 64 bytes of packed 2-bit palette indices, MSB-FIRST:
    #                       mt = 4*Y + k where k=0 uses bits 6-7, k=1 bits 4-5,
    #                       k=2 bits 2-3, k=3 bits 0-1. Unpacked to $7E00.
    #   bytes 1088..1343  = 256 bytes of metatile collision/property flags
    def metatile_tiles(mt_idx):
        base = mt_idx * 4
        return ts_data[base:base+4]
    def metatile_palette(mt_idx):
        b = ts_data[1024 + (mt_idx >> 2)]
        # MSB-first: mt 0 within a byte is bits 6-7, mt 3 is bits 0-1
        shift = (3 - (mt_idx & 3)) * 2
        return (b >> shift) & 0x03

    # 4) True format (RE'd from disasm of $09:8190 column-fill +
    # $1F:EBAB row-pointer setup, plus direct inspection of byte values):
    #
    #   Header: 218 bytes (NOT 26 — bytes 26..217 are padding so the
    #   screen-storage region starts at WRAM $68C8). Within the header,
    #   bytes 8..N hold the screen-sequence table — indices into the
    #   physical screen storage. Map 43 has '00 01 02 03' (linear).
    #
    #   Screens: each is 192 bytes = 16 metatile-cols x 12 metatile-rows,
    #   ROW-MAJOR. data[r*16 + c]. byte[7] = $0C = 12 = SCREEN HEIGHT in
    #   metatile-rows. $EBAB computes ptr = base[screen_id] + (row*16) so
    #   row*16 indexing matches what the engine does at runtime.
    #
    #   Sanity check for map 43: row 9 = all $58, row 10 = $30/$20 checker,
    #   row 11 = all $28 — classic ground-edge / brick / dirt layout
    #   visible only under row-major interpretation.
    HEADER = 218
    SCREEN_H = map_data[7] if map_data[7] else 12   # byte 7 = screen HEIGHT (12 for horizontal stages)
    SCREEN_W = 16   # always 16 metatile cols per screen (one nametable wide)
    SCREEN_BYTES = SCREEN_W * SCREEN_H  # 192 for the common case
    grid_bytes = map_data[HEADER:]
    physical_screens = len(grid_bytes) // SCREEN_BYTES
    SCREENS = map_data[0]  # byte 0 = sequence length
    print(f'Physical screens stored: {physical_screens}, sequence length (byte 0): {SCREENS}')
    print(f'Screen height (byte 7): {SCREEN_H} metatile rows; width assumed {SCREEN_W} cols')
    print(f'Sequence: ' + ' '.join(f'{map_data[8+i]:02X}' for i in range(SCREENS)))
    # Each screen: 16 cols x 12 rows row-major, 192 bytes.
    # screen_data[col, row] = grid_bytes[screen_id * 192 + row * 16 + col]
    cols, rows = SCREEN_W * SCREENS, SCREEN_H
    def at(c, r):
        slot = c // SCREEN_W
        local_c = c % SCREEN_W
        screen_id = map_data[8 + slot] if args.layout != 'physical' else slot
        return grid_bytes[screen_id * SCREEN_BYTES + r * SCREEN_W + local_c]

    print(f'Rendering as {cols} x {rows} metatiles ({cols*2} x {rows*2} tiles, {cols*16} x {rows*16} px)')

    # 5) Tile-index grid + per-tile palette index (each metatile -> 4 tiles in a 2x2 quad)
    tile_cols = cols * 2
    tile_rows = rows * 2
    tile_grid = bytearray(tile_cols * tile_rows)
    pal_grid  = bytearray(tile_cols * tile_rows)
    for r in range(rows):
        for c in range(cols):
            mt = at(c, r)
            t = metatile_tiles(mt)
            p = metatile_palette(mt)
            for sub_idx, (sy, sx) in enumerate([(0,0),(0,1),(1,0),(1,1)]):
                tile_grid[(r*2+sy) * tile_cols + (c*2+sx)] = t[sub_idx]
                pal_grid [(r*2+sy) * tile_cols + (c*2+sx)] = p

    # 6) CHR window
    R = chr_banks
    chr_map = bytearray(0x2000)
    chr_map[0x0000:0x0800] = chr_rom[chr_bank_offset(R[0],2):chr_bank_offset(R[0],2)+0x800]
    chr_map[0x0800:0x1000] = chr_rom[chr_bank_offset(R[1],2):chr_bank_offset(R[1],2)+0x800]
    chr_map[0x1000:0x1400] = chr_rom[chr_bank_offset(R[2],1):chr_bank_offset(R[2],1)+0x400]
    chr_map[0x1400:0x1800] = chr_rom[chr_bank_offset(R[3],1):chr_bank_offset(R[3],1)+0x400]
    chr_map[0x1800:0x1C00] = chr_rom[chr_bank_offset(R[4],1):chr_bank_offset(R[4],1)+0x400]
    chr_map[0x1C00:0x2000] = chr_rom[chr_bank_offset(R[5],1):chr_bank_offset(R[5],1)+0x400]

    # 7) Palette
    pal_hex = pal_hex.replace(' ', '').replace(',', '')
    palette = bytes.fromhex(pal_hex)
    if len(palette) != 32:
        print(f'WARNING: palette is {len(palette)} bytes, expected 32')

    # 8) Render using per-metatile attribute (BG palette index 0..3)
    pt_base = args.pt_base
    W = tile_cols * 8
    H = tile_rows * 8
    img = bytearray(W*H*3)
    for ty in range(tile_rows):
        for tx in range(tile_cols):
            tile_idx = tile_grid[ty*tile_cols + tx]
            pal_idx  = pal_grid [ty*tile_cols + tx]
            t_off = pt_base + tile_idx * 16
            plane0 = chr_map[t_off:t_off+8]
            plane1 = chr_map[t_off+8:t_off+16]
            for row in range(8):
                p0 = plane0[row]; p1 = plane1[row]
                for col in range(8):
                    bit = 7 - col
                    c = ((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1)
                    pal_byte = palette[0] if c == 0 else palette[pal_idx*4 + c]
                    r, g, b = NES_PALETTE[pal_byte & 0x3F]
                    px = (ty*8 + row)*W + (tx*8 + col)
                    img[px*3] = r; img[px*3+1] = g; img[px*3+2] = b

    # 9) Optional scale
    if args.scale > 1:
        s = args.scale
        W2 = W*s; H2 = H*s
        out = bytearray(W2*H2*3)
        for y in range(H):
            for x in range(W):
                base = (y*W + x) * 3
                rgb = img[base:base+3]
                for sy in range(s):
                    for sx in range(s):
                        o = ((y*s+sy)*W2 + (x*s+sx))*3
                        out[o:o+3] = rgb
        img = out; W, H = W2, H2

    with open(args.out, 'wb') as f:
        f.write(png_from_rgb(bytes(img), W, H))
    print(f'Wrote {args.out} ({W} x {H} px)')

if __name__ == '__main__':
    main()
