#!/usr/bin/env python3
"""6502 control-flow walker — find every JMP/JSR $CCA7 reachable from
a starting (bank, PC).

Pure static. Given a start point, walks all reachable 6502 instructions
within the bank, following branches, JMP abs, JSR abs (and returning at
RTS). For every JMP/JSR to $CCA7 or $CC4B encountered, captures the
immediately-preceding LDA #/LDY #/LDX # / LDA #+STA $6031 values to
recover (A, Y, X, bank) spawn parameters.

Stops walking on:
- Crossing out of current bank window ($8000-$BFFF for R6/R7, or
  $C000-$DFFF for fixed-penultimate)
- JMP (ind) — indirect target unresolved
- Hitting an already-visited instruction

Usage:
    spawn_walker.py [--bank BANK] [--pc PC] [--quiet]
    spawn_walker.py --start 0x21 0x8CE8                     # walk stage 1 dispatcher
    spawn_walker.py --start-all-orphans                     # walk every orphan in spawn_graph.json
    spawn_walker.py --start-target 0x14 0xA5AE              # walk a known script PC

Outputs:
    Per starting point, prints unique spawn calls reachable, with
    parameters where extractable. Optionally writes to a JSON file.
"""
import argparse
import json
import sys

# Minimal 6502 control-flow table.
# Each opcode -> (size, kind)
#   kind: 'normal', 'branch', 'jmp_abs', 'jmp_ind', 'jsr', 'rts', 'brk', 'rti'
OP = {}

def _add(opcode, size, kind='normal'):
    OP[opcode] = (size, kind)

# Default: fill all opcodes as 1-byte normal (illegal opcode safety)
for i in range(256):
    OP[i] = (1, 'normal')

# Implied (1-byte)
for op in [0x00, 0x40, 0x60, 0x08, 0x28, 0x48, 0x68, 0xAA, 0xA8, 0xBA, 0x8A,
           0x9A, 0x98, 0xCA, 0x88, 0xE8, 0xC8, 0xEA, 0x18, 0x38, 0x58, 0x78,
           0xB8, 0xD8, 0xF8, 0x0A, 0x2A, 0x4A, 0x6A]:
    _add(op, 1)
# Special control
_add(0x00, 1, 'brk')
_add(0x40, 1, 'rti')
_add(0x60, 1, 'rts')

# 2-byte (immediate, zp, zp,x, zp,y, ind,x, ind,y, relative)
for op in [0xA9, 0xA2, 0xA0, 0x69, 0x29, 0x49, 0x09, 0xE9, 0xC9, 0xE0, 0xC0,
           # ZP
           0xA5, 0xA6, 0xA4, 0x85, 0x86, 0x84, 0x65, 0x25, 0x45, 0x05, 0xE5,
           0xC5, 0xE4, 0xC4, 0x06, 0x46, 0x26, 0x66, 0xC6, 0xE6,
           # ZP,X / ZP,Y
           0xB5, 0xB4, 0x95, 0x94, 0x75, 0x35, 0x55, 0x15, 0xF5, 0xD5, 0xB6,
           0x96, 0x16, 0x56, 0x36, 0x76, 0xD6, 0xF6,
           # (ind,X) / (ind),Y
           0xA1, 0x81, 0x61, 0x21, 0x41, 0x01, 0xE1, 0xC1,
           0xB1, 0x91, 0x71, 0x31, 0x51, 0x11, 0xF1, 0xD1]:
    _add(op, 2)
# Branches (2-byte, REL)
for op, _name in [(0x10, 'BPL'), (0x30, 'BMI'), (0x50, 'BVC'), (0x70, 'BVS'),
                  (0x90, 'BCC'), (0xB0, 'BCS'), (0xD0, 'BNE'), (0xF0, 'BEQ')]:
    _add(op, 2, 'branch')

# 3-byte (abs, abs,X, abs,Y, ind)
for op in [0xAD, 0xAE, 0xAC, 0x8D, 0x8E, 0x8C, 0x6D, 0x2D, 0x4D, 0x0D, 0xED,
           0xCD, 0xEC, 0xCC, 0x0E, 0x4E, 0x2E, 0x6E, 0xCE, 0xEE,
           0xBD, 0xBE, 0xBC, 0x9D, 0x99, 0xB9, 0x7D, 0x3D, 0x5D, 0x1D, 0xFD,
           0xD9, 0xDD, 0x1E, 0x5E, 0x3E, 0x7E, 0xDE, 0xFE]:
    _add(op, 3)
# Control 3-byte
_add(0x4C, 3, 'jmp_abs')
_add(0x6C, 3, 'jmp_ind')
_add(0x20, 3, 'jsr')


def walk(rom, start_bank, start_pc, max_steps=10000):
    """Walk 6502 control flow from (start_bank, start_pc).

    Returns dict with:
      visited:   set of CPU addresses visited (within current bank)
      spawns:    list of {site_cpu, target ('CCA7' or 'CC4B'),
                          A, Y, X, bank}
      exits:     list of why-we-stopped reasons (for diagnostics)
    """
    visited = set()
    spawns = []
    exits = []

    def file_of(cpu):
        if 0xA000 <= cpu < 0xC000:
            return 0x10 + start_bank * 0x2000 + (cpu - 0xA000)
        if 0x8000 <= cpu < 0xA000:
            return 0x10 + start_bank * 0x2000 + (cpu - 0x8000)
        return None

    def reg_load_before(pc, max_back=40):
        """Recover LDA #/LDY #/LDX # and (LDA # + STA $6031) preceding pc."""
        out = {'A': None, 'Y': None, 'X': None, 'bank': None}
        fo = file_of(pc)
        if fo is None:
            return out
        # Bank: LDA #imm followed by STA $6031 (8D 31 60)
        j = fo - 1
        end = max(0, fo - max_back)
        while j > end + 2:
            if rom[j] == 0x8D and rom[j+1] == 0x31 and rom[j+2] == 0x60 \
                    and j >= 2 and rom[j-2] == 0xA9:
                out['bank'] = rom[j-1]
                break
            j -= 1
        # A, Y, X — scan back, take the latest immediate-load before a
        # control-flow break.
        for reg, ld_op in [('A', 0xA9), ('Y', 0xA0), ('X', 0xA2)]:
            j = fo - 1
            while j > end:
                if j >= 1 and rom[j-1] == ld_op:
                    out[reg] = rom[j]
                    break
                if rom[j-1] in (0x4C, 0x20, 0x60, 0x40):
                    break
                j -= 1
        return out

    stack = [start_pc]  # work list of PCs to visit
    steps = 0
    while stack and steps < max_steps:
        pc = stack.pop()
        if pc in visited:
            continue
        if not (0x8000 <= pc < 0xE000):
            exits.append(('out_of_bank', pc))
            continue
        fo = file_of(pc)
        if fo is None or fo >= len(rom) - 2:
            exits.append(('oob', pc))
            continue
        visited.add(pc)
        steps += 1
        op = rom[fo]
        size, kind = OP[op]
        operand1 = rom[fo+1] if size >= 2 else None
        operand2 = rom[fo+2] if size >= 3 else None
        target = None
        if kind == 'jmp_abs':
            target = (operand2 << 8) | operand1
        elif kind == 'jsr':
            target = (operand2 << 8) | operand1
        elif kind == 'branch':
            rel = operand1
            if rel >= 0x80:
                rel -= 0x100
            target = pc + 2 + rel

        # Check for spawn calls — JMP/JSR to $CCA7 or $CC4B
        if target == 0xCCA7 or target == 0xCC4B:
            params = reg_load_before(pc)
            spawns.append({
                'site_bank': start_bank,
                'site_cpu': pc,
                'kind': 'JMP' if kind == 'jmp_abs' else 'JSR',
                'target': 'CCA7' if target == 0xCCA7 else 'CC4B',
                **params,
                'target_pc': ((params['Y'] << 8) | params['A']) if params['A'] is not None and params['Y'] is not None else None,
            })
            # JMP $CCA7 = tail-call (don't continue past); JSR returns
            if kind == 'jmp_abs':
                continue
            # JSR: continue at next instruction
            stack.append(pc + size)
            continue

        if kind == 'jmp_abs':
            if target is not None and 0x8000 <= target < 0xE000:
                stack.append(target)
            else:
                exits.append(('jmp_oob', pc, target))
            continue
        if kind == 'jmp_ind':
            exits.append(('jmp_ind', pc))
            continue
        if kind == 'rts' or kind == 'rti' or kind == 'brk':
            continue
        if kind == 'jsr':
            # Follow into the callee
            if target is not None and 0x8000 <= target < 0xE000:
                stack.append(target)
            # Also continue at next instruction
            stack.append(pc + size)
            continue
        if kind == 'branch':
            if target is not None and 0x8000 <= target < 0xE000:
                stack.append(target)
            stack.append(pc + size)
            continue
        # Normal — fall through
        stack.append(pc + size)

    return {'visited': sorted(visited), 'spawns': spawns, 'exits': exits[:20]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rom', default='../reference/rom/kirby.nes')
    ap.add_argument('--bank', type=lambda x: int(x, 0))
    ap.add_argument('--pc', type=lambda x: int(x, 0))
    ap.add_argument('--start-all-orphans', action='store_true',
                    help='Walk every orphan root from spawn_graph.json')
    ap.add_argument('--start-all-scripts', action='store_true',
                    help='Walk every unique target script in spawn_census.json')
    ap.add_argument('--out', help='Save results JSON')
    ap.add_argument('--max-steps', type=int, default=10000)
    args = ap.parse_args()

    with open(args.rom, 'rb') as f:
        rom = f.read()

    results = []
    if args.bank is not None and args.pc is not None:
        r = walk(rom, args.bank, args.pc, args.max_steps)
        results.append({'start': (args.bank, args.pc), **r})
        print_walk(args.bank, args.pc, r)
    elif args.start_all_orphans:
        with open('docs/spawn_graph.json') as f:
            g = json.load(f)
        for o in g['orphan_roots']:
            r = walk(rom, o['bank'], o['pc'], args.max_steps)
            results.append({'start': (o['bank'], o['pc']), **r})
            print_walk(o['bank'], o['pc'], r)
    elif args.start_all_scripts:
        with open('docs/spawn_census.json') as f:
            c = json.load(f)
        seen = set()
        for rec in c['records']:
            if rec.get('target') == 'CCA7' and rec.get('target_pc') and rec.get('bank'):
                key = (rec['bank'], rec['target_pc'])
                if key in seen:
                    continue
                seen.add(key)
                r = walk(rom, rec['bank'], rec['target_pc'], args.max_steps)
                results.append({'start': key, **r})
        # Summarize
        total_spawns = sum(len(r['spawns']) for r in results)
        print(f'Walked {len(results)} scripts, recorded {total_spawns} spawn calls')
    else:
        ap.error('Specify --bank/--pc or --start-all-orphans or --start-all-scripts')

    if args.out:
        with open(args.out, 'w') as f:
            json.dump(results, f, indent=2, default=lambda x: list(x) if hasattr(x, '__iter__') else str(x))
        print(f'Saved {args.out}')


def print_walk(bank, pc, r):
    print(f'\n=== ${bank:02X}:${pc:04X} ===')
    print(f'  visited {len(r["visited"])} instructions')
    if not r['spawns']:
        print(f'  no spawn calls reachable')
        return
    print(f'  {len(r["spawns"])} spawn call(s):')
    for s in r['spawns']:
        kind = s['kind']
        tgt = s['target']
        params = []
        if s['A'] is not None and s['Y'] is not None:
            params.append(f'PC=${(s["Y"]<<8)|s["A"]:04X}')
        elif s['A'] is not None:
            params.append(f'A=${s["A"]:02X}')
        if s['X'] is not None:
            params.append(f'slot=${s["X"]:02X}')
        if s['bank'] is not None:
            params.append(f'bank=${s["bank"]:02X}')
        param_str = ', '.join(params) if params else '?'
        print(f'    site ${s["site_cpu"]:04X}  {kind} ${tgt}  ({param_str})')


if __name__ == '__main__':
    main()
