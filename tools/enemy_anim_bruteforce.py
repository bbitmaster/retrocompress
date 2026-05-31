#!/usr/bin/env python3
"""Brute-force discovery of opcode $1A (set anim table) inside each
spawn target's script.

Strategy: scan the script's bank starting from the entry PC, looking
for any $1A byte where the following 3 bytes form a plausible
(lo, hi, bank) tuple:
  - bank is a "known metasprite-data chunk" (we've seen $1C, $30, $31,
    $32, $33 so far — but accept any < $40 with content nearby)
  - lo in $00-$FF, hi in $80-$BF (so the CPU address is in slot
    $8000-$9FFF or $A000-$BFFF)

For each script, take the FIRST plausible match within ~256 bytes of
the entry. This catches enemies whose $1A hides behind:
- a leading wait opcode
- a conditional skip (op $14 / $15)
- a switch-case (op $0B / $0F / $29)
- one near-CALL (op $18) that we don't follow

False-positive risk: medium — the byte $1A might appear in non-opcode
positions (operand bytes of other opcodes). Mitigated by requiring
all 3 following bytes to fit a strict pattern.
"""
import json


KNOWN_METASPRITE_BANKS = {0x1A, 0x1C, 0x30, 0x31, 0x32, 0x33, 0x37, 0x3A}


def file_offset(bank, cpu):
    if 0xA000 <= cpu < 0xC000:
        return 0x10 + bank * 0x2000 + (cpu - 0xA000)
    if 0x8000 <= cpu < 0xA000:
        return 0x10 + bank * 0x2000 + (cpu - 0x8000)
    return None


def scan_script_for_1a(rom, bank, pc, max_bytes=1024, strict_bank=True):
    """Return list of (offset, lo, hi, bank) candidates."""
    fo = file_offset(bank, pc)
    if fo is None:
        return []
    candidates = []
    for off in range(max_bytes):
        if fo + off + 3 >= len(rom):
            break
        if rom[fo + off] != 0x1A:
            continue
        lo = rom[fo + off + 1]
        hi = rom[fo + off + 2]
        bk = rom[fo + off + 3]
        if not (0x80 <= hi <= 0xBF):
            continue
        if bk >= 0x40:
            continue
        if strict_bank and bk not in KNOWN_METASPRITE_BANKS:
            continue
        candidates.append({'offset': off, 'lo': lo, 'hi': hi, 'bank': bk})
    return candidates


def main():
    with open('../reference/rom/kirby.nes', 'rb') as f:
        rom = f.read()
    with open('docs/enemy_catalog.json') as f:
        cat = json.load(f)

    targets = {}
    for s in cat['sites']:
        if s['spawn_id'] is not None and s['target_pc']:
            targets[(s['target_bank'], s['target_pc'])] = s['spawn_id']
        if s['indexed_table']:
            for e in s['indexed_table']['entries']:
                targets[(e['target_bank'], e['target_pc'])] = e['spawn_id']

    results = []
    print(f'{"id":>4} | {"script":>11} | {"anim":>11} | {"@off":>5} | strict?')
    print('-' * 65)
    for (bk, pc), sid in sorted(targets.items(), key=lambda kv: kv[1]):
        # First try strict (known banks only)
        cands = scan_script_for_1a(rom, bk, pc, strict_bank=True)
        strict_used = True
        if not cands:
            # Fall back to loose (any bank with valid hi)
            cands = scan_script_for_1a(rom, bk, pc, strict_bank=False)
            strict_used = False
        if not cands:
            print(f'${sid:02X}  | ${bk:02X}:${pc:04X} |        -    |     - |')
            results.append({'spawn_id': sid, 'script_bank': bk,
                            'script_pc': pc, 'found': False})
            continue
        c = cands[0]
        anim_pc = (c['hi'] << 8) | c['lo']
        print(f'${sid:02X}  | ${bk:02X}:${pc:04X} | ${c["bank"]:02X}:${anim_pc:04X} | {c["offset"]:5d} | {strict_used}')
        results.append({
            'spawn_id': sid, 'script_bank': bk, 'script_pc': pc,
            'found': True, 'op_1a_at_offset': c['offset'],
            'anim_lo': c['lo'], 'anim_hi': c['hi'], 'anim_bank': c['bank'],
            'strict_match': strict_used,
        })

    found = sum(1 for r in results if r['found'])
    print(f'\n{found}/{len(results)} enemies have a plausible $1A (brute-force)')

    with open('docs/enemy_anim_bruteforce.json', 'w') as f:
        json.dump(results, f, indent=2)
    print('Wrote docs/enemy_anim_bruteforce.json')


if __name__ == '__main__':
    main()
