#!/usr/bin/env python3
"""Render each slot's top metasprites using the ground-truth context
captured from the stage-1 trace.

For each slot, we know:
  - The PRG chunk loaded at $8000-$9FFF when the metasprite was read
  - The metasprite address ($8000-$9FFF range)
  - The blit variant (plain or R0-override)

For R0-override variants ($DDA3, $DDB3): byte 0 of the metasprite is
loaded into $42 (= R0 sprite-CHR bank). The remaining bytes start
with the count at byte 1.

R0/R1 baseline: stage-1 globals are R0=$80, R1=$9A (the values we used
as defaults). For Kirby (R0-override path), the override REPLACES R0;
R1 stays at $9A.

Output:
  galleries/trace_renders/slot_XX_pose_N.png
  galleries/trace_renders/index.html
"""
import json
import os
import subprocess
import sys
import struct
import zlib
from collections import defaultdict

# Reuse render_metasprite as a module
sys.path.insert(0, 'tools')
import render_metasprite as RM
from dump_metasprite import decode

ROM = '../reference/rom/kirby.nes'
OUT = 'galleries/trace_renders'
os.makedirs(OUT, exist_ok=True)

with open(ROM, 'rb') as f:
    rom = f.read()
with open('docs/trace_draws.json') as f:
    trace = json.load(f)

chr_rom = rom[16 + 0x80000 : 16 + 0xC0000]

DEFAULT_R1 = 0x9A  # stage-1 R1 sprite-CHR
STAGE1_PALETTE = bytes.fromhex(
    '2137202A212A1909212A1A0F213727072135250F213037172136260F2120250F')


def file_of(chunk, addr):
    """Get ROM file offset for chunk at $8000-$9FFF address."""
    if 0x8000 <= addr < 0xA000:
        return 0x10 + chunk * 0x2000 + (addr - 0x8000)
    if 0xA000 <= addr < 0xC000:
        return 0x10 + chunk * 0x2000 + (addr - 0xA000)
    return None


def render_one(chunk, addr, variant, scale=3, palette=STAGE1_PALETTE):
    """Render one metasprite. Returns (rgba, w, h) or None.

    For R0-override variants, byte 0 of the metasprite is the new R0.
    The count then starts at byte 1, and entries are byte-offset by 1
    from the plain-blit format.
    """
    fo = file_of(chunk, addr)
    if fo is None:
        return None
    if variant.startswith('r0_ovr'):
        r0 = rom[fo]
        ms_start = fo + 1
        chr_map = RM.load_sprite_chr(chr_rom, r0, DEFAULT_R1)
    elif variant.startswith('r1_ovr'):
        r1 = rom[fo]
        ms_start = fo + 1
        chr_map = RM.load_sprite_chr(chr_rom, 0x80, r1)
    else:
        ms_start = fo
        chr_map = RM.load_sprite_chr(chr_rom, 0x80, DEFAULT_R1)
    count, entries = decode(rom, ms_start)
    if count == 0 or count > 30:
        return None
    res = RM.render_metasprite(chr_map, palette, entries, sprite_size=16,
                                normalize_facing=False)
    if not res:
        return None
    rgba, w, h, _, _ = res
    rgba = RM.background_fill(rgba, w, h, (80, 80, 96, 255))
    rgba_scaled, fw, fh = RM.scale_rgba(rgba, w, h, scale)
    return rgba_scaled, fw, fh


# Group trace events by slot. For each slot, pick the TOP 12
# most-drawn metasprites (these are likely the character's main poses).
by_slot = defaultdict(list)
for t in trace['top']:
    slot = t['slot']
    if slot is None or t['r6'] is None:
        continue
    by_slot[slot].append(t)

# Sort each slot's metasprites by count desc, keep top 12
for s in by_slot:
    by_slot[s].sort(key=lambda r: -r['count'])
    by_slot[s] = by_slot[s][:12]


# Render
index_rows = []
for slot in sorted(by_slot.keys()):
    entries = by_slot[slot]
    rendered = []
    for t in entries:
        out_name = f'slot_{slot:02X}_{t["r6"]:02X}_{t["ms_addr"]:04X}.png'
        out_path = f'{OUT}/{out_name}'
        try:
            res = render_one(t['r6'], t['ms_addr'], t['variant'])
        except Exception as e:
            print(f'  slot ${slot:02X} ${t["r6"]:02X}:${t["ms_addr"]:04X}: ERR {e}')
            continue
        if res is None:
            continue
        rgba, w, h = res
        with open(out_path, 'wb') as f:
            f.write(RM.png_from_rgba(bytes(rgba), w, h))
        rendered.append({
            'image': out_name, 'count': t['count'],
            'chunk': t['r6'], 'addr': t['ms_addr'],
            'variant': t['variant'],
        })
    if rendered:
        print(f'  slot ${slot:02X}: {len(rendered)} pose(s) rendered')
        index_rows.append({'slot': slot, 'renders': rendered})

# Build HTML index
parts = ['''<!doctype html>
<html><head><meta charset="utf-8"><title>Kirby NES — Trace-Driven Renders</title>
<style>
body { background:#222; color:#ddd; font-family: ui-monospace, monospace; padding:1em; max-width:1400px; margin:0 auto; }
h1 { color:#fc9; }
h2 { color:#9cf; margin-top:1.5em; border-top: 1px solid #444; padding-top: 1em; }
.meta { color:#888; font-size:0.85em; }
.grid { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
.cell { background:#333; padding:6px; border:1px solid #555; text-align:center; }
.cell img { image-rendering: pixelated; image-rendering: crisp-edges; display:block; }
.cell .label { font-size:0.75em; color:#aaa; margin-top:4px; }
</style></head><body>
<h1>Trace-driven render gallery</h1>
<p class="meta">Each slot shows the metasprites the engine actually drew in stage 1, in order of draw count. Each pose is rendered with the correct PRG chunk loaded at $8000-$9FFF and the right R0 override (for $DDA3 R0-override-variant blits).</p>
''']
for row in index_rows:
    parts.append(f'<h2>slot ${row["slot"]:02X}</h2>\n<div class="grid">\n')
    for r in row['renders']:
        parts.append(
            f'  <div class="cell"><img src="{r["image"]}">'
            f'<div class="label">${r["chunk"]:02X}:${r["addr"]:04X}<br>x{r["count"]}</div>'
            f'</div>\n')
    parts.append('</div>\n')
parts.append('</body></html>')
with open(f'{OUT}/index.html', 'w') as f:
    f.write(''.join(parts))
print(f'Wrote {len(index_rows)} slots + index.html')
