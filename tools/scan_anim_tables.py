#!/usr/bin/env python3
"""Scan known metasprite chunks for ALL pointer-tables that look like
anim tables.

Pattern we've learned from the trace + RE:
  - Anim table = consecutive 2-byte little-endian pointers
  - All pointers in slot range ($8000-$BFFF)
  - Each pointer targets a valid metasprite within the SAME chunk's
    slot range
  - A valid metasprite has either:
      * plain format: byte0 = count (1..20), then count*[dx,dy,tile,attr]
        with all dx/dy in -64..64 range
      * R0-override format: byte0 in known R1 bank set, byte1 = count
        (1..20), then count*[dx,dy,tile,attr]
  - Table length >= 2 entries (otherwise too many false positives)

This catches anim tables that brute-force $1A search missed (because
they're not pointed to by any $1A in our enumerated scripts), and lets
us verify the ones we did find.
"""
import json
import sys

ROM = '../reference/rom/kirby.nes'
KNOWN_CHUNKS = [0x1A, 0x1C, 0x30, 0x31, 0x32, 0x33, 0x37, 0x3A]
R1_BANKS = {0x3C, 0x9A, 0x9C, 0x9E, 0xA0, 0xA2, 0xA8, 0xAA, 0xAC, 0xAE,
            0xC4, 0xCA, 0xCC, 0xD2, 0xD6, 0xE8, 0xEC, 0xEE}


def is_valid_metasprite(rom, chunk, ptr):
    """Check whether (chunk, ptr) points to a plausible metasprite.

    Tries plain format first, then R0-override. Returns ('plain', count)
    or ('r0_ovr', count, r0) or None.
    """
    if not (0x8000 <= ptr < 0xC000):
        return None
    if 0x8000 <= ptr < 0xA000:
        fo = 0x10 + chunk * 0x2000 + (ptr - 0x8000)
    else:
        fo = 0x10 + chunk * 0x2000 + (ptr - 0xA000)
    if fo >= len(rom):
        return None

    def validate(ms_start, max_count):
        if ms_start + 1 >= len(rom):
            return None
        count = rom[ms_start]
        if not (1 <= count <= max_count):
            return None
        end = ms_start + 1 + count * 4
        if end > len(rom):
            return None
        # Check each entry's dx, dy are sane
        for i in range(count):
            dx = rom[ms_start + 1 + i*4]
            dy = rom[ms_start + 1 + i*4 + 1]
            sdx = dx - 256 if dx >= 128 else dx
            sdy = dy - 256 if dy >= 128 else dy
            if not (-64 <= sdx <= 64) or not (-64 <= sdy <= 64):
                return None
        return count

    c = validate(fo, 16)
    if c is not None:
        return ('plain', c)
    # Try R0-override (byte 0 is CHR bank)
    if rom[fo] in R1_BANKS:
        c = validate(fo + 1, 16)
        if c is not None:
            return ('r0_ovr', c, rom[fo])
    return None


def scan_chunk(rom, chunk):
    """Find all candidate anim tables in this chunk's slot ranges
    ($8000-$9FFF and $A000-$BFFF as separate spaces)."""
    chunk_base = 0x10 + chunk * 0x2000
    # Try every 2-byte-aligned position in the chunk
    candidates = []
    for slot_base in (0x8000, 0xA000):
        for off in range(0, 0x2000, 1):  # any offset (not just aligned)
            anim_addr = slot_base + off
            anim_fo = chunk_base + off
            # Read pointers until one fails the slot-range test or
            # points to an invalid metasprite
            ptrs = []
            ok = True
            i = 0
            while i < 32 and anim_fo + i*2 + 1 < chunk_base + 0x2000:
                lo = rom[anim_fo + i*2]
                hi = rom[anim_fo + i*2 + 1]
                ptr = (hi << 8) | lo
                # Stop if ptr leaves slot range
                if not (slot_base <= ptr < slot_base + 0x2000):
                    break
                # Validate metasprite
                vm = is_valid_metasprite(rom, chunk, ptr)
                if vm is None:
                    break
                ptrs.append((ptr, vm))
                i += 1
            # Accept tables with at least 3 entries — fewer = too many
            # false positives
            if len(ptrs) >= 3:
                candidates.append({
                    'slot_base': slot_base,
                    'anim_addr': anim_addr,
                    'anim_file': anim_fo,
                    'count': len(ptrs),
                    'first_ptr': ptrs[0][0],
                    'first_kind': ptrs[0][1][0],
                })
    return candidates


def main():
    with open(ROM, 'rb') as f:
        rom = f.read()
    results = {}
    for chunk in KNOWN_CHUNKS:
        cands = scan_chunk(rom, chunk)
        # Deduplicate: prefer LONGEST runs and discard subsequences.
        # Sort by (anim_addr, -count); skip those that are continuations
        # of an earlier table.
        cands.sort(key=lambda c: (c['slot_base'], c['anim_addr'], -c['count']))
        kept = []
        last_end = {}
        for c in cands:
            slot = c['slot_base']
            # If this candidate starts inside an earlier table's range, skip
            prev_end = last_end.get(slot, 0)
            if c['anim_addr'] < prev_end:
                continue
            kept.append(c)
            last_end[slot] = c['anim_addr'] + c['count'] * 2
        results[chunk] = kept
        print(f'chunk ${chunk:02X}: {len(kept)} candidate anim table(s)')
        for c in kept[:15]:
            print(f'  ${chunk:02X}:${c["anim_addr"]:04X}  file ${c["anim_file"]:X}  '
                  f'len={c["count"]:2d}  first_ptr=${c["first_ptr"]:04X}  ({c["first_kind"]})')
        if len(kept) > 15:
            print(f'  ... and {len(kept) - 15} more')

    # Save
    with open('docs/anim_tables_discovered.json', 'w') as f:
        json.dump({
            'chunks_scanned': [f'${c:02X}' for c in KNOWN_CHUNKS],
            'tables_by_chunk': {
                f'${chunk:02X}': [
                    {'anim_addr': f'${c["anim_addr"]:04X}',
                     'anim_file': f'${c["anim_file"]:X}',
                     'len': c['count'],
                     'first_ptr': f'${c["first_ptr"]:04X}',
                     'first_kind': c['first_kind']}
                    for c in cs
                ]
                for chunk, cs in results.items()
            },
        }, f, indent=2)
    total = sum(len(cs) for cs in results.values())
    print(f'\nTotal: {total} candidate anim tables across {len(KNOWN_CHUNKS)} chunks')
    print('Saved docs/anim_tables_discovered.json')


if __name__ == '__main__':
    main()
