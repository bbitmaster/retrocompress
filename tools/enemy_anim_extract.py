#!/usr/bin/env python3
"""For each spawn target in enemy_catalog.json, scan the first ~32 bytes
of its bytecode looking for opcode $1A (set anim table). Report the
(anim_lo, anim_hi, anim_bank) tuple so we can render the enemy.

Uses a simple heuristic: try every position 0..31 as a potential start
of a $1A instruction (since we don't know exact opcode boundaries
without full VM simulation). Cross-check against the OPSIZES table for
plausibility — only accept a $1A if the bytes before it parse cleanly
under our known opcode size table.

Output: docs/enemy_anim_tables.json
"""
import json

OPSIZES = {
    0x00: -1, 0x01: -1, 0x02: -1, 0x03: -1, 0x04: -1, 0x05: -1, 0x06: 2,
    0x07: 3, 0x08: 4, 0x09: -1, 0x0A: -1, 0x0B: -1, 0x0C: 1, 0x0D: 3,
    0x0E: 1, 0x0F: -1, 0x10: -1, 0x11: 4, 0x12: 4, 0x13: -1, 0x14: -1,
    0x15: -1, 0x16: -1, 0x17: -1, 0x18: -1, 0x19: -1, 0x1A: 4, 0x1B: 2,
    0x1C: 3, 0x1D: 2, 0x1E: 2, 0x1F: 2, 0x20: 3, 0x21: 3, 0x22: -1,
    0x23: 3, 0x24: 2, 0x25: -1, 0x26: 1, 0x27: 5, 0x28: 2, 0x29: -1,
    0x2A: 3, 0x2B: 3, 0x2C: 3, 0x2D: 3, 0x2E: 3, 0x2F: 3, 0x30: 3,
    0x31: 3, 0x32: 3, 0x33: 3, 0x34: 3, 0x35: 3, 0x36: 3, 0x37: 3,
    0x38: 1, 0x39: 1, 0x3A: 3, 0x3B: 3, 0x3C: 3, 0x3D: 3, 0x3E: -1,
    0x3F: -1,
}


def scan_for_op_1a(rom, fo, max_search=40):
    """Walk bytecode forward using OPSIZES; return offset of $1A or None."""
    pos = 0
    while pos < max_search:
        op = rom[fo + pos]
        if op == 0x1A:
            return pos
        if op >= 0x50:  # wait — stops linear scan
            return None
        sz = OPSIZES.get(op, -1)
        if sz < 0:
            return None
        pos += sz
    return None


def main():
    with open('../reference/rom/kirby.nes', 'rb') as f:
        rom = f.read()
    with open('docs/enemy_catalog.json') as f:
        cat = json.load(f)

    # Collect unique targets
    targets = {}
    for s in cat['sites']:
        if s['spawn_id'] is not None and s['target_pc']:
            targets[(s['target_bank'], s['target_pc'])] = s['spawn_id']
        if s['indexed_table']:
            for e in s['indexed_table']['entries']:
                targets[(e['target_bank'], e['target_pc'])] = e['spawn_id']

    records = []
    print(f'{"id":>4} | {"script":>11} | {"anim table":>11} | scanned')
    print('-' * 60)
    for (bk, pc), sid in sorted(targets.items(), key=lambda kv: kv[1]):
        if pc >= 0xA000:
            fo = 0x10 + bk * 0x2000 + (pc - 0xA000)
        else:
            fo = 0x10 + bk * 0x2000 + (pc - 0x8000)
        off = scan_for_op_1a(rom, fo)
        anim_str = '-'
        anim_lo = anim_hi = anim_bank = None
        if off is not None:
            anim_lo = rom[fo + off + 1]
            anim_hi = rom[fo + off + 2]
            anim_bank = rom[fo + off + 3]
            anim_str = f'${anim_bank:02X}:${(anim_hi<<8)|anim_lo:04X}'
        records.append({
            'spawn_id': sid,
            'script_bank': bk,
            'script_pc': pc,
            'op_1a_at_offset': off,
            'anim_lo': anim_lo,
            'anim_hi': anim_hi,
            'anim_bank': anim_bank,
        })
        print(f'${sid:02X}  | ${bk:02X}:${pc:04X} | {anim_str:>11} | offset={off}')

    with open('docs/enemy_anim_tables.json', 'w') as f:
        json.dump(records, f, indent=2)
    found = sum(1 for r in records if r['anim_bank'] is not None)
    print(f'\n{found}/{len(records)} enemy scripts have a discoverable $1A')


if __name__ == '__main__':
    main()
