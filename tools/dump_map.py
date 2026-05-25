#!/usr/bin/env python3
"""Decompress a Kirby's Adventure NES map blob from the ROM.

Looks up the pointer tables at $244E1 / $2476F / $248B6 (MAP_BANK / HI / LO,
327 entries), resolves the file offset of map index N, decompresses with a
Python port of the retrocompress decoder, and writes both the raw bytes and
a human-readable dump.

Usage:
    dump_map.py <rom.nes> [--map N | --bank BB --addr AAAA] [--out OUT]
"""
import sys, argparse

INES = 16
BANK_SIZE = 0x2000
MAP_BANK_TBL = 0x244E1
MAP_HI_TBL   = 0x2476F
MAP_LO_TBL   = 0x248B6
MAP_N        = 0x147  # 327

def bitrev_u8(b):
    b = ((b & 0xF0) >> 4) | ((b & 0x0F) << 4)
    b = ((b & 0xCC) >> 2) | ((b & 0x33) << 2)
    b = ((b & 0xAA) >> 1) | ((b & 0x55) << 1)
    return b

def decompress(src, off=0):
    """Decompress retrocompress / Kirby NES stream. Returns (bytes, consumed)."""
    dst = bytearray()
    i = off
    while i < len(src):
        ctrl = src[i]; i += 1
        if ctrl == 0xFF:
            return bytes(dst), i - off
        cmd = ctrl >> 5
        if cmd == 7:
            cmd = (ctrl >> 2) & 7
            ln = (((ctrl & 3) << 8) | src[i]) + 1; i += 1
            if cmd == 7: cmd = 4
        else:
            ln = (ctrl & 0x1F) + 1
        if cmd == 0:
            dst += src[i:i+ln]; i += ln
        elif cmd == 1:
            dst += bytes([src[i]]) * ln; i += 1
        elif cmd == 2:
            a, b = src[i], src[i+1]; i += 2
            for _ in range(ln):
                dst.append(a); dst.append(b)
        elif cmd == 3:
            b = src[i]; i += 1
            for _ in range(ln):
                dst.append(b & 0xFF); b += 1
        elif cmd == 4:
            addr = (src[i] << 8) | src[i+1]; i += 2
            for _ in range(ln):
                dst.append(dst[addr]); addr += 1
        elif cmd == 5:
            addr = (src[i] << 8) | src[i+1]; i += 2
            for _ in range(ln):
                dst.append(bitrev_u8(dst[addr])); addr += 1
        elif cmd == 6:
            addr = (src[i] << 8) | src[i+1]; i += 2
            for _ in range(ln):
                dst.append(dst[addr]); addr -= 1
        else:
            raise RuntimeError(f'bad cmd {cmd}')
    raise RuntimeError('ran off end of stream without $FF terminator')

def file_off_for(bank, cpu_addr):
    return INES + bank * BANK_SIZE + (cpu_addr - 0xA000)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom')
    ap.add_argument('--map', type=lambda x: int(x, 0))
    ap.add_argument('--bank', type=lambda x: int(x, 0))
    ap.add_argument('--addr', type=lambda x: int(x, 0))
    ap.add_argument('--out', default='/tmp/map_dump.bin')
    ap.add_argument('--all', action='store_true', help='List file offsets for all 327 maps')
    args = ap.parse_args()

    with open(args.rom, 'rb') as f:
        rom = f.read()

    if args.all:
        print(f'{"idx":>4} {"bank":>4} {"cpu":>5} {"file":>7} {"first":>5}')
        for i in range(MAP_N):
            bb = rom[MAP_BANK_TBL + i]
            hi = rom[MAP_HI_TBL + i]
            lo = rom[MAP_LO_TBL + i]
            addr = (hi << 8) | lo
            if addr < 0xA000 or addr > 0xBFFF:
                continue
            fo = file_off_for(bb & 0x7F, addr)
            if 0 <= fo < len(rom):
                first = rom[fo]
                print(f'{i:>4} {bb&0x7F:>4} ${addr:04X} {fo:>7X} ${first:02X}')
        return

    if args.bank is not None and args.addr is not None:
        bank = args.bank & 0x7F
        addr = args.addr
        fo = file_off_for(bank, addr)
        # Match a map index if possible
        match = None
        for i in range(MAP_N):
            if (rom[MAP_BANK_TBL + i] & 0x7F) == bank and \
               ((rom[MAP_HI_TBL + i] << 8) | rom[MAP_LO_TBL + i]) == addr:
                match = i; break
        print(f'Decompressing bank=${bank:02X} addr=${addr:04X} file=${fo:X}'
              + (f' (matches map index {match})' if match is not None else ' (no map index match)'))
    elif args.map is not None:
        i = args.map
        bb = rom[MAP_BANK_TBL + i]
        hi = rom[MAP_HI_TBL + i]
        lo = rom[MAP_LO_TBL + i]
        bank = bb & 0x7F
        addr = (hi << 8) | lo
        fo = file_off_for(bank, addr)
        print(f'Map {i}: bank=${bank:02X} cpu=${addr:04X} file=${fo:X}')
    else:
        print('Specify --map N OR --bank/--addr OR --all')
        return

    dec, csz = decompress(rom, fo)
    print(f'Compressed size: {csz} bytes')
    print(f'Decompressed:    {len(dec)} bytes')
    with open(args.out, 'wb') as f:
        f.write(dec)
    print(f'Wrote {args.out}')

    # Hex dump preview
    print('First 256 decompressed bytes:')
    for row in range(0, min(256, len(dec)), 16):
        h = ' '.join(f'{b:02X}' for b in dec[row:row+16])
        print(f'  {row:04X}: {h}')

if __name__ == '__main__':
    main()
