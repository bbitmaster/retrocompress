#!/usr/bin/env python3
"""One sprite sheet per spawnable enemy — render its full anim table.

Output:
  galleries/enemy_sheets/id_XX.png    — sprite sheet
  galleries/enemy_sheets/index.html   — simple gallery, one row per enemy
"""
import json, os, subprocess

OUT = 'galleries/enemy_sheets'
os.makedirs(OUT, exist_ok=True)

with open('docs/enemy_anim_tables.json') as f:
    enemies_strict = json.load(f)
with open('docs/enemy_anim_bruteforce.json') as f:
    enemies_bf = json.load(f)

# Merge: prefer strict (script_scan_anim found by walking bytecode)
# if available, otherwise use brute-force result.
by_sid = {}
for e in enemies_strict:
    if e.get('anim_bank') is not None:
        by_sid[e['spawn_id']] = e
for e in enemies_bf:
    if e['spawn_id'] in by_sid:
        continue
    if e.get('found'):
        by_sid[e['spawn_id']] = {
            'spawn_id': e['spawn_id'],
            'script_bank': e['script_bank'],
            'script_pc': e['script_pc'],
            'anim_lo': e['anim_lo'],
            'anim_hi': e['anim_hi'],
            'anim_bank': e['anim_bank'],
        }
# Known false-positive anim tables that hang the renderer —
# brute-force matched a $1A byte inside another opcode's operand.
SKIP_IDS = {0x01, 0x04, 0x09, 0x60}
enemies = [by_sid[k] for k in sorted(by_sid.keys()) if k not in SKIP_IDS]

rom = open('../reference/rom/kirby.nes', 'rb').read()

def needs_r1(e):
    """Return True if any tile in the anim table is in R1 region (>= $80 after XOR)."""
    import sys
    sys.path.insert(0, 'tools')
    from dump_metasprite import decode
    anim_cpu = (e['anim_hi'] << 8) | e['anim_lo']
    if anim_cpu >= 0xA000:
        tbl_fo = 0x10 + e['anim_bank'] * 0x2000 + (anim_cpu - 0xA000)
    else:
        tbl_fo = 0x10 + e['anim_bank'] * 0x2000 + (anim_cpu - 0x8000)
    for idx in range(32):
        lo = rom[tbl_fo + idx*2]; hi = rom[tbl_fo + idx*2 + 1]
        ptr = (hi << 8) | lo
        for slot_base in (0x8000, 0xA000):
            if slot_base <= ptr < slot_base + 0x2000:
                ms_fo = 0x10 + e['anim_bank'] * 0x2000 + (ptr - slot_base)
                if ms_fo >= len(rom): continue
                count, entries = decode(rom, ms_fo)
                if 1 <= count <= 24:
                    for ent in entries:
                        if (ent[2] ^ 0x01) >= 0x80:
                            return True
    return False

rows = []
for e in enemies:
    if e['anim_bank'] is None:
        continue
    sid = e['spawn_id']
    anim_cpu = (e['anim_hi'] << 8) | e['anim_lo']
    if anim_cpu >= 0xA000:
        tbl_fo = 0x10 + e['anim_bank'] * 0x2000 + (anim_cpu - 0xA000)
    else:
        tbl_fo = 0x10 + e['anim_bank'] * 0x2000 + (anim_cpu - 0x8000)
    out_file = f'{OUT}/id_{sid:02X}.png'
    cmd = ['python3', 'tools/render_metasprite.py',
           '../reference/rom/kirby.nes',
           '--table-file', hex(tbl_fo),
           '--table-chunk', hex(e['anim_bank']),
           '--sprite-size', '16',
           '--normalize-facing',
           '--out', out_file, '--scale', '2']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        print(f'  id ${sid:02X}: TIMEOUT (anim-table format probably non-standard)')
        continue
    if r.returncode != 0:
        print(f'  id ${sid:02X}: FAIL ({r.stderr.strip()[:80]})')
        continue
    # Count metasprites from output
    n_sprites = sum(1 for ln in r.stdout.splitlines() if ln.strip().startswith('['))
    r1_needed = needs_r1(e)
    rows.append({'sid': sid, 'n': n_sprites, 'r1_needed': r1_needed,
                 'image': f'id_{sid:02X}.png',
                 'anim_bank': e['anim_bank'],
                 'anim_pc': anim_cpu})
    print(f'  id ${sid:02X}: {n_sprites} frames, R1-dependent={r1_needed}')

# HTML
parts = ['''<!doctype html>
<html><head><meta charset="utf-8"><title>Kirby NES — Enemy Sheets</title>
<style>
body { background:#222; color:#ddd; font-family: ui-monospace, monospace; padding:1em; max-width: 1200px; margin: 0 auto; }
h1 { color:#fc9; }
h2 { color:#9cf; margin-top:1.5em; }
img { image-rendering: pixelated; image-rendering: crisp-edges; max-width:100%; border:1px solid #444; background:#3a3a4a; }
.note { color:#aaa; font-size:0.9em; }
.r1-dep { color:#ff9; }
.r1-ok  { color:#9f9; }
</style></head><body>
<h1>Kirby NES — Enemy sprite sheets</h1>
<p class="note">
Each row shows one enemy's full animation-table frames at the default
stage-1 palette/CHR. Some frames may show pixel garbage if the enemy
uses R1-region tiles (high 2KB of sprite CHR) and the wrong R1 is
loaded. Each row is tagged whether R1 matters.
</p>
''']
for r in rows:
    tag = '<span class="r1-dep">[R1-DEPENDENT]</span>' if r['r1_needed'] else '<span class="r1-ok">[R1 OK — these frames are correct]</span>'
    parts.append(f'''
<h2>spawn id ${r['sid']:02X} &nbsp; {tag}</h2>
<div class="note">anim table at ${r['anim_bank']:02X}:${r['anim_pc']:04X} &nbsp; {r['n']} frame(s)</div>
<img src="{r['image']}" alt="id ${r['sid']:02X}">
''')
parts.append('</body></html>')

with open(f'{OUT}/index.html', 'w') as f:
    f.write(''.join(parts))
print(f'\nWrote {len(rows)} sprite sheets + index.html in {OUT}/')
