#!/usr/bin/env python3
"""Dump Kirby's Adventure (NES) compressed tile data as PNG tile sheets.

For each compressed blob whose decompressed bytes are sized like CHR tile
data (multiples of 16 bytes, i.e. 8x8 NES tiles), decompress and render as
a tile sheet: 16 tiles per row, grayscale (NES is 2 bits per pixel = 4
shades). Maps (level layout data) are skipped — they're metatile indices,
not graphics.

Usage:
    python3 tools/dump_tilesheets.py path/to/kirby.nes [output_dir]

Output: one PNG per compressed blob in <output_dir> (default ./tilesheets/),
plus INDEX.txt listing every blob with its source category, file offset,
decompressed size, and PNG dimensions.
"""

import os, sys, struct, zlib

# ---------------- Kirby decompressor ----------------

def decompress(src, base=0):
    out = bytearray(); i = base
    while i < len(src):
        ctrl = src[i]; i += 1
        if ctrl == 0xFF: return bytes(out)
        cmd = ctrl >> 5
        if cmd == 7:
            cmd = (ctrl >> 2) & 7
            ln = (((ctrl & 3) << 8) | src[i]) + 1; i += 1
        else: ln = (ctrl & 0x1F) + 1
        if cmd == 7: cmd = 4
        try:
            if cmd == 0:
                out += src[i:i+ln]; i += ln
            elif cmd == 1:
                out += bytes([src[i]] * ln); i += 1
            elif cmd == 2:
                a, b = src[i], src[i+1]; i += 2
                for _ in range(ln): out += bytes([a, b])
            elif cmd == 3:
                v = src[i]; i += 1
                for _ in range(ln): out.append(v); v = (v + 1) & 0xFF
            elif cmd in (4, 5, 6):
                addr = (src[i] << 8) | src[i+1]; i += 2
                for _ in range(ln):
                    if cmd == 4: out.append(out[addr]); addr += 1
                    elif cmd == 5:
                        by = out[addr]; r = 0
                        for _ in range(8): r = (r << 1) | (by & 1); by >>= 1
                        out.append(r); addr += 1
                    else: out.append(out[addr]); addr -= 1
        except Exception:
            return None
    return None

# ---------------- NES tile rendering ----------------
# Each NES tile = 16 bytes: bytes 0..7 are bit plane 0 of rows 0..7,
# bytes 8..15 are bit plane 1. Pixel value at (x, y) = bit `7-x` of
# (bp0[y]) plus 2x bit `7-x` of bp1[y]. Range 0..3.

def render_tile(out_px, x0, y0, W, tile_bytes, scale=1):
    """Paint one 8x8 NES tile into out_px (palette indices 0..3)."""
    for y in range(8):
        bp0 = tile_bytes[y]
        bp1 = tile_bytes[8 + y]
        for x in range(8):
            b = 7 - x
            v = ((bp0 >> b) & 1) | (((bp1 >> b) & 1) << 1)
            base_x = x0 + x * scale
            base_y = y0 + y * scale
            for sy in range(scale):
                row_off = (base_y + sy) * W + base_x
                for sx in range(scale):
                    out_px[row_off + sx] = v

def render_nametable(name_data, chr_data, chr_offset=0, scale=2):
    """Render a single 1024-byte nametable using CHR data starting at chr_offset.
    Returns (W, H, pixels). NES nametable is 32x30 8x8 tiles = 256x240 pixels.
    name_data[0:960] = tile indices; name_data[960:1024] = attribute bytes (ignored
    in this grayscale view).
    """
    if len(name_data) < 960: return None
    Wt, Ht = 32 * 8, 30 * 8     # 256 x 240
    W, H = Wt * scale, Ht * scale
    px = bytearray(W * H)
    for cell in range(960):
        tile_idx = name_data[cell]
        col, row = cell % 32, cell // 32
        tile_off = chr_offset + tile_idx * 16
        if tile_off + 16 > len(chr_data): continue
        x0, y0 = col * 8 * scale, row * 8 * scale
        render_tile(px, x0, y0, W, chr_data[tile_off:tile_off+16], scale)
    return W, H, px

def render_tilesheet(tile_data, cols=16, scale=4):
    n = len(tile_data) // 16
    if n == 0: return None
    rows = (n + cols - 1) // cols
    Wt, Ht = cols * 8, rows * 8
    W, H = Wt * scale, Ht * scale
    px = bytearray(W * H)
    for t in range(n):
        tx, ty = (t % cols) * 8 * scale, (t // cols) * 8 * scale
        for y in range(8):
            bp0 = tile_data[t*16 + y]
            bp1 = tile_data[t*16 + 8 + y]
            for x in range(8):
                b = 7 - x
                v = ((bp0 >> b) & 1) | (((bp1 >> b) & 1) << 1)
                # Write a scale x scale block
                base_x = tx + x * scale
                base_y = ty + y * scale
                for sy in range(scale):
                    row_off = (base_y + sy) * W + base_x
                    for sx in range(scale):
                        px[row_off + sx] = v
    return W, H, px

# ---------------- PNG writer (stdlib only) ----------------

def write_png(path, W, H, pixels, palette):
    def chunk(name, data):
        crc = zlib.crc32(name + data)
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', crc)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', W, H, 8, 3, 0, 0, 0)  # 8-bit, palette type
    plte = b''.join(bytes(p) for p in palette)
    raw = bytearray()
    for y in range(H):
        raw.append(0)  # filter byte: 0 = None
        raw += pixels[y*W:(y+1)*W]
    idat = zlib.compress(bytes(raw), 6)
    with open(path,'wb') as f:
        f.write(sig)
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'PLTE', plte))
        f.write(chunk(b'IDAT', idat))
        f.write(chunk(b'IEND', b''))

# 4-shade grayscale (NES 2bpp); colors 0..3 dark to light.
PALETTE = [(0, 0, 0), (85, 85, 85), (170, 170, 170), (255, 255, 255)]

# ---------------- Kirby ROM blob enumeration ----------------
# Same constants as the C++ repacker.

MAP_BANK_TBL, MAP_HI_TBL, MAP_LO_TBL, MAP_N = 0x244E1, 0x2476F, 0x248B6, 0x147
TILESET_BANK_TBL, TILESET_HI_TBL, TILESET_LO_TBL, TILESET_N = 0x249FD, 0x24A2E, 0x24A5F, 0x31
TABLE_AC28_LO, TABLE_AC28_HI, TABLE_AC28_BANK, TABLE_AC28_N = 0x68C38, 0x68C40, 52, 8
TABLE_B531_LO, TABLE_B531_HI, TABLE_B531_BANK, TABLE_B531_N = 0x27541, 0x2756E, 19, 45
INLINE_SITES = [
    (0x5C655, 0xA691), (0x5C67D, 0xA7BE), (0x6C7B4, 0xA85E), (0x6CDD0, 0xB10B),
    (0x7656E, 0xA881), (0x765C5, 0xA9C1), (0x77360, 0xBA82), (0x773B1, 0xBBCE),
    (0x781E9, 0xA2C2), (0x78204, 0xA7C9), (0x7823B, 0xA50B), (0x7A3AD, 0xAAE3),
    (0x7A3DC, 0xAB4D),
]

def file_off_for(bank, addr): return 0x10 + bank * 0x2000 + (addr - 0xA000)

def enumerate_blobs(rom):
    """Return list of (kind, idx, file_off, label) — skips MAP (not graphics)."""
    blobs = []
    # TILESETs
    for i in range(TILESET_N):
        b = rom[TILESET_BANK_TBL+i]; h = rom[TILESET_HI_TBL+i]; l = rom[TILESET_LO_TBL+i]
        addr = (h<<8)|l
        if b==0 and addr==0: continue
        if not (0xA000 <= addr <= 0xBFFF): continue
        bank = b & 0x7F
        off = file_off_for(bank, addr)
        blobs.append(('TILESET', i, off, f'tileset_{i:02d}'))
    # TABLE_AC28
    for i in range(TABLE_AC28_N):
        l = rom[TABLE_AC28_LO+i]; h = rom[TABLE_AC28_HI+i]
        addr = (h<<8)|l
        if not (0xA000 <= addr <= 0xBFFF): continue
        off = file_off_for(TABLE_AC28_BANK, addr)
        blobs.append(('TBL_AC28', i, off, f'tbl_ac28_{i:d}'))
    # TABLE_B531
    for y in range(1, TABLE_B531_N + 1):
        l = rom[TABLE_B531_LO+y]; h = rom[TABLE_B531_HI+y]
        addr = (h<<8)|l
        if not (0xA000 <= addr <= 0xBFFF): continue
        off = file_off_for(TABLE_B531_BANK, addr)
        blobs.append(('TBL_B531', y, off, f'tbl_b531_{y:02d}'))
    # INLINE
    for idx, (jsr, src_cpu) in enumerate(INLINE_SITES):
        bank = (jsr - 0x10) // 0x2000
        off = file_off_for(bank, src_cpu)
        blobs.append(('INLINE', idx, off, f'inline_{idx:02d}_at_{jsr:X}'))
    return blobs

# ---------------- Main ----------------

def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    rom_path = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) >= 3 else 'tilesheets'
    os.makedirs(out_dir, exist_ok=True)
    with open(rom_path,'rb') as f: rom = f.read()

    blobs = enumerate_blobs(rom)
    print(f'Found {len(blobs)} graphics-candidate blobs (TILESET / TBL_AC28 / TBL_B531 / INLINE).')
    print(f'Maps (327 entries) skipped — they are metatile/level data, not raw CHR.')
    print(f'Output dir: {out_dir}')

    # Sanity check: render a slice of Kirby's uncompressed CHR ROM as a known-good
    # comparison. CHR ROM follows PRG at file offset 0x80010 in our 512KB PRG / 256KB CHR layout.
    # First 4KB (256 tiles) of CHR is a great visual test.
    chr_off = 0x80010
    if chr_off + 4096 < len(rom):
        chr_slice = rom[chr_off:chr_off + 4096]  # 256 tiles
        result = render_tilesheet(chr_slice)
        if result:
            W, H, px = result
            write_png(os.path.join(out_dir, '_SANITY_chr_rom_first_4k.png'), W, H, px, PALETTE)
            print(f'(also rendered _SANITY_chr_rom_first_4k.png — uncompressed CHR ROM sample)')

    index_lines = ['# Kirby NES tilesheet dump']
    index_lines.append(f'# source ROM: {rom_path}')
    index_lines.append(f'# {"kind":<10} {"idx":<4} {"file_off":<10} {"dec_sz":<6} {"tiles":<6} {"png":<32} note')

    # CHR ROM lives after PRG: file offset 0x80010 + ... (for 512KB PRG)
    # Total CHR = 256KB = 0x40000 bytes. Each 4KB = one PPU pattern table.
    chr_rom = rom[0x80010:]
    n_chr_banks_4k = len(chr_rom) // 4096

    rendered = 0
    skipped = 0
    nametables_rendered = 0
    for kind, idx, file_off, label in blobs:
        dec = decompress(rom, file_off)
        if dec is None:
            note = 'DECOMPRESS FAIL'
            index_lines.append(f'  {kind:<10} {idx:<4} 0x{file_off:<8X} -     -     -                                {note}')
            skipped += 1
            continue

        # Phase 2a: if blob is a multiple of 1024 bytes, treat it as a sequence
        # of NES nametables and render each. We don't know which CHR bank is
        # active so we try several common ones and emit a variant per bank.
        # Bank 0 = first 4KB of CHR ROM ($80010..$81010 in the file).
        if len(dec) % 1024 == 0 and len(dec) >= 1024:
            n_screens = len(dec) // 1024
            for s in range(n_screens):
                nm = dec[s*1024:(s+1)*1024]
                # Try a few CHR banks: bank 0, 1, 2, 3, 8, 16. Save all that work.
                # (For a final dumper we'd pick the right one; for now show all
                # so user can identify which CHR bank pairs with which blob.)
                for bank_idx in [0, 1, 2, 4, 8, 16]:
                    if bank_idx >= n_chr_banks_4k: break
                    chr_off = bank_idx * 4096
                    result = render_nametable(nm, chr_rom, chr_offset=chr_off, scale=2)
                    if result is None: continue
                    W, H, px = result
                    png_name = f'{label}_screen{s}_chr{bank_idx}.png'
                    write_png(os.path.join(out_dir, png_name), W, H, px, PALETTE)
                    rendered += 1
                    nametables_rendered += 1
            index_lines.append(f'  {kind:<10} {idx:<4} 0x{file_off:<8X} {len(dec):<5d} -     (nametable view, {n_screens} screen(s) x 6 CHR banks)')
            continue

        # Otherwise try tile-sheet view if size is multiple of 16
        if len(dec) >= 16 and len(dec) % 16 == 0:
            result = render_tilesheet(dec)
            if result:
                W, H, px = result
                n_tiles = len(dec) // 16
                png_name = f'{label}.png'
                write_png(os.path.join(out_dir, png_name), W, H, px, PALETTE)
                index_lines.append(f'  {kind:<10} {idx:<4} 0x{file_off:<8X} {len(dec):<5d} {n_tiles:<5d} {png_name:<32}')
                rendered += 1
                continue

        index_lines.append(f'  {kind:<10} {idx:<4} 0x{file_off:<8X} {len(dec):<5d} -     (size not 16- or 1024-aligned; skipped)')
        skipped += 1

    with open(os.path.join(out_dir, 'INDEX.txt'), 'w') as f:
        f.write('\n'.join(index_lines) + '\n')
    print(f'\nRendered: {rendered} PNGs ({nametables_rendered} nametable variants, '
          f'{rendered - nametables_rendered} other)')
    print(f'Skipped:  {skipped}')
    print(f'See {out_dir}/INDEX.txt for the manifest.')

if __name__ == '__main__':
    main()
