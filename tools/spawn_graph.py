#!/usr/bin/env python3
"""Build the static spawn graph.

Each spawn site (JMP/JSR $CCA7) in the ROM is located somewhere inside a
6502 function. That function is itself a "script" — and another spawn
site somewhere upstream brought it into being. So:

  caller_script  --[spawns]-->  target_script  --[spawns]-->  ...

We approximate `caller_script` by finding the closest preceding spawn
target (in the same PRG chunk, lower PC than the JMP $CCA7 site) — i.e.
the most plausible "owning" function entry. This is a heuristic; it
catches the common case where scripts are laid out as flat functions
in the ROM, one per (bank, PC). It will be wrong when callers and
callees overlap, but for the purpose of finding orphan stage-entries
it's good enough.

Outputs:
  - docs/spawn_graph.json — full edge list + per-script summary
  - prints orphan roots (scripts not spawned by any other script) — these
    are stage-entry candidates.
"""
import json
import collections

with open('docs/spawn_census.json') as f:
    census = json.load(f)


def caller_chunk_pc(rec):
    """Return (chunk, pc) for the caller site, guessing slot $A000 if rem<$2000."""
    return rec['chunk'], rec['cpu_hi']  # spawn sites are almost always in A000-BFFF


# Build the set of all known scripts (target_pc + bank).
scripts = set()
for r in census['records']:
    if r.get('target') == 'CCA7' and r.get('target_pc') and r.get('bank'):
        scripts.add((r['bank'], r['target_pc']))

# For each spawn site, find the closest preceding script-entry (bank, pc)
# in the same chunk that is <= the caller PC.
chunk_scripts = collections.defaultdict(list)
for bk, pc in scripts:
    chunk_scripts[bk].append(pc)
for bk in chunk_scripts:
    chunk_scripts[bk].sort()


def find_owning_script(chunk, pc):
    """Find the script entry in this chunk whose PC is the largest <= pc."""
    candidates = chunk_scripts.get(chunk, [])
    best = None
    for cpc in candidates:
        if cpc <= pc:
            best = cpc
        else:
            break
    return best


# Build edges: (caller_script) -> (target_script, slot)
edges = []
unresolved = 0
for r in census['records']:
    if r.get('target') != 'CCA7' or not r.get('target_pc') or not r.get('bank'):
        unresolved += 1
        continue
    caller_chunk, caller_cpu_hi = caller_chunk_pc(r)
    caller_pc = find_owning_script(caller_chunk, caller_cpu_hi)
    target = (r['bank'], r['target_pc'])
    if caller_pc is None:
        # Caller chunk has no known scripts — this is likely a top-level
        # spawn from engine code (not from another script).
        edges.append({
            'caller': None,
            'caller_chunk': caller_chunk,
            'caller_site_cpu': caller_cpu_hi,
            'target_bank': target[0],
            'target_pc': target[1],
            'slot': r.get('X'),
            'note': 'engine-level spawn (caller chunk holds no known scripts)',
        })
    else:
        edges.append({
            'caller': {'bank': caller_chunk, 'pc': caller_pc},
            'caller_site_cpu': caller_cpu_hi,
            'target_bank': target[0],
            'target_pc': target[1],
            'slot': r.get('X'),
        })

# Reverse edges: target -> list of callers
reverse = collections.defaultdict(list)
for e in edges:
    tgt = (e['target_bank'], e['target_pc'])
    if e['caller']:
        reverse[tgt].append((e['caller']['bank'], e['caller']['pc']))
    else:
        reverse[tgt].append(('engine', e['caller_chunk']))

# Identify orphan roots: scripts with no caller (or caller is 'engine')
orphans = []
internal_spawned = set()
for s in scripts:
    callers = reverse.get(s, [])
    has_script_caller = any(c[0] != 'engine' for c in callers)
    if not has_script_caller:
        orphans.append((s, callers))
    else:
        internal_spawned.add(s)

# Forward edges: caller -> list of targets
forward = collections.defaultdict(list)
for e in edges:
    if e['caller']:
        src = (e['caller']['bank'], e['caller']['pc'])
        forward[src].append((e['target_bank'], e['target_pc']))


def reachable_from(start_bank, start_pc, max_depth=20):
    """Closure of spawn-graph reachability."""
    seen = set()
    frontier = {(start_bank, start_pc)}
    while frontier and len(seen) < 500:
        new_frontier = set()
        for n in frontier:
            if n in seen:
                continue
            seen.add(n)
            for t in forward.get(n, []):
                if t not in seen:
                    new_frontier.add(t)
        frontier = new_frontier
    return seen


print(f'Total scripts (unique bank,PC): {len(scripts)}')
print(f'Total spawn edges:              {len(edges)}')
print(f'Unresolved spawn calls:         {unresolved}')
print(f'Orphan roots (no script caller): {len(orphans)}')
print()
print('=== ORPHAN ROOTS — stage-entry candidates ===')
for (bk, pc), callers in sorted(orphans):
    reach = reachable_from(bk, pc)
    eng_callers = [c for c in callers if c[0] == 'engine']
    print(f'  ${bk:02X}:${pc:04X}  reaches {len(reach):3d} scripts  ({len(eng_callers)} engine caller(s))')

# Save graph
with open('docs/spawn_graph.json', 'w') as f:
    serializable = {
        'scripts': sorted(scripts),
        'edges': edges,
        'orphan_roots': [
            {'bank': bk, 'pc': pc,
             'reachable_count': len(reachable_from(bk, pc)),
             'engine_callers': len([c for c in callers if c[0] == 'engine']),
             'reachable': sorted(reachable_from(bk, pc))}
            for (bk, pc), callers in sorted(orphans)
        ],
        'forward_edges_by_caller': {f'${bk:02X}:${pc:04X}': [f'${b:02X}:${p:04X}' for b, p in tgts]
                                    for (bk, pc), tgts in sorted(forward.items())},
        'reverse_edges_by_target': {f'${bk:02X}:${pc:04X}': [f'{a}:${b:04X}' if a != 'engine' else f'engine:chunk_${b:02X}' for a, b in c]
                                     for (bk, pc), c in sorted(reverse.items())},
    }
    json.dump(serializable, f, indent=2)
print('\nSaved docs/spawn_graph.json')
