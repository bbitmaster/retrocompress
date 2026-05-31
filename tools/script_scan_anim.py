#!/usr/bin/env python3
"""For each unique target script in spawn_census.json, scan the script's
bytecode for opcode $1A (set anim table) and record (anim_lo, anim_hi,
anim_bank). This lets us map each spawnable character to its metasprite
pointer table.

Strategy: walk the bytecode from script entry. We can decode opcode sizes
from the dispatch handlers we know:

  $03: 4 bytes  (op + lo + hi + bank)  [far JMP]
  $18: 3 bytes  (op + lo + hi)         [near CALL]
  $1A: 4 bytes  (op + lo + hi + bank)  [set anim table]   *** RECORD ***
  $1B: 2 bytes  (op + byte)
  $15: 1 byte   (op)
  >=$50: 1 byte (wait + delay)

For unknown opcodes we record "unknown size" and stop (we keep what
we've found so far). When we see opcode $03 (far JMP), we follow it
across banks. When we see opcode $18 (near CALL), we follow it.
Returns / "wait" ops also terminate the linear scan.

This is a HEURISTIC — many opcodes are unknown so we'll stop early on
many scripts. For each script we report:
  - found_anim: (lo, hi, bank) tuple if $1A was found, else None
  - terminated_by: which opcode stopped the scan
  - bytes_scanned
"""
import json
import collections

# Opcode sizes auto-extracted from VM dispatcher handlers (LDA #N / JMP $CE13).
# Sentinel -1 = stop (handler manages PC itself: branches, returns, far JMP).
OPSIZES = {
    0x00: -1, # unknown — stop
    0x01: -1,
    0x02: -1, # PC-manipulating
    0x03: -1, # far JMP — we follow it manually
    0x04: -1,
    0x05: -1, # RETURN
    0x06: 2,
    0x07: 3,
    0x08: 4,
    0x09: -1,
    0x0A: -1,
    0x0B: -1,
    0x0C: 1,
    0x0D: 3,
    0x0E: 1,
    0x0F: -1,
    0x10: -1,
    0x11: 4,
    0x12: 4,
    0x13: -1,
    0x14: -1,
    0x15: -1, # conditional skip — flow-dependent
    0x16: -1,
    0x17: -1,
    0x18: -1, # near CALL — follow manually
    0x19: -1,
    0x1A: 4,  # set anim table *** TARGET ***
    0x1B: 2,
    0x1C: 3,
    0x1D: 2,
    0x1E: 2,
    0x1F: 2,
    0x20: 3,
    0x21: 3,
    0x22: -1,
    0x23: 3,
    0x24: 2,
    0x25: -1,
    0x26: 1,
    0x27: 5,
    0x28: 2,
    0x29: -1,
    0x2A: 3,
    0x2B: 3,
    0x2C: 3,
    0x2D: 3,
    0x2E: 3,
    0x2F: 3,
    0x30: 3,
    0x31: 3,
    0x32: 3,
    0x33: 3,
    0x34: 3,
    0x35: 3,
    0x36: 3,
    0x37: 3,
    0x38: 1,
    0x39: 1,
    0x3A: 3,
    0x3B: 3,
    0x3C: 3,
    0x3D: 3,
    0x3E: -1,
    0x3F: -1,
}

def file_offset(bank, cpu):
    """Return ROM file offset for (bank, CPU). cpu must be $A000-$BFFF
    or $8000-$9FFF; bank is the 8KB chunk loaded into that slot."""
    if 0xA000 <= cpu < 0xC000:
        return 0x10 + bank * 0x2000 + (cpu - 0xA000)
    if 0x8000 <= cpu < 0xA000:
        return 0x10 + bank * 0x2000 + (cpu - 0x8000)
    return None


def scan_script(rom, bank, pc, max_steps=200):
    """Walk the bytecode looking for opcode $1A.

    Returns (anim_lo, anim_hi, anim_bank, terminated_by, steps).
    Follows near CALL ($18) by recursing. Stops on unknown opcode or
    a wait, RETURN, or far JMP (after recording its target if useful)."""
    steps = 0
    cur_bank = bank
    cur_pc = pc
    while steps < max_steps:
        steps += 1
        fo = file_offset(cur_bank, cur_pc)
        if fo is None or fo >= len(rom) - 4:
            return None, None, None, 'oob', steps
        op = rom[fo]
        if op == 0x1A:
            # SET ANIM TABLE
            return rom[fo+1], rom[fo+2], rom[fo+3], 'found', steps
        if op >= 0x50:
            return None, None, None, f'wait_${op:02X}', steps
        sz = OPSIZES.get(op, -1)
        if sz == -1 and op not in (0x03, 0x18):
            return None, None, None, f'stop_${op:02X}', steps
        if op == 0x03:
            # Far JMP: follow it
            cur_pc = rom[fo+1] | (rom[fo+2] << 8)
            cur_bank = rom[fo+3]
            continue
        if op == 0x18:
            # Near CALL: recurse, then continue past
            sub_lo = rom[fo+1]
            sub_hi = rom[fo+2]
            sub_pc = sub_lo | (sub_hi << 8)
            # Recurse with smaller budget
            r = scan_script(rom, cur_bank, sub_pc, max_steps=max_steps//2)
            if r[3] == 'found':
                return r
            cur_pc += sz
            continue
        if op in (0x05, 0x06):
            return None, None, None, 'return', steps
        cur_pc += sz
    return None, None, None, 'budget', steps


def main():
    with open('../reference/rom/kirby.nes', 'rb') as f:
        rom = f.read()
    with open('docs/spawn_census.json') as f:
        census = json.load(f)

    # Collect unique (bank, target_pc)
    targets = set()
    for r in census['records']:
        if r.get('target') == 'CCA7' and r.get('target_pc') and r.get('bank'):
            targets.add((r['bank'], r['target_pc']))

    results = []
    for bank, pc in sorted(targets):
        anim = scan_script(rom, bank, pc)
        results.append({
            'script_bank': bank,
            'script_pc': pc,
            'anim_lo': anim[0],
            'anim_hi': anim[1],
            'anim_bank': anim[2],
            'terminated': anim[3],
            'steps': anim[4],
        })

    # Histogram by anim_bank
    anim_banks = collections.Counter()
    found = sum(1 for r in results if r['terminated'] == 'found')
    for r in results:
        if r['anim_bank'] is not None:
            anim_banks[r['anim_bank']] += 1
    print(f'Total unique target scripts: {len(targets)}')
    print(f'Resolved anim table:        {found}')
    print(f'\nAnim tables found, by chunk that holds them:')
    for bk in sorted(anim_banks):
        print(f'  chunk ${bk:02X}: {anim_banks[bk]:3d} scripts')

    # Show termination histogram
    term = collections.Counter(r['terminated'] for r in results)
    print(f'\nTermination reasons:')
    for k, v in term.most_common():
        print(f'  {k}: {v}')

    with open('docs/script_anim_tables.json', 'w') as f:
        json.dump({'records': results,
                   'opcodes_known': sorted(OPSIZES.keys()),
                   'note': 'For each unique spawn target, walk bytecode until opcode $1A (set anim table) is hit. Returns (anim_lo, anim_hi, anim_bank) on success.'},
                  f, indent=2)
    print('\nWrote docs/script_anim_tables.json')


if __name__ == '__main__':
    main()
