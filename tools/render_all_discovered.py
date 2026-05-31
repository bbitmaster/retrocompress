#!/usr/bin/env python3
"""Render every discovered anim table — full sprite gallery of the
metasprite chunks.

For each table from anim_tables_discovered.json:
  - Render the first 4 entries
  - Use the format detected by scanner (plain or R0-override)
  - Default R0=$80, R1=$9A (stage-1 globals), stage-1 palette
  - For R0-override tables, byte 0 of the metasprite IS the R0 override

Output:
  galleries/all_sprites/<chunk>_<addr>_<idx>.png
  galleries/all_sprites/index.html  (organized by chunk)
"""
import json
import os
import sys

sys.path.insert(0, 'tools')
import render_metasprite as RM
from dump_metasprite import decode

ROM = '../reference/rom/kirby.nes'
OUT = 'galleries/all_sprites'
os.makedirs(OUT, exist_ok=True)

with open(ROM, 'rb') as f:
    rom = f.read()
chr_rom = rom[16 + 0x80000 : 16 + 0xC0000]

with open('docs/anim_tables_discovered.json') as f:
    discovered = json.load(f)

STAGE1_PALETTE = bytes.fromhex(
    '2137202A212A1909212A1A0F213727072135250F213037172136260F2120250F')


def file_of(chunk, addr):
    if 0x8000 <= addr < 0xA000:
        return 0x10 + chunk * 0x2000 + (addr - 0x8000)
    if 0xA000 <= addr < 0xC000:
        return 0x10 + chunk * 0x2000 + (addr - 0xA000)
    return None


def render_pose(chunk, ms_addr, r0_override):
    fo = file_of(chunk, ms_addr)
    if fo is None:
        return None
    if r0_override:
        r0 = rom[fo]
        ms_start = fo + 1
    else:
        r0 = 0x80
        ms_start = fo
    if ms_start >= len(rom):
        return None
    count = rom[ms_start]
    if not (1 <= count <= 16):
        return None
    _, entries = decode(rom, ms_start)
    if not entries:
        return None
    chr_map = RM.load_sprite_chr(chr_rom, r0, 0xD8)
    res = RM.render_metasprite(chr_map, STAGE1_PALETTE, entries,
                                sprite_size=16, normalize_facing=False)
    if not res:
        return None
    rgba, w, h, _, _ = res
    # Skip tiny / empty
    if w < 4 or h < 4:
        return None
    rgba = RM.background_fill(rgba, w, h, (80, 80, 96, 255))
    return RM.scale_rgba(rgba, w, h, 2) + (r0,)  # rgba, w, h, r0


total_rendered = 0
sections = []
for chunk_s, tables in discovered['tables_by_chunk'].items():
    chunk = int(chunk_s.lstrip('$'), 16)
    section_renders = []
    for t in tables:
        anim_addr = int(t['anim_addr'].lstrip('$'), 16)
        anim_fo = file_of(chunk, anim_addr)
        if anim_fo is None:
            continue
        r0_ovr = (t['first_kind'] == 'r0_ovr')
        table_renders = []
        # Read first 4 entries of this table and render
        for idx in range(4):
            lo = rom[anim_fo + idx*2]
            hi = rom[anim_fo + idx*2 + 1]
            ptr = (hi << 8) | lo
            if not (0x8000 <= ptr < 0xC000):
                break
            r = render_pose(chunk, ptr, r0_ovr)
            if r is None:
                continue
            rgba, w, h, r0 = r
            out_name = f'{chunk:02X}_{anim_addr:04X}_{idx}_{ptr:04X}.png'
            out_path = f'{OUT}/{out_name}'
            with open(out_path, 'wb') as f:
                f.write(RM.png_from_rgba(bytes(rgba), w, h))
            table_renders.append({
                'idx': idx, 'ptr': ptr, 'r0': r0, 'image': out_name,
            })
            total_rendered += 1
        if table_renders:
            section_renders.append({
                'anim_addr': anim_addr,
                'r0_ovr': r0_ovr,
                'table_len': t['len'],
                'poses': table_renders,
            })
    if section_renders:
        sections.append({'chunk': chunk, 'tables': section_renders})

# Build HTML index
parts = ['''<!doctype html>
<html><head><meta charset="utf-8"><title>Kirby NES — All Discovered Sprites</title>
<style>
body { background:#1a1a1a; color:#ddd; font-family: ui-monospace, monospace; padding:1em; max-width:1500px; margin:0 auto; }
h1 { color:#fc9; }
h2 { color:#9cf; margin-top:1.5em; border-top: 2px solid #555; padding-top: 1em; }
.tab { background:#2a2a2a; padding:8px; margin:6px 0; border:1px solid #444; }
.tab .head { color:#aaa; font-size:0.85em; margin-bottom:4px; }
.row { display:flex; gap:8px; flex-wrap:wrap; align-items:flex-end; }
.cell { background:#333; padding:4px; border:1px solid #555; text-align:center; }
.cell img { image-rendering: pixelated; image-rendering: crisp-edges; display:block; }
.cell .lbl { font-size:0.65em; color:#888; margin-top:2px; }
nav { background:#2a2a2a; padding:8px; position:sticky; top:0; z-index:10; }
nav a { color:#9cf; padding:0 6px; text-decoration:none; }
</style></head><body>
<h1>All discovered sprites</h1>
<nav>jump to chunk: ''']
for s in sections:
    parts.append(f'<a href="#c{s["chunk"]:02X}">${s["chunk"]:02X}</a>')
parts.append(f'</nav>\n<p style="color:#888">{total_rendered} sprites across {sum(len(s["tables"]) for s in sections)} anim tables in {len(sections)} chunks. R0/R1=$80/$9A (stage-1) for plain blits; R0-override tables use the byte from the metasprite.</p>\n')
for s in sections:
    parts.append(f'<h2 id="c{s["chunk"]:02X}">chunk ${s["chunk"]:02X} ({len(s["tables"])} tables)</h2>\n')
    for t in s['tables']:
        kind = 'R0-override' if t['r0_ovr'] else 'plain'
        parts.append(f'<div class="tab"><div class="head">${s["chunk"]:02X}:${t["anim_addr"]:04X} &nbsp; '
                     f'len={t["table_len"]} &nbsp; {kind}</div><div class="row">\n')
        for p in t['poses']:
            parts.append(f'  <div class="cell"><img src="{p["image"]}">'
                         f'<div class="lbl">[{p["idx"]}] ${p["ptr"]:04X}'
                         f'{" R0=$"+format(p["r0"], "02X") if t["r0_ovr"] else ""}'
                         f'</div></div>\n')
        parts.append('</div></div>\n')
parts.append('</body></html>')
with open(f'{OUT}/index.html', 'w') as f:
    f.write(''.join(parts))
print(f'Rendered {total_rendered} sprites across {sum(len(s["tables"]) for s in sections)} tables / {len(sections)} chunks')
print(f'Open {OUT}/index.html')
