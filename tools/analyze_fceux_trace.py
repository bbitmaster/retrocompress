#!/usr/bin/env python3
"""Better trace analysis: capture pre+post palette around each decomp, and
the FINAL steady-state palette at end of trace.
"""
import re, sys

path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/kirby_log_start_from_save_load_level.log'

mmc3_select = 0
mmc3_banks = [0]*8
ppu_addr = 0
ppu_phase = 0
ppu_data = []
last_palette = None
palette_writes = []   # list of (line, 32-byte palette)
decomp_events = []

RE_A = re.compile(r'^A:([0-9A-F]{2})')
def get_a(line):
    m = RE_A.match(line)
    return int(m.group(1), 16) if m else 0

with open(path) as f:
    for ln, line in enumerate(f, 1):
        if 'STA $8000' in line:
            mmc3_select = get_a(line) & 0x07
        elif 'STA $8001' in line:
            v = get_a(line)
            mmc3_banks[mmc3_select] = v
        elif 'STA $2006' in line:
            v = get_a(line)
            if ppu_phase == 0:
                ppu_addr = (v << 8) & 0xFF00
                ppu_phase = 1
            else:
                ppu_addr |= v
                ppu_phase = 0
                if 0x3F00 <= ppu_addr <= 0x3F1F:
                    ppu_data = []
                    ppu_palette_active = True
                else:
                    ppu_palette_active = False
        elif 'STA $2007' in line:
            v = get_a(line)
            if 0x3F00 <= ppu_addr <= 0x3F1F:
                ppu_data.append(v)
                if len(ppu_data) >= 32:
                    last_palette = list(ppu_data)
                    palette_writes.append((ln, list(ppu_data)))
                    ppu_data = []
            ppu_addr = (ppu_addr + 1) & 0x3FFF
        elif 'JSR $C43A' in line:
            decomp_events.append({
                'line': ln, 'context': line.strip(),
                'mmc3': list(mmc3_banks),
                'palette_pre': last_palette[:] if last_palette else None,
            })

# Capture post-decomp palette for each event
for i, ev in enumerate(decomp_events):
    # find next palette write after ev['line']
    ev['palette_post'] = None
    for pln, pal in palette_writes:
        if pln > ev['line']:
            ev['palette_post'] = pal
            break

# Print
print(f'{len(decomp_events)} decomps, {len(palette_writes)} palette writes.\n')
for i, ev in enumerate(decomp_events):
    print(f'#{i+1} @ line {ev["line"]}: {ev["context"]}')
    print(f'  MMC3 (R0..R7): {" ".join(f"${v:02X}" for v in ev["mmc3"])}')
    if ev['palette_post']:
        sub = [ev['palette_post'][j*4:(j+1)*4] for j in range(8)]
        print(f'  Next palette (post-decomp):')
        for j, p in enumerate(sub):
            print(f'    sub{j}: {" ".join(f"${b:02X}" for b in p)}')
    else:
        print('  (no post-decomp palette write)')
    print()

# Final steady-state palette
if palette_writes:
    print(f'\nFINAL palette in trace (steady-state at end):')
    final = palette_writes[-1][1]
    for j in range(8):
        p = final[j*4:(j+1)*4]
        print(f'  sub{j}: {" ".join(f"${b:02X}" for b in p)}')

# Also list distinct palettes seen in the trace
seen = set()
distinct = []
for ln, pal in palette_writes:
    t = tuple(pal)
    if t not in seen:
        seen.add(t)
        distinct.append((ln, pal))
print(f'\n{len(distinct)} distinct palettes seen in trace:')
for ln, pal in distinct:
    # Compact one-line repr
    s = ' '.join(f'{b:02X}' for b in pal)
    print(f'  line {ln}: {s}')
