#!/usr/bin/env python3
"""For each metasprite chunk, render its first anim table's first
metasprite with all 18 candidate R1 values from the per-map header[4]
set. Lets us visually pick the right R1 per chunk.

Output:
  galleries/chunk_r1_picker/chunk_XX.png  (18-cell contact sheet)
  galleries/chunk_r1_picker/index.html
"""
import json
import os
import sys

sys.path.insert(0, 'tools')
import render_metasprite as RM
from dump_metasprite import decode

ROM = '../reference/rom/kirby.nes'
OUT = 'galleries/chunk_r1_picker'
os.makedirs(OUT, exist_ok=True)

with open(ROM, 'rb') as f:
    rom = f.read()
chr_rom = rom[16 + 0x80000 : 16 + 0xC0000]
with open('docs/anim_tables_discovered.json') as f:
    discovered = json.load(f)

R1_BANKS = [0x3C, 0x9A, 0x9C, 0x9E, 0xA0, 0xA2, 0xA8, 0xAA, 0xAC, 0xAE,
            0xC4, 0xCA, 0xCC, 0xD2, 0xD6, 0xD8, 0xE8, 0xEC, 0xEE]
STAGE1_PALETTE = bytes.fromhex(
    '2137202A212A1909212A1A0F213727072135250F213037172136260F2120250F')


def file_of(chunk, addr):
    if 0x8000 <= addr < 0xA000:
        return 0x10 + chunk * 0x2000 + (addr - 0x8000)
    if 0xA000 <= addr < 0xC000:
        return 0x10 + chunk * 0x2000 + (addr - 0xA000)
    return None


# Pick FIRST table of each chunk that has at least 4 entries and looks good
def pick_table(chunk_s):
    for t in discovered['tables_by_chunk'][chunk_s]:
        if t['len'] >= 4:
            return t
    return discovered['tables_by_chunk'][chunk_s][0] if discovered['tables_by_chunk'][chunk_s] else None


parts = ['''<!doctype html>
<html><head><meta charset="utf-8"><title>Per-chunk R1 picker</title>
<style>
body { background:#1a1a1a; color:#ddd; font-family: ui-monospace, monospace; padding:1em; max-width:1400px; margin:0 auto; }
h1 { color:#fc9; } h2 { color:#9cf; margin-top:1.5em; }
.row { display:flex; gap:8px; flex-wrap:wrap; margin:6px 0; }
.cell { background:#333; padding:4px; border:1px solid #555; text-align:center; }
.cell img { image-rendering: pixelated; image-rendering: crisp-edges; display:block; }
.cell .lbl { font-size:0.8em; color:#aaa; margin-top:2px; }
.cell.picked { border-color:#5a5; background:#363; }
</style></head><body>
<h1>Per-chunk R1 picker</h1>
<p style="color:#888">For each metasprite chunk, the first metasprite of its first anim table rendered with all 18 R1 sprite-CHR banks observed across the game maps. Click the cell that looks "right" — most chunks will have exactly one R1 where the sprite is coherent.</p>
''']

for chunk_s in sorted(discovered['tables_by_chunk'].keys()):
    chunk = int(chunk_s.lstrip('$'), 16)
    t = pick_table(chunk_s)
    if t is None:
        continue
    anim_addr = int(t['anim_addr'].lstrip('$'), 16)
    anim_fo = file_of(chunk, anim_addr)
    # First entry's pointer + first metasprite's bytes
    lo = rom[anim_fo]; hi = rom[anim_fo + 1]
    ptr = (hi << 8) | lo
    ms_fo = file_of(chunk, ptr)
    r0_ovr = (t['first_kind'] == 'r0_ovr')
    if r0_ovr:
        r0 = rom[ms_fo]
        ms_start = ms_fo + 1
    else:
        r0 = 0x80
        ms_start = ms_fo
    count, entries = decode(rom, ms_start)
    if not (1 <= count <= 16):
        continue
    parts.append(f'<h2 id="c{chunk:02X}">chunk ${chunk:02X} '
                 f'<small style="color:#888">first metasprite from {chunk_s}:{t["anim_addr"]} '
                 f'({"r0_ovr R0=$"+format(r0,"02X") if r0_ovr else "plain"})</small></h2>\n<div class="row">\n')
    for r1 in R1_BANKS:
        chr_map = RM.load_sprite_chr(chr_rom, r0, r1)
        res = RM.render_metasprite(chr_map, STAGE1_PALETTE, entries,
                                    sprite_size=16, normalize_facing=False)
        if not res:
            continue
        rgba, w, h, _, _ = res
        rgba = RM.background_fill(rgba, w, h, (80, 80, 96, 255))
        rgba, w, h = RM.scale_rgba(rgba, w, h, 2)
        out_name = f'chunk_{chunk:02X}_r1_{r1:02X}.png'
        out_path = f'{OUT}/{out_name}'
        with open(out_path, 'wb') as f:
            f.write(RM.png_from_rgba(bytes(rgba), w, h))
        parts.append(f'  <div class="cell"><img src="{out_name}">'
                     f'<div class="lbl">R1=${r1:02X}</div></div>\n')
    parts.append('</div>\n')

parts.append('</body></html>')
with open(f'{OUT}/index.html', 'w') as f:
    f.write(''.join(parts))
print(f'Wrote chunk R1 picker — open {OUT}/index.html')
