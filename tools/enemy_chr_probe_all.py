#!/usr/bin/env python3
"""Generate one CHR-probe contact sheet per enemy with a known anim
table, plus a simple HTML index page for picking the right R1 per enemy.

For each enemy in enemy_anim_tables.json with a resolved anim_bank:
  1. Read entry 0 of the anim table (first metasprite pointer)
  2. Resolve it to a file offset assuming the standard $A000-$BFFF or
     $8000-$9FFF slot mapping
  3. Try rendering with all 18 unique R1 values from the per-map header[4]
  4. Save the contact sheet to galleries/enemy_chr_probes/id_XX.png

Also writes index.html with one block per enemy, showing the contact
sheet and a list of R1 values you can copy a pick into a manifest.
"""
import json
import os
import subprocess

ROM = '../reference/rom/kirby.nes'
OUT_DIR = 'galleries/enemy_chr_probes'
os.makedirs(OUT_DIR, exist_ok=True)

with open(ROM, 'rb') as f:
    rom = f.read()
with open('docs/enemy_anim_tables.json') as f:
    enemies = json.load(f)
with open('docs/enemy_catalog.json') as f:
    cat = json.load(f)


def find_first_valid_metasprite(anim_bank, anim_lo, anim_hi, max_try=32):
    """Try entry 0..max_try of the anim table. Return all candidates
    with plausible count (1..30) sorted by count DESCENDING (we want
    a metasprite with lots of tiles for easier visual identification).
    Tries both slot interpretations.
    """
    anim_cpu = (anim_hi << 8) | anim_lo
    if anim_cpu >= 0xA000:
        tbl_fo = 0x10 + anim_bank * 0x2000 + (anim_cpu - 0xA000)
    else:
        tbl_fo = 0x10 + anim_bank * 0x2000 + (anim_cpu - 0x8000)

    candidates = []
    for idx in range(max_try):
        if tbl_fo + idx * 2 + 1 >= len(rom):
            continue
        lo = rom[tbl_fo + idx * 2]
        hi = rom[tbl_fo + idx * 2 + 1]
        ptr = (hi << 8) | lo
        for slot_base in (0x8000, 0xA000):
            if slot_base <= ptr < slot_base + 0x2000:
                ms_fo = 0x10 + anim_bank * 0x2000 + (ptr - slot_base)
                if ms_fo < len(rom):
                    count = rom[ms_fo]
                    if 1 <= count <= 24:
                        candidates.append({'idx': idx, 'ptr': ptr,
                                           'ms_fo': ms_fo, 'count': count,
                                           'slot_base': slot_base})
    # Prefer larger metasprites — they're easier to visually identify
    candidates.sort(key=lambda c: (-c['count'], c['idx']))
    return candidates


def main():
    index_entries = []
    for e in enemies:
        sid = e['spawn_id']
        if e['anim_bank'] is None:
            continue
        candidates = find_first_valid_metasprite(
            e['anim_bank'], e['anim_lo'], e['anim_hi'])
        if not candidates:
            print(f'  id ${sid:02X}: no valid metasprite found in anim table')
            continue
        # Pick the FIRST candidate
        c = candidates[0]
        out_name = f'id_{sid:02X}.png'
        out_path = f'{OUT_DIR}/{out_name}'
        cmd = ['python3', 'tools/sprite_chr_probe.py', ROM,
               '--file', hex(c['ms_fo']),
               '--out', out_path, '--scale', '3', '--per-row', '6']
        print(f'  id ${sid:02X}: rendering {len(candidates)} candidate(s), '
              f'using entry {c["idx"]} (file ${c["ms_fo"]:X}, count={c["count"]})')
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f'    FAIL: {r.stderr.strip()[:120]}')
            continue
        index_entries.append({
            'spawn_id': sid,
            'script_bank': e['script_bank'],
            'script_pc': e['script_pc'],
            'anim_bank': e['anim_bank'],
            'anim_pc': (e['anim_hi'] << 8) | e['anim_lo'],
            'metasprite_file': c['ms_fo'],
            'metasprite_count': c['count'],
            'image': out_name,
        })

    # Build HTML index
    R1_VALUES = [0x3C, 0x9A, 0x9C, 0x9E, 0xA0, 0xA2, 0xA8, 0xAA, 0xAC, 0xAE,
                 0xC4, 0xCA, 0xCC, 0xD2, 0xD6, 0xE8, 0xEC, 0xEE]
    grid_html = '\n'.join(
        f'        <span class="rg" data-r1="0x{r:02X}">${r:02X}</span>'
        for r in R1_VALUES)

    html_parts = ["""<!doctype html>
<html><head><meta charset="utf-8"><title>Kirby NES — Enemy CHR Picker</title>
<style>
  body { background:#222; color:#ddd; font-family: ui-monospace, monospace; padding:1em; }
  h2 { color:#fc9; margin-top:2em; }
  .meta { color:#aaa; font-size:0.9em; }
  img { image-rendering: pixelated; image-rendering: crisp-edges; max-width:100%; border:1px solid #555; }
  .grid { display:flex; flex-wrap:wrap; gap:6px; margin: 8px 0; }
  .rg { background:#333; padding:4px 8px; border:1px solid #555; cursor:pointer; user-select:none; }
  .rg.picked { background:#582; color:#fff; }
  .row { display:flex; gap:1em; align-items:flex-start; margin: 1em 0; }
  .col { flex:1; min-width:0; }
  textarea { background:#111; color:#7df; width:100%; height:8em; font-family:inherit; padding:8px; }
  button { background:#582; color:#fff; border:0; padding:8px 12px; cursor:pointer; }
</style>
</head><body>
<h1>Enemy CHR Picker</h1>
<p>For each enemy below, click the R1 chip that makes the sprite look cleanest. The order matches the 6-per-row contact sheet (left-to-right, top-to-bottom). At the bottom you'll get a JSON manifest you can save.</p>
<div class="grid"><strong>R1 values (in sheet order, 6 per row):</strong></div>
<div class="grid">
""" + grid_html + """
</div>
"""]

    for e in index_entries:
        sid = e['spawn_id']
        html_parts.append('''
<h2>spawn id ${sid:02X}</h2>
<div class="meta">
  script ${sb:02X}:${sp:04X} &nbsp;|&nbsp;
  anim_table ${ab:02X}:${ap:04X} &nbsp;|&nbsp;
  first metasprite at file ${mf:X} (count={mc})
</div>
<div class="row">
  <div class="col"><img src="{img}" alt="id ${sid:02X}"></div>
  <div class="col">
    <p>Click the R1 value that looks correct:</p>
    <div class="grid id-{sid:02X}">
'''.format(sid=sid, sb=e['script_bank'], sp=e['script_pc'],
           ab=e['anim_bank'], ap=e['anim_pc'],
           mf=e['metasprite_file'], mc=e['metasprite_count'],
           img=e['image']))
        for r in R1_VALUES:
            html_parts.append(f'      <span class="rg" data-id="{sid}" data-r1="0x{r:02X}">${r:02X}</span>\n')
        html_parts.append('    </div>\n  </div>\n</div>\n')

    html_parts.append('''
<h2>Manifest</h2>
<button onclick="exportManifest()">Export JSON</button>
<textarea id="out"></textarea>
<script>
document.querySelectorAll('.rg[data-id]').forEach(el => {
  el.addEventListener('click', () => {
    const sid = el.dataset.id;
    document.querySelectorAll(`.rg[data-id="${sid}"]`).forEach(s => s.classList.remove('picked'));
    el.classList.add('picked');
  });
});
function exportManifest() {
  const picks = {};
  document.querySelectorAll('.rg.picked[data-id]').forEach(el => {
    picks['0x' + parseInt(el.dataset.id).toString(16).padStart(2,'0').toUpperCase()] = el.dataset.r1;
  });
  document.getElementById('out').value = JSON.stringify(picks, null, 2);
}
</script>
</body></html>
''')

    with open(f'{OUT_DIR}/index.html', 'w') as f:
        f.write(''.join(html_parts))
    print(f'\nWrote {len(index_entries)} enemy contact sheets + index.html in {OUT_DIR}/')


if __name__ == '__main__':
    main()
