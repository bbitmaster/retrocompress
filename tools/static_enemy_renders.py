#!/usr/bin/env python3
"""Static clean-portrait renderer for each enemy in the catalog.

Per spawn ID:
  - Look up the discovered anim table (from enemy_anim_bruteforce.json)
  - PRG chunk for metasprite reads = anim_bank
  - Walk first ~12 anim-table entries to find ones whose count looks
    sane (1..16). Render each as a "candidate pose."
  - Try BOTH plain blit ($DA89) and R0-override ($DDA3) interpretations
    — show whichever produces a reasonable result.

R0/R1 defaults: stage-1 globals ($80 / $9A). For R0-override entries,
byte 0 of the metasprite replaces R0.

Output:
  galleries/static_enemies/id_XX.png  — top 8 candidate poses
  galleries/static_enemies/index.html
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, 'tools')
import render_metasprite as RM
from dump_metasprite import decode

ROM = '../reference/rom/kirby.nes'
OUT = 'galleries/static_enemies'
os.makedirs(OUT, exist_ok=True)

with open(ROM, 'rb') as f:
    rom = f.read()
chr_rom = rom[16 + 0x80000 : 16 + 0xC0000]

with open('docs/enemy_anims_verified.json') as f:
    verified_data = json.load(f)
# Only use verified or corrected anims
enemies = []
for v in verified_data['enemies']:
    if v['verification'] == 'false_positive':
        continue
    if v['verification'] == 'near_miss':
        # Corrected — overwrite anim_lo/anim_hi from verified_addr
        addr = v['verified_addr']
        v['anim_lo'] = addr & 0xFF
        v['anim_hi'] = (addr >> 8) & 0xFF
    v['found'] = True
    enemies.append(v)

DEFAULT_R0 = 0x80
DEFAULT_R1 = 0xD8
STAGE1_PALETTE = bytes.fromhex(
    '2137202A212A1909212A1A0F213727072135250F213037172136260F2120250F')


def file_of(chunk, addr):
    if 0x8000 <= addr < 0xA000:
        return 0x10 + chunk * 0x2000 + (addr - 0x8000)
    if 0xA000 <= addr < 0xC000:
        return 0x10 + chunk * 0x2000 + (addr - 0xA000)
    return None


def is_clean_render(rgba, w, h):
    """Heuristic: 'clean' if at least one tile renders content but
    we're not just a tiny dot or oversized garbage."""
    # Count non-transparent pixels (alpha > 0)
    visible = 0
    for i in range(0, len(rgba), 4):
        if rgba[i+3] > 0:
            visible += 1
    return 16 <= visible <= 4096


def try_render(chunk, ms_addr, r0_override=False):
    """Render one metasprite, returning (rgba, w, h, r0) or None.

    Validates:
      - count in 1..16
      - all dx (interpreted signed) in -64..64
      - all dy (signed) in -64..64
      - bounding-box width and height each <= 128 px
    These reject the "garbage metasprite" case where the address isn't
    actually a metasprite (count byte happens to be plausible but the
    following bytes are unrelated data).
    """
    fo = file_of(chunk, ms_addr)
    if fo is None:
        return None
    if r0_override:
        r0 = rom[fo]
        ms_start = fo + 1
        if not (0x80 <= r0 < 0xFF):
            return None
    else:
        r0 = DEFAULT_R0
        ms_start = fo
    if ms_start + 1 >= len(rom):
        return None
    count = rom[ms_start]
    if not (1 <= count <= 16):
        return None
    _, entries = decode(rom, ms_start)
    if not entries:
        return None
    # Validate dx/dy: signed bytes, all within +/-64
    for dx, dy, _tile, _attr in entries:
        sdx = dx - 256 if dx >= 128 else dx
        sdy = dy - 256 if dy >= 128 else dy
        if not (-64 <= sdx <= 64) or not (-64 <= sdy <= 64):
            return None
    # Bounding-box sanity (<= 128 px on each axis)
    dxs = [dx - 256 if dx >= 128 else dx for dx, _, _, _ in entries]
    dys = [dy - 256 if dy >= 128 else dy for _, dy, _, _ in entries]
    if (max(dxs) - min(dxs)) > 120 or (max(dys) - min(dys)) > 120:
        return None
    chr_map = RM.load_sprite_chr(chr_rom, r0, DEFAULT_R1)
    res = RM.render_metasprite(chr_map, STAGE1_PALETTE, entries,
                                sprite_size=16, normalize_facing=False)
    if not res:
        return None
    rgba, w, h, _, _ = res
    if not is_clean_render(rgba, w, h):
        return None
    rgba = RM.background_fill(rgba, w, h, (80, 80, 96, 255))
    return rgba, w, h, r0


# Spawn IDs known to use R0-override variant (from trace observation).
# Also set when the verified anim table's first_kind = 'r0_ovr'.
R0_OVERRIDE_IDS = {0x01}  # Kirby

results = []
for e in enemies:
    sid = e['spawn_id']
    if not e.get('found'):
        continue
    chunk = e['anim_bank']
    anim_addr = (e['anim_hi'] << 8) | e['anim_lo']
    anim_fo = file_of(chunk, anim_addr)
    if anim_fo is None:
        continue
    # Use R0-override variant if either the spawn ID is known to need it,
    # OR the verified scan said the table's first metasprite is R0-override
    use_r0_ovr = (sid in R0_OVERRIDE_IDS
                  or e.get('first_kind') == 'r0_ovr')

    # Walk first 16 entries of anim table, render each
    rendered = []
    for idx in range(16):
        if anim_fo + idx*2 + 1 >= len(rom):
            break
        lo = rom[anim_fo + idx*2]
        hi = rom[anim_fo + idx*2 + 1]
        ms_addr = (hi << 8) | lo
        if not (0x8000 <= ms_addr < 0xC000):
            break
        # Try preferred variant first; fall back to the other
        r = try_render(chunk, ms_addr, r0_override=use_r0_ovr)
        if r is None and not use_r0_ovr:
            r = try_render(chunk, ms_addr, r0_override=True)
        if r is None:
            continue
        rgba, w, h, r0 = r
        rgba_scaled, fw, fh = RM.scale_rgba(rgba, w, h, 3)
        # Save (cap at 8 per id)
        if len(rendered) < 8:
            out_name = f'id_{sid:02X}_pose_{len(rendered):02d}_{ms_addr:04X}.png'
            out_path = f'{OUT}/{out_name}'
            with open(out_path, 'wb') as f:
                f.write(RM.png_from_rgba(bytes(rgba_scaled), fw, fh))
            rendered.append({'image': out_name, 'ms_addr': ms_addr, 'r0': r0})
    if rendered:
        results.append({'sid': sid, 'chunk': chunk,
                        'anim_addr': anim_addr,
                        'use_r0_ovr': use_r0_ovr,
                        'renders': rendered})
        print(f'  id ${sid:02X} (${chunk:02X}:${anim_addr:04X}, '
              f'{"R0-ovr" if use_r0_ovr else "plain"}): '
              f'{len(rendered)} pose(s)')


# HTML index
parts = ['''<!doctype html>
<html><head><meta charset="utf-8"><title>Static Enemy Renders</title>
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
<h1>Static enemy renders (no trace required)</h1>
<p class="meta">Each enemy's top anim-table entries rendered with the
correct PRG chunk and default R0/R1 (stage-1 globals). Kirby (id $01)
forced to R0-override variant.</p>
''']
for r in results:
    variant = 'R0-override ($DDA3)' if r['use_r0_ovr'] else 'plain blit ($DA89)'
    parts.append(f'<h2>id ${r["sid"]:02X}</h2>\n<div class="meta">anim ${r["chunk"]:02X}:${r["anim_addr"]:04X} &nbsp; variant: {variant}</div>\n<div class="grid">\n')
    for pose in r['renders']:
        parts.append(
            f'  <div class="cell"><img src="{pose["image"]}">'
            f'<div class="label">${pose["ms_addr"]:04X}<br>R0=${pose["r0"]:02X}</div>'
            f'</div>\n')
    parts.append('</div>\n')
parts.append('</body></html>')
with open(f'{OUT}/index.html', 'w') as f:
    f.write(''.join(parts))
print(f'\nWrote {len(results)} enemies + index.html')
