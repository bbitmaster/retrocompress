#!/usr/bin/env python3
"""Render Kirby's Adventure first room from FCEUX trace + ROM.

Walks an FCEUX trace log, snapshots VRAM nametable state at a chosen
line (just after the map-load decomp at line ~2281788 in the canonical
trace), combines that with CHR ROM banks active at the same instant and
the recorded palette, and writes a 256x240 RGB PNG via stdlib zlib.

Usage:
    render_first_room.py <rom.nes> <trace.log> <out.png> [--snapshot LINE]
"""
import re, sys, zlib, struct, argparse

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

# Parse register state and opcode mnemonic from a trace line. Returns
# (a, x, y, opcode_3char) or None if not a normal instruction line.
RE_LINE = re.compile(
    r'^A:([0-9A-F]{2}) X:([0-9A-F]{2}) Y:([0-9A-F]{2}) S:[0-9A-F]{2} P:[A-Za-z]+\s+'
    r'\$[0-9A-F]{2}:[0-9A-F]{4}: (?:[0-9A-F]{2} ?){1,3}\s+([A-Z]{3})\s'
)

def parse_line(line):
    m = RE_LINE.match(line)
    if not m: return None
    return int(m.group(1),16), int(m.group(2),16), int(m.group(3),16), m.group(4)

def value_from_opcode(a, x, y, opc):
    if opc == 'STA': return a
    if opc == 'STX': return x
    if opc == 'STY': return y
    return None

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

def build_chr_window(snap, chr_rom):
    R = snap['mmc3']
    chr_map = bytearray(0x2000)
    chr_map[0x0000:0x0800] = chr_rom[chr_bank_offset(R[0],2):chr_bank_offset(R[0],2)+0x800]
    chr_map[0x0800:0x1000] = chr_rom[chr_bank_offset(R[1],2):chr_bank_offset(R[1],2)+0x800]
    chr_map[0x1000:0x1400] = chr_rom[chr_bank_offset(R[2],1):chr_bank_offset(R[2],1)+0x400]
    chr_map[0x1400:0x1800] = chr_rom[chr_bank_offset(R[3],1):chr_bank_offset(R[3],1)+0x400]
    chr_map[0x1800:0x1C00] = chr_rom[chr_bank_offset(R[4],1):chr_bank_offset(R[4],1)+0x400]
    chr_map[0x1C00:0x2000] = chr_rom[chr_bank_offset(R[5],1):chr_bank_offset(R[5],1)+0x400]
    return chr_map

def render_nametable(nt, chr_map, palette, pt_base):
    tiles = nt[:960]
    attrs = nt[960:]
    W, H = 256, 240
    img = bytearray(W*H*3)
    for ty in range(30):
        for tx in range(32):
            tile_idx = tiles[ty*32 + tx]
            attr = attrs[(ty//4)*8 + (tx//4)]
            qx = (tx & 2) >> 1
            qy = (ty & 2) >> 1
            shift = (qy*2 + qx) * 2
            pal_idx = (attr >> shift) & 0x03
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
    return bytes(img)

def hstack_rgb(left, right, w, h):
    """Stack two w*h*3 RGB images side-by-side to a (w*2)*h image."""
    out = bytearray(w*2*h*3)
    for y in range(h):
        row_l = left[y*w*3:(y+1)*w*3]
        row_r = right[y*w*3:(y+1)*w*3]
        out[y*w*2*3:y*w*2*3 + w*3] = row_l
        out[y*w*2*3 + w*3:(y+1)*w*2*3] = row_r
    return bytes(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom')
    ap.add_argument('trace')
    ap.add_argument('out')
    ap.add_argument('--snapshot', type=int, default=2350000,
                    help='Trace line to snapshot VRAM (default: 2.35M, just after map-load decomp)')
    ap.add_argument('--nt-base', type=lambda x:int(x,0), default=0x2000)
    ap.add_argument('--diag', action='store_true', help='Print VRAM-write stats periodically')
    args = ap.parse_args()

    with open(args.rom, 'rb') as f:
        rom = f.read()
    chr_rom_offset = 16 + 0x80000
    chr_rom = rom[chr_rom_offset:chr_rom_offset + 0x40000]
    assert len(chr_rom) == 0x40000

    mmc3_select = 0
    mmc3_banks = [0]*8
    ppu_addr = 0
    ppu_phase = 0
    vram = bytearray(0x1000)
    palette = bytearray(32)
    vram_writes = 0
    palette_writes = 0
    write_count_by_nt = [0,0,0,0]

    print(f'Walking trace {args.trace} ...')
    with open(args.trace) as f:
        for ln, line in enumerate(f, 1):
            if ln >= args.snapshot:
                break
            parsed = parse_line(line)
            if not parsed: continue
            a, x, y, opc = parsed
            if opc not in ('STA','STX','STY'): continue
            v = value_from_opcode(a, x, y, opc)
            # Check what address this opcode is hitting. Read the suffix.
            if '$8000 ' in line:
                mmc3_select = v & 0x07
            elif '$8001 ' in line:
                mmc3_banks[mmc3_select] = v
            elif '$2006 ' in line:
                if ppu_phase == 0:
                    ppu_addr = (v << 8) & 0xFF00
                    ppu_phase = 1
                else:
                    ppu_addr |= v
                    ppu_phase = 0
            elif '$2007 ' in line:
                a_addr = ppu_addr & 0x3FFF
                if 0x2000 <= a_addr < 0x3000:
                    vram[a_addr - 0x2000] = v
                    vram_writes += 1
                    nt_idx = (a_addr - 0x2000) >> 10
                    write_count_by_nt[nt_idx] += 1
                elif 0x3F00 <= a_addr < 0x3F20:
                    palette[a_addr - 0x3F00] = v
                    palette_writes += 1
                # PPUCTRL bit 2 controls increment; assume +1 (the common case)
                ppu_addr = (ppu_addr + 1) & 0x3FFF
            if args.diag and ln % 500000 == 0:
                print(f'  line {ln}: {vram_writes} vram writes, NT counts={write_count_by_nt}')

    print(f'Snapshot @ line {args.snapshot}:')
    print(f'  MMC3 R0..R7: {" ".join(f"${b:02X}" for b in mmc3_banks)}')
    print(f'  Palette: {" ".join(f"{b:02X}" for b in palette)}')
    print(f'  Total vram writes so far: {vram_writes}, palette writes: {palette_writes}')
    print(f'  Writes per nametable region: NT0={write_count_by_nt[0]}, NT1={write_count_by_nt[1]}, NT2={write_count_by_nt[2]}, NT3={write_count_by_nt[3]}')

    snap = {'mmc3': list(mmc3_banks), 'palette': bytes(palette)}
    chr_map = build_chr_window(snap, chr_rom)

    nt0 = vram[0x000:0x400]
    nt1 = vram[0x400:0x800]
    nt2 = vram[0x800:0xC00]
    nt3 = vram[0xC00:0x1000]
    print(f'  NT0 non-zero: {sum(1 for b in nt0 if b)}/1024')
    print(f'  NT1 non-zero: {sum(1 for b in nt1 if b)}/1024')
    print(f'  NT2 non-zero: {sum(1 for b in nt2 if b)}/1024')
    print(f'  NT3 non-zero: {sum(1 for b in nt3 if b)}/1024')

    # Kirby uses MMC3 IRQ split: NT0 = HUD (top of screen), NT2 = playfield.
    # Mirroring = horizontal ($A000 bit 0 = 1), so NT0/NT1 share and NT2/NT3
    # share. Render each populated nametable; main playfield is NT2.
    for nt_name, nt_data in (('nt0_hud', nt0), ('nt2_playfield', nt2)):
        if sum(1 for b in nt_data if b) == 0:
            continue
        for pt_base, suffix in ((0x1000, ''), (0x0000, '_pt0000')):
            img = render_nametable(nt_data, chr_map, palette, pt_base)
            base, _, ext = args.out.rpartition('.')
            stem = base if base else args.out
            out_name = f'{stem}_{nt_name}{suffix}.{ext if base else "png"}'
            with open(out_name, 'wb') as f:
                f.write(png_from_rgb(img, 256, 240))
            print(f'  Wrote {out_name}')

    nt_dump = args.out + '.vram.bin'
    with open(nt_dump, 'wb') as f:
        f.write(vram)
    print(f'  Wrote {nt_dump} (4KB full VRAM dump)')

if __name__ == '__main__':
    main()
