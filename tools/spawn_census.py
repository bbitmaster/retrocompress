#!/usr/bin/env python3
"""Census of all spawn-call sites in Kirby's Adventure ROM.

Spawn happens via JMP/JSR to $3E:CCA7 (raw VM-slot setup) or $3E:CC4B
(spawn-by-ID, very rarely used). For each call site we scan ~32 bytes
backward and pattern-match the immediate-loads that set A, Y, X, and
$6031 (= script bank for the new slot).

Output:
  - docs/spawn_census.json  with one record per call site
  - prints a histogram by (bank, PC) of unique target scripts
"""
import json
import collections

ROM_PATH = '../reference/rom/kirby.nes'

# Immediate-load opcodes we care about
LDA_IMM = 0xA9
LDX_IMM = 0xA2
LDY_IMM = 0xA0
STA_ABS = 0x8D


def scan_back(rom, pos, slot_known=None, max_back=40):
    """Scan up to ~40 bytes before pos to extract immediate-loads.

    Returns dict {'A': val, 'Y': val, 'X': val, 'bank': val}, with values
    None if not statically resolvable. Stops on any branch/JMP/JSR or
    when we wander off the function.
    """
    result = {'A': None, 'Y': None, 'X': None, 'bank': None}
    seen_a = seen_y = seen_x = seen_bank = False
    i = pos - 1
    end = max(0, pos - max_back)
    # Walk forward from candidate starts (we don't know instruction
    # boundaries, so try multiple alignments and take the last one that
    # parses cleanly into pos).
    # Simpler approach: scan backward looking for the literal opcode
    # sequences "A9 XX" (LDA #), "A0 XX" (LDY #), "A2 XX" (LDX #), and
    # "8D 31 60" (STA $6031, prefixed by an LDA).
    # Take the FIRST occurrence reading backward (the latest write before
    # the call).
    j = pos - 1
    while j > end:
        if not seen_bank and j >= 2 and rom[j-2] == 0xA9 and rom[j] == 0x8D \
                and j + 2 < len(rom) and rom[j+1] == 0x31 and rom[j+2] == 0x60:
            # LDA #XX / STA $6031
            result['bank'] = rom[j-1]
            seen_bank = True
        j -= 1
    j = pos - 1
    while j > end:
        if not seen_a and j > 0 and rom[j-1] == LDA_IMM:
            result['A'] = rom[j]
            seen_a = True
            break
        # don't crawl through likely instruction boundaries blindly
        if rom[j-1] in (0x4C, 0x20, 0x60, 0x40):  # JMP/JSR/RTS/RTI
            break
        j -= 1
    j = pos - 1
    while j > end:
        if not seen_y and j > 0 and rom[j-1] == LDY_IMM:
            result['Y'] = rom[j]
            seen_y = True
            break
        if rom[j-1] in (0x4C, 0x20, 0x60, 0x40):
            break
        j -= 1
    j = pos - 1
    while j > end:
        if not seen_x and j > 0 and rom[j-1] == LDX_IMM:
            result['X'] = rom[j]
            seen_x = True
            break
        if rom[j-1] in (0x4C, 0x20, 0x60, 0x40):
            break
        j -= 1
    return result


def main():
    with open(ROM_PATH, 'rb') as f:
        rom = f.read()

    records = []
    # Find all spawn calls
    for op_b, mnem in [(0x4C, 'JMP'), (0x20, 'JSR')]:
        for target_lo, target_hi, name in [(0xA7, 0xCC, 'CCA7'), (0x4B, 0xCC, 'CC4B')]:
            i = 0
            while i < len(rom) - 2:
                if rom[i] == op_b and rom[i+1] == target_lo and rom[i+2] == target_hi:
                    chunk = (i - 0x10) // 0x2000
                    rem = (i - 0x10) % 0x2000
                    # Try both slot assumptions for CPU display
                    cpu_lo_slot = 0x8000 + rem
                    cpu_hi_slot = 0xA000 + rem
                    # Heuristic: chunks usually live in $A000 (R7) for scripts
                    cpu = cpu_hi_slot if rem < 0x2000 else cpu_lo_slot

                    params = scan_back(rom, i)
                    rec = {
                        'file': i,
                        'chunk': chunk,
                        'rem': rem,
                        'cpu_lo': cpu_lo_slot,
                        'cpu_hi': cpu_hi_slot,
                        'kind': f'{mnem} ${name}',
                        'target': name,
                        'A': params['A'],
                        'Y': params['Y'],
                        'X': params['X'],
                        'bank': params['bank'],
                    }
                    if name == 'CCA7' and params['A'] is not None and params['Y'] is not None:
                        rec['target_pc'] = (params['Y'] << 8) | params['A']
                    records.append(rec)
                i += 1

    # Histogram by (bank, target_pc) - unique spawnees
    by_target = collections.Counter()
    resolved = 0
    for r in records:
        if r['target'] == 'CCA7' and r.get('target_pc') is not None and r['bank'] is not None:
            by_target[(r['bank'], r['target_pc'])] += 1
            resolved += 1

    print(f'Total spawn-call sites: {len(records)}')
    print(f'Statically resolved (A,Y,bank known): {resolved}')
    print(f'Unique (bank, target_pc) tuples: {len(by_target)}')

    # Group resolved by bank
    by_bank = collections.defaultdict(set)
    for (bk, pc), cnt in by_target.items():
        by_bank[bk].add(pc)
    print('\nUnique target scripts per chunk:')
    for bk in sorted(by_bank):
        pcs = sorted(by_bank[bk])
        print(f'  chunk ${bk:02X}: {len(pcs):3d} scripts  (e.g. ${pcs[0]:04X}, ${pcs[-1]:04X})')

    # Save
    out_path = 'docs/spawn_census.json'
    with open(out_path, 'w') as f:
        json.dump({
            'records': records,
            'unique_targets_by_bank': {f'${bk:02X}': sorted(pcs) for bk, pcs in by_bank.items()},
            'note': ('Census of every JMP/JSR to $3E:CCA7 (raw VM-slot setup) '
                     'and $3E:CC4B (spawn-by-ID) in the ROM. Parameters '
                     '(A, Y, X, bank) extracted by scanning <= 40 bytes back '
                     'for LDA #imm / LDY #imm / LDX #imm / LDA #imm + STA $6031. '
                     'NULL = could not statically resolve.'),
        }, f, indent=2)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
