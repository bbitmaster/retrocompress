#!/usr/bin/env python3
"""Cross-reference the brute-forced enemy anim tables against the
scanned-discovered anim tables. Mark each enemy's anim_table as:
  - 'verified' if it matches a scanned table exactly
  - 'near-miss' if a scanned table starts within 16 bytes of the
    brute-force address (use the scanned one as corrected)
  - 'false_positive' if no nearby scanned table exists

Output: docs/enemy_anims_verified.json
"""
import json

with open('docs/enemy_anim_bruteforce.json') as f:
    bf = json.load(f)
with open('docs/anim_tables_discovered.json') as f:
    discovered = json.load(f)

# Build lookup: (chunk, anim_addr) -> {len, first_kind}
scanned = {}
for chunk_s, tables in discovered['tables_by_chunk'].items():
    chunk = int(chunk_s.lstrip('$'), 16)
    for t in tables:
        addr = int(t['anim_addr'].lstrip('$'), 16)
        scanned[(chunk, addr)] = {'len': t['len'], 'first_kind': t['first_kind']}

# Cross-check each enemy
verified = []
for e in bf:
    if not e.get('found'):
        continue
    sid = e['spawn_id']
    chunk = e['anim_bank']
    addr = (e['anim_hi'] << 8) | e['anim_lo']
    if (chunk, addr) in scanned:
        info = scanned[(chunk, addr)]
        verified.append({**e, 'verification': 'verified',
                         'verified_addr': addr,
                         'table_len': info['len'],
                         'first_kind': info['first_kind']})
        continue
    near = None
    for (c, a), info in scanned.items():
        if c == chunk and 0 < a - addr <= 16:
            near = (a, info)
            break
    if near:
        verified.append({**e, 'verification': 'near_miss',
                         'verified_addr': near[0],
                         'table_len': near[1]['len'],
                         'first_kind': near[1]['first_kind'],
                         'offset_correction': near[0] - addr})
    else:
        verified.append({**e, 'verification': 'false_positive',
                         'verified_addr': None,
                         'table_len': 0,
                         'first_kind': None})

# Summary
n_verified = sum(1 for v in verified if v['verification'] == 'verified')
n_near = sum(1 for v in verified if v['verification'] == 'near_miss')
n_fp = sum(1 for v in verified if v['verification'] == 'false_positive')
print(f'{"id":>4} | brute-force anim    | verification  | corrected addr')
print('-' * 65)
for v in sorted(verified, key=lambda x: x['spawn_id']):
    bf_str = f"${v['anim_bank']:02X}:${(v['anim_hi']<<8)|v['anim_lo']:04X}"
    corr = (f"${v['anim_bank']:02X}:${v['verified_addr']:04X} (+{v['offset_correction']})"
            if v['verification'] == 'near_miss' else
            (f"${v['anim_bank']:02X}:${v['verified_addr']:04X}"
             if v['verification'] == 'verified' else '-'))
    print(f"${v['spawn_id']:02X}  | {bf_str:>20} | {v['verification']:>13} | {corr}")

print(f'\n  verified:        {n_verified}')
print(f'  near-miss:       {n_near} (corrected)')
print(f'  false-positive:  {n_fp}')

with open('docs/enemy_anims_verified.json', 'w') as f:
    json.dump({'enemies': verified,
               'summary': {'verified': n_verified, 'near_miss': n_near,
                           'false_positive': n_fp}}, f, indent=2)
print('Saved docs/enemy_anims_verified.json')
