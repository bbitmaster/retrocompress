#!/usr/bin/env python3
"""Minimal 6502 disassembler for NES ROMs.

Usage:
    disasm.py rom.nes <file_offset> [--cpu CPU_BASE] [--len BYTES]

  file_offset      file offset (hex, e.g. 0x7E660) to start disassembling
  --cpu CPU_BASE   what CPU address corresponds to file_offset (default: try
                   to infer from MMC3 mapping; defaults to $8000 if unknown)
  --len BYTES      how many bytes to disassemble (default 128)

Annotations: known NES + Kirby's-Adventure-specific addresses are labeled
(MMC3 mapper regs, banked WRAM, the decompressor, etc.).
"""

import sys

# Full 256-entry 6502 opcode table. Each entry: (mnemonic, addressing_mode).
# Length is determined by addressing mode. Unofficial / illegal opcodes are
# labeled '???' with mode 'IMP' (treated as 1-byte).
IMP, IMM, ZP, ZPX, ZPY, ABS, ABSX, ABSY, IND, INDX, INDY, REL, ACC = \
    'IMP', 'IMM', 'ZP', 'ZPX', 'ZPY', 'ABS', 'ABSX', 'ABSY', 'IND', 'INDX', 'INDY', 'REL', 'ACC'

MODE_LEN = {IMP:1, IMM:2, ZP:2, ZPX:2, ZPY:2, ABS:3, ABSX:3, ABSY:3,
            IND:3, INDX:2, INDY:2, REL:2, ACC:1}

OP = {}
def _add(opcode, mnem, mode):
    OP[opcode] = (mnem, mode)

# Control
_add(0x00,'BRK',IMP); _add(0x40,'RTI',IMP); _add(0x60,'RTS',IMP); _add(0xEA,'NOP',IMP)
_add(0x4C,'JMP',ABS); _add(0x6C,'JMP',IND); _add(0x20,'JSR',ABS)

# Branches (relative)
_add(0x10,'BPL',REL); _add(0x30,'BMI',REL); _add(0x50,'BVC',REL); _add(0x70,'BVS',REL)
_add(0x90,'BCC',REL); _add(0xB0,'BCS',REL); _add(0xD0,'BNE',REL); _add(0xF0,'BEQ',REL)

# Flags
_add(0x18,'CLC',IMP); _add(0x38,'SEC',IMP); _add(0x58,'CLI',IMP); _add(0x78,'SEI',IMP)
_add(0xB8,'CLV',IMP); _add(0xD8,'CLD',IMP); _add(0xF8,'SED',IMP)

# Stack
_add(0x48,'PHA',IMP); _add(0x68,'PLA',IMP); _add(0x08,'PHP',IMP); _add(0x28,'PLP',IMP)

# Transfers
_add(0xAA,'TAX',IMP); _add(0xA8,'TAY',IMP); _add(0xBA,'TSX',IMP); _add(0x8A,'TXA',IMP)
_add(0x9A,'TXS',IMP); _add(0x98,'TYA',IMP)

# Inc/Dec
_add(0xE8,'INX',IMP); _add(0xC8,'INY',IMP); _add(0xCA,'DEX',IMP); _add(0x88,'DEY',IMP)

# ORA/AND/EOR/ADC/STA/LDA/CMP/SBC (cc=01) — groups of 8 addr modes
# Encoding: opcode = aaa bbb 01 where aaa = operation, bbb = addr mode
op01_mnem = {0:'ORA', 1:'AND', 2:'EOR', 3:'ADC', 4:'STA', 5:'LDA', 6:'CMP', 7:'SBC'}
mode01 = [INDX, ZP, IMM, ABS, INDY, ZPX, ABSY, ABSX]
for aaa in range(8):
    for bbb in range(8):
        # STA #imm doesn't exist (skipped by hardware -> illegal)
        if aaa == 4 and bbb == 2: continue
        opcode = (aaa<<5) | (bbb<<2) | 0b01
        _add(opcode, op01_mnem[aaa], mode01[bbb])

# Group cc=10 — ASL ROL LSR ROR STX LDX DEC INC, with addr modes ACC/ZP/zpx/abs/absx
op10_mnem = {0:'ASL', 1:'ROL', 2:'LSR', 3:'ROR', 4:'STX', 5:'LDX', 6:'DEC', 7:'INC'}
mode10 = [IMM, ZP, ACC, ABS, None, ZPX, None, ABSX]
for aaa in range(8):
    for bbb in range(8):
        if mode10[bbb] is None: continue
        if aaa == 4 and bbb == 0: continue   # STX #imm illegal
        if aaa == 5 and bbb == 0:
            opcode = (5<<5) | (0<<2) | 0b10
            _add(opcode, 'LDX', IMM)
            continue
        if aaa in (4,5):
            if bbb == 5:
                mode = ZPY if aaa==4 else ZPY
                opcode = (aaa<<5) | (bbb<<2) | 0b10
                _add(opcode, op10_mnem[aaa], mode)
                continue
            if aaa == 5 and bbb == 7:
                _add((aaa<<5)|(bbb<<2)|0b10, 'LDX', ABSY); continue
            if aaa == 4 and bbb == 7:
                continue  # STX abs,X illegal
        # ACC vs IMM/IMM/IMM: aaa 0..3 use ACC for bbb=2; aaa 4..7 don't use bbb=2
        if bbb == 2 and aaa >= 4: continue
        opcode = (aaa<<5) | (bbb<<2) | 0b10
        if opcode in OP: continue
        _add(opcode, op10_mnem[aaa], mode10[bbb])

# Group cc=00 — mixed
# BIT abs/zp; STY zp/abs/zpx; LDY imm/zp/abs/zpx/absx; CPY imm/zp/abs; CPX imm/zp/abs
_add(0x24,'BIT',ZP); _add(0x2C,'BIT',ABS)
_add(0x84,'STY',ZP); _add(0x94,'STY',ZPX); _add(0x8C,'STY',ABS)
_add(0xA0,'LDY',IMM); _add(0xA4,'LDY',ZP); _add(0xB4,'LDY',ZPX); _add(0xAC,'LDY',ABS); _add(0xBC,'LDY',ABSX)
_add(0xC0,'CPY',IMM); _add(0xC4,'CPY',ZP); _add(0xCC,'CPY',ABS)
_add(0xE0,'CPX',IMM); _add(0xE4,'CPX',ZP); _add(0xEC,'CPX',ABS)

# Named addresses for annotations
NES_REGS = {
    0x2000:'PPUCTRL', 0x2001:'PPUMASK', 0x2002:'PPUSTATUS', 0x2003:'OAMADDR',
    0x2004:'OAMDATA', 0x2005:'PPUSCROLL', 0x2006:'PPUADDR', 0x2007:'PPUDATA',
    0x4014:'OAMDMA', 0x4015:'APU_STATUS', 0x4016:'JOY1', 0x4017:'JOY2',
    0x8000:'MMC3_BANK_SEL', 0x8001:'MMC3_BANK_DATA',
    0xA000:'MMC3_MIRRORING', 0xA001:'MMC3_PRG_RAM',
    0xC000:'MMC3_IRQ_LATCH', 0xC001:'MMC3_IRQ_RELOAD',
    0xE000:'MMC3_IRQ_DISABLE', 0xE001:'MMC3_IRQ_ENABLE',
}
KIRBY_LABELS = {
    0xC43A: 'Kirby_Decompressor',
    0xF052: 'BankSet_R7_A',         # set R7 ($A000-$BFFF) to bank in A
    0xF062: 'BankSet_R7_from_0576', # set R7 from RAM $0576
    0x0016: 'src_lo',
    0x0017: 'src_hi',
    0x0018: 'dst_lo',
    0x0019: 'dst_hi',
}

def label_addr(addr):
    if addr in NES_REGS: return f'${addr:04X} ({NES_REGS[addr]})'
    if addr in KIRBY_LABELS: return f'${addr:04X} ({KIRBY_LABELS[addr]})'
    return f'${addr:04X}'

def fmt_operand(mode, b1, b2, pc):
    if mode == IMP or mode == ACC: return ''
    if mode == IMM: return f'#${b1:02X}'
    if mode == ZP:  return label_addr(b1) if b1 in KIRBY_LABELS else f'${b1:02X}'
    if mode == ZPX: return f'${b1:02X},X'
    if mode == ZPY: return f'${b1:02X},Y'
    if mode == ABS: return label_addr(b1 | (b2<<8))
    if mode == ABSX: addr = b1|(b2<<8); return f'{label_addr(addr)},X'
    if mode == ABSY: addr = b1|(b2<<8); return f'{label_addr(addr)},Y'
    if mode == IND: addr = b1|(b2<<8); return f'({label_addr(addr)})'
    if mode == INDX: return f'(${b1:02X},X)'
    if mode == INDY: return f'(${b1:02X}),Y'
    if mode == REL:
        offset = b1 if b1 < 0x80 else b1 - 0x100
        return f'${(pc + 2 + offset) & 0xFFFF:04X}'
    return f'?{b1:02X}'

def disasm(rom, file_off, cpu_base, length):
    out = []
    i = file_off
    end = file_off + length
    pc = cpu_base
    while i < end and i < len(rom):
        opcode = rom[i]
        if opcode in OP:
            mnem, mode = OP[opcode]
            ln = MODE_LEN[mode]
            b1 = rom[i+1] if ln >= 2 and i+1 < len(rom) else 0
            b2 = rom[i+2] if ln >= 3 and i+2 < len(rom) else 0
            operand = fmt_operand(mode, b1, b2, pc)
            bytes_str = ' '.join(f'{rom[i+k]:02X}' for k in range(ln))
            out.append(f'  {pc:04X}  {i:06X}  {bytes_str:<10}  {mnem} {operand}'.rstrip())
            i += ln; pc += ln
        else:
            out.append(f'  {pc:04X}  {i:06X}  {opcode:02X}          .byte ${opcode:02X}')
            i += 1; pc += 1
    return out

def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr); sys.exit(1)
    rom_path = sys.argv[1]
    file_off = int(sys.argv[2], 0)
    cpu_base = None
    length = 128
    args = sys.argv[3:]
    while args:
        a = args.pop(0)
        if a == '--cpu': cpu_base = int(args.pop(0), 0)
        elif a == '--len': length = int(args.pop(0), 0)
    with open(rom_path,'rb') as f: rom = f.read()
    # iNES header
    if rom[:4] == b'NES\x1a':
        prg_offset = file_off
    else:
        prg_offset = file_off
    if cpu_base is None:
        # Infer from MMC3: assume bank N is in slot that maps to $8000+
        prg = prg_offset - 0x10  # remove iNES header
        bank = prg // 0x2000
        in_bank = prg % 0x2000
        # default guess: bank mapped to $8000-$9FFF
        cpu_base = 0x8000 + in_bank
    lines = disasm(rom, file_off, cpu_base, length)
    print(f'# Disassembly of {rom_path} @ file 0x{file_off:X}, CPU ${cpu_base:04X} ({length} bytes)')
    for line in lines: print(line)

if __name__ == '__main__': main()
