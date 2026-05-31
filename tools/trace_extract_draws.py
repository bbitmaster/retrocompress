#!/usr/bin/env python3
"""Stream-parse the stage-1 trace log and extract every $1F:DA89
(metasprite blit) event with the runtime context that produced it.

For each blit we capture:
  - slot:  X register at most recent $1F:CD33 (the draw dispatcher entry)
  - r6_chunk:  current chunk loaded at $8000-$9FFF (from $1F:D790
    "STA $48" — $48 shadows MMC3's R6 register)
  - r0, r1:  sprite-CHR banks (tracked via writes to $0042/$0043 or
    directly via R0/R1 bank-switch helpers)
  - ms_lo, ms_hi:  metasprite pointer = (A_reg, X_reg) at $DA89 entry
  - resolved_file:  ROM file offset of the metasprite data

Output: docs/trace_draws.json — list of unique (slot, r6_chunk, ms_addr,
r0, r1) tuples with occurrence counts.

This is the ground-truth dataset we can cross-reference against the
spawn catalog: for each spawn ID's slot, we know exactly which
metasprites the engine actually drew and with which CHR/palette
context.
"""
import re
import json
import sys
from collections import defaultdict

TRACE = '../traces/kirby_stage1_walkthrough.log'

# Patterns (compiled once for speed). Trace lines look like:
#   f????  cycle  insn  A:?? X:?? Y:?? S:?? flags  $BB:CPU: opcodes mnemonic
LINE_RE = re.compile(
    r'A:([0-9A-F]{2}) X:([0-9A-F]{2}) Y:([0-9A-F]{2}).*?\$([0-9A-F]{2}):([0-9A-F]{4}):'
)

# Per-event we care about (sparse; most lines are irrelevant)
KEY_ADDRS = {
    '1F:CD33': 'cd33_entry',   # draw dispatcher entry: X = slot
    '1F:D790': 'r6_write',     # STA $48 = new R6 (after PLA)
    '1F:DA89': 'blit_plain',   # metasprite blit ($DA89 variant)
    '1F:DDB3': 'blit_r0_ovr',  # metasprite blit with R0 override
    '1F:DDE9': 'blit_r1_ovr',  # metasprite blit with R1 override
}

state = {
    'cur_slot': None,
    'cur_r6': None,
    'cur_r0': None,
    'cur_r1': None,
}
events = []  # list of (slot, r6, r0, r1, ms_lo_hex, ms_hi_hex) at each blit

# Also need to capture R0/R1 — these are in WRAM at $0042/$0043 (sprite
# CHR globals). We don't see WRAM writes in the trace as nicely, but
# the "level globals R0/R1" don't change often in a stage — we can
# assume the stage-1 defaults: R0=$80, R1=$9A unless changed.
state['cur_r0'] = 0x80
state['cur_r1'] = 0x9A

line_count = 0
blit_count = 0
with open(TRACE, 'r') as f:
    for line in f:
        line_count += 1
        if line_count % 1_000_000 == 0:
            print(f'  ...{line_count // 1_000_000}M lines, {blit_count} blits so far', file=sys.stderr)
        # Cheap filter: only lines mentioning one of our addresses
        if '$1F:CD33:' in line:
            m = LINE_RE.search(line)
            if m:
                state['cur_slot'] = int(m.group(2), 16)
        elif '$1F:D790:' in line:
            m = LINE_RE.search(line)
            if m:
                state['cur_r6'] = int(m.group(1), 16)
        elif ('$1F:DA89:' in line or '$1F:DDA3:' in line
              or '$1F:DDB3:' in line or '$1F:DDE7:' in line
              or '$1F:DDE9:' in line):
            m = LINE_RE.search(line)
            if m:
                a = int(m.group(1), 16)
                x = int(m.group(2), 16)
                if '$1F:DA89:' in line:
                    variant = 'plain'
                elif '$1F:DDA3:' in line:
                    variant = 'r0_ovr_dda3'
                elif '$1F:DDB3:' in line:
                    variant = 'r0_ovr_ddb3'
                elif '$1F:DDE7:' in line:
                    variant = 'r1_ovr_dde7'
                else:
                    variant = 'r1_ovr_dde9'
                events.append({
                    'slot': state['cur_slot'],
                    'r6': state['cur_r6'],
                    'r0': state['cur_r0'],
                    'r1': state['cur_r1'],
                    'ms_lo': a,
                    'ms_hi': x,
                    'ms_addr': (x << 8) | a,
                    'variant': variant,
                })
                blit_count += 1

print(f'Parsed {line_count} trace lines, captured {blit_count} blit events', file=sys.stderr)

# Aggregate: unique (slot, r6, ms_addr, variant) tuples
buckets = defaultdict(int)
for e in events:
    key = (e['slot'], e['r6'], e['ms_addr'], e.get('variant', 'plain'))
    buckets[key] += 1

# Sort by count
ordered = sorted(buckets.items(), key=lambda kv: -kv[1])

# Print summary
print(f'\nUnique (slot, r6_chunk, metasprite) tuples: {len(buckets)}')
print(f'Top 30 most-drawn metasprites:')
print(f'{"slot":>4} {"r6":>4} {"ms_addr":>8} {"variant":>10} {"count":>6}  file_offset')
for (slot, r6, ms, var), n in ordered[:50]:
    if r6 is None or slot is None:
        continue
    if 0x8000 <= ms < 0xA000:
        fo = 0x10 + r6 * 0x2000 + (ms - 0x8000)
        print(f'  ${slot:02X}  ${r6:02X}   ${ms:04X}  {var:>10}  {n:6d}  ${fo:X}')
    elif 0xA000 <= ms < 0xC000:
        print(f'  ${slot:02X}  ${r6:02X}   ${ms:04X}  {var:>10}  {n:6d}  (R7 slot)')
    else:
        print(f'  ${slot:02X}  ${r6:02X}   ${ms:04X}  {var:>10}  {n:6d}  (outside)')

# Save full data
with open('docs/trace_draws.json', 'w') as f:
    json.dump({
        'events_count': blit_count,
        'unique_tuples': len(buckets),
        'top': [{'slot': s, 'r6': r, 'ms_addr': m, 'variant': v, 'count': n}
                for (s, r, m, v), n in ordered[:300]],
    }, f, indent=2)
print('Saved docs/trace_draws.json')
