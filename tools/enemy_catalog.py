#!/usr/bin/env python3
"""Enemy spawn catalog — finds every static JSR $CBA2 (in-game spawn)
across the ROM, extracts the spawn-ID parameter (A loaded just before
the call), and cross-references with the 112-entry spawn-ID table in
chunk $18 to identify the target script and bank.

This is the answer to "which enemies exist in the game." Static, no
trace required.

Output: docs/enemy_catalog.json — per call site:
  - file offset, chunk, CPU address (likely-bank-mapping)
  - spawn_id (extracted from immediate LDA, or None)
  - script_pc, script_bank (from spawn_table.json)
"""
import json
import collections

ROM_PATH = '../reference/rom/kirby.nes'
CBA2 = 0xCBA2

with open(ROM_PATH, 'rb') as f:
    rom = f.read()
with open('docs/spawn_table.json') as f:
    spawn_tbl = json.load(f)['entries']

# Index spawn_table by id
TABLE = {e['id']: e for e in spawn_tbl}


def scan_back_lda_imm(pos, max_back=32):
    """Scan back for LDA #imm = A9 XX. Returns the imm or None."""
    j = pos - 1
    end = max(0, pos - max_back)
    while j > end:
        if j >= 1 and rom[j-1] == 0xA9:
            return rom[j]
        if rom[j-1] in (0x4C, 0x20, 0x60, 0x40):
            break
        j -= 1
    return None


def scan_back_lda_abs_y(pos, max_back=32):
    """Scan back for LDA abs,Y = B9 lo hi or LDA abs,X = BD lo hi.
    Returns (operand_addr, 'X' or 'Y') or None."""
    j = pos - 1
    end = max(0, pos - max_back)
    while j > end:
        # 3-byte instruction: opcode at j-2, operand bytes at j-1, j.
        # But operands could be later; let's check both forms.
        if j >= 2 and rom[j-2] in (0xB9, 0xBD):
            addr = rom[j-1] | (rom[j] << 8)
            return (addr, 'X' if rom[j-2] == 0xBD else 'Y', rom[j-2])
        j -= 1
    return None


def extract_table(chunk, table_cpu, spawn_tbl, max_entries=32):
    """Given a table at chunk:cpu, return the list of valid spawn IDs
    until we hit an invalid entry (one not in spawn_tbl).
    """
    if 0xA000 <= table_cpu < 0xC000:
        base = 0x10 + chunk * 0x2000 + (table_cpu - 0xA000)
    elif 0x8000 <= table_cpu < 0xA000:
        base = 0x10 + chunk * 0x2000 + (table_cpu - 0x8000)
    else:
        return []
    out = []
    for i in range(max_entries):
        sid = rom[base + i]
        if sid not in spawn_tbl:
            break
        out.append({'idx': i, 'spawn_id': sid,
                    'target_pc': spawn_tbl[sid]['pc'],
                    'target_bank': spawn_tbl[sid]['bank']})
    return out


# Find every JSR/JMP $CBA2
sites = []
for op_b, mnem in [(0x20, 'JSR'), (0x4C, 'JMP')]:
    i = 0
    while i < len(rom) - 2:
        if rom[i] == op_b and rom[i+1] == (CBA2 & 0xFF) and rom[i+2] == (CBA2 >> 8):
            chunk = (i - 0x10) // 0x2000
            rem = (i - 0x10) % 0x2000
            cpu_lo = 0x8000 + rem
            cpu_hi = 0xA000 + rem
            cpu_c0 = 0xC000 + rem
            spawn_id = scan_back_lda_imm(i)
            tgt = TABLE.get(spawn_id) if spawn_id is not None else None
            indexed = None
            if spawn_id is None:
                # Try indexed-table pattern: LDA abs,Y / JSR $CBA2
                ind = scan_back_lda_abs_y(i)
                if ind is not None:
                    addr, reg, _opc = ind
                    indexed = {
                        'table_cpu': addr,
                        'index_reg': reg,
                        'entries': extract_table(chunk, addr, TABLE),
                    }
            sites.append({
                'file': i,
                'chunk': chunk,
                'cpu_lo': cpu_lo,
                'cpu_hi': cpu_hi,
                'cpu_c0': cpu_c0,
                'kind': mnem,
                'spawn_id': spawn_id,
                'target_pc': tgt['pc'] if tgt else None,
                'target_bank': tgt['bank'] if tgt else None,
                'indexed_table': indexed,
            })
        i += 1


# Group by spawn_id
by_id = collections.defaultdict(list)
for s in sites:
    by_id[s['spawn_id']].append(s)

# Group by chunk
by_chunk = collections.defaultdict(list)
for s in sites:
    by_chunk[s['chunk']].append(s)


# Collect indexed-table additions
all_ids_seen = set(s['spawn_id'] for s in sites if s['spawn_id'] is not None)
for s in sites:
    if s['indexed_table']:
        for e in s['indexed_table']['entries']:
            all_ids_seen.add(e['spawn_id'])

print(f'Found {len(sites)} JSR/JMP $CBA2 sites in ROM')
print(f'Direct spawn IDs:         {len(set(s["spawn_id"] for s in sites if s["spawn_id"] is not None))}')
print(f'Dynamic-A sites resolved via indexed table: {sum(1 for s in sites if s["indexed_table"] and s["indexed_table"]["entries"])}')
print(f'Total unique spawn IDs reachable: {len(all_ids_seen)}')
print(f'Resolved (direct A):              {sum(1 for s in sites if s["target_pc"] is not None)}')
print(f'Stack-passed (still dynamic):     {sum(1 for s in sites if s["spawn_id"] is None and not s["indexed_table"])}')
print()
print('=== Sites by chunk ===')
for c in sorted(by_chunk):
    print(f'  chunk ${c:02X}: {len(by_chunk[c])} site(s)')
    for s in by_chunk[c]:
        loc = f'${s["chunk"]:02X}:${s["cpu_hi"]:04X}'
        if s['spawn_id'] is not None:
            tgt = (f'${s["target_bank"]:02X}:${s["target_pc"]:04X}'
                   if s['target_pc'] is not None else '<id out of table>')
            print(f'    {loc}  spawn_id=${s["spawn_id"]:02X}  -> {tgt}')
        elif s['indexed_table'] and s['indexed_table']['entries']:
            print(f'    {loc}  LDA ${s["indexed_table"]["table_cpu"]:04X},{s["indexed_table"]["index_reg"]}  -> {len(s["indexed_table"]["entries"])} entries:')
            for e in s['indexed_table']['entries']:
                print(f'        [{e["idx"]:2d}] id=${e["spawn_id"]:02X}  -> ${e["target_bank"]:02X}:${e["target_pc"]:04X}')
        else:
            print(f'    {loc}  spawn_id=<stack-passed>')

with open('docs/enemy_catalog.json', 'w') as f:
    json.dump({
        'sites': sites,
        'by_spawn_id': {f'${k:02X}' if k is not None else 'dynamic':
                        [{'chunk': s['chunk'], 'cpu_hi': f'${s["cpu_hi"]:04X}',
                          'target_pc': f'${s["target_pc"]:04X}' if s['target_pc'] else None,
                          'target_bank': f'${s["target_bank"]:02X}' if s['target_bank'] else None}
                         for s in v]
                        for k, v in sorted(by_id.items(), key=lambda kv: -1 if kv[0] is None else kv[0])},
        'note': 'Every static call site for $3E:$CBA2, the in-game enemy '
                'spawn routine. spawn_id is the A value loaded immediately '
                'before the JSR (None if not statically resolvable). '
                'target_pc/bank derived from spawn_table.json (the 112-entry '
                'dispatch table at $18:A0A7/A117/A187 in chunk $18).',
    }, f, indent=2)
print('\nWrote docs/enemy_catalog.json')
