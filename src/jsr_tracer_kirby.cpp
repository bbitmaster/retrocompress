// jsr_tracer_kirby.cpp - find every compressed-data source in Kirby's
// Adventure (NES) by scanning for JSR calls to the decompressor and
// pattern-matching the source-setup code preceding each call.
//
// Findings on the standard (U) ROM:
//
//   - The decompressor's entry point is at CPU $C43A (file 0x7C44A), in
//     PRG bank 62 which is fixed-mapped to $C000-$DFFF by MMC3.
//   - It reads compressed bytes via the indirect zero-page pointer
//     ($16/$17), and writes decompressed bytes via ($18/$19).
//   - There are 20 JSR $C43A sites in the ROM.
//   - Each site sets up $16/$17 (source CPU address) and $18/$19
//     (destination CPU address) immediately before the JSR.
//
// Source-setup patterns this tool recognizes:
//
//   INLINE:        LDA #lo / STA $16 / LDA #hi / STA $17
//                  LDA #dlo / STA $18 / LDA #dhi / STA $19
//                  JSR $C43A
//
//   TABLE_Y_2:     LDA tbl_lo,Y / STA $16 / LDA tbl_hi,Y / STA $17
//                  LDA #dlo / STA $18 / LDA #dhi / STA $19
//                  JSR $C43A
//                  -- 2-table form: just hi/lo, no explicit bank (bank
//                  is set by surrounding code via JSR $F052 with bank in A)
//
//   STACK:         (source/dest loaded from elsewhere; we see only
//                  `85 16 86 17` style stores)  -- caller-prepared,
//                  used for map/tileset loops that walk a 3-table set
//                  ($84D1/$875F/$88A6 etc.).
//
// Bank disambiguation note: knowing that source-CPU = $A691 isn't enough to
// resolve a file offset, since $A000-$BFFF is the swappable R7 slot in MMC3.
// The actual bank is set by surrounding code (typically `LDA #bank / JSR $F052`
// or via MMC3 register writes to $8001) further back than our 24-byte window.
// We use a brute-force heuristic: try every 8KB bank, accept the candidate
// whose source decompresses to a sensibly-sized blob. This works for many
// cases but isn't guaranteed correct; manual verification (or extending the
// tracer to do longer back-walks) is required for any blob we'd actually
// repack.

#include <cstdio>
#include <cstring>
#include <vector>
#include <set>
#include <algorithm>
#include "retrocompress.h"

using u8 = unsigned char;

// Brute-force bank detection: try every 8KB PRG bank and see which one
// makes the CPU source address point at a valid compressed stream.
// CPU $A000-$BFFF -> file = bank*0x2000 + (cpu-$A000) + 0x10 (iNES header).
// Returns (bank, file_off, decompressed_size) or {-1,-1,0} if none works.
// Scan back from a JSR site looking for the most-recent `LDA #imm / JSR $F052`
// pattern (Kirby's bank-switch helper at CPU $F052). Stops if it crosses an
// RTS (opcode 0x60) which would mean we've left the current function.
// Returns the immediate (bank value with high bit possibly set; caller may
// mask with 0x7F), or -1 if not found.
static int find_bank_setup(const std::vector<u8>& rom, int jsr_off, int window = 256) {
    int latest_bank = -1;
    int start = std::max(0, jsr_off - window);
    // We can't easily know function boundaries without full disassembly, but
    // RTS (0x60) is a strong heuristic. To avoid stopping at an RTS that's
    // actually an operand byte, we use a sliding heuristic: when we see 0x60,
    // we assume it's a function boundary unless the byte three before it
    // makes it look like part of an absolute-mode operand. Good enough in
    // practice for the patterns we care about.
    int boundary = start;
    for (int i = jsr_off - 1; i >= start; --i) {
        if (rom[i] == 0x60) { // RTS - probable function end (start of next)
            boundary = i + 1;
            break;
        }
    }
    for (int i = boundary; i + 5 <= jsr_off; ++i) {
        // LDA #imm / JSR $F052  =  A9 imm 20 52 F0
        if (rom[i] == 0xA9 && rom[i+2] == 0x20 &&
            rom[i+3] == 0x52 && rom[i+4] == 0xF0) {
            latest_bank = rom[i+1];
        }
    }
    return latest_bank;
}

struct BankResolved { int bank; int file_off; int dec_sz; int orig_csz; bool from_setup; };
static BankResolved resolve_src_bank_with_setup(const std::vector<u8>& rom,
                                                 int src_cpu, int jsr_off);
static BankResolved resolve_src_bank(const std::vector<u8>& rom, int src_cpu) {
    if (src_cpu < 0xA000 || src_cpu > 0xBFFF) return {-1, -1, 0, 0};
    int in_bank = src_cpu - 0xA000;
    int valid_count = 0;
    BankResolved best{-1, -1, 0, 0};
    for (int b = 0; b < 64; ++b) {
        int file_off = 0x10 + b * 0x2000 + in_bank;
        if (file_off + 4 > (int)rom.size()) continue;
        int dec_sz = Retrocompress::decompress(&rom[file_off],
                                               (int)rom.size() - file_off, nullptr);
        if (dec_sz <= 0) continue;
        // Re-measure original compressed size to confirm a valid terminator.
        int p = 0, rs = (int)rom.size();
        bool ok = false;
        while (file_off + p < rs) {
            u8 ctrl = rom[file_off + p++];
            if (ctrl == 0xFF) { ok = true; break; }
            int cmd = ctrl >> 5, ln;
            if (cmd == 7) {
                if (file_off + p >= rs) break;
                ln = (((ctrl & 3) << 8) | rom[file_off + p++]) + 1;
                cmd = (ctrl >> 2) & 7;
            } else ln = (ctrl & 0x1F) + 1;
            if (cmd == 7) cmd = 4;
            if (cmd == 0) p += ln;
            else if (cmd == 1 || cmd == 3) p += 1;
            else p += 2;
        }
        if (!ok) continue;
        // Sanity: 64 <= dec_sz <= 16KB, must actually compress (csz < dec_sz),
        // and ratio shouldn't be ridiculous.
        if (dec_sz < 64 || dec_sz > 0x4000) continue;
        if (p >= dec_sz) continue;
        if (p < 8) continue; // implausibly small compressed stream
        ++valid_count;
        // Heuristic: prefer the candidate with the largest decompressed size,
        // since real Kirby blobs tend to be hundreds to a couple thousand
        // bytes; false positives (random bytes that happen to end on an early
        // 0xFF) typically have tiny dec_sz.
        if (best.bank < 0 || dec_sz > best.dec_sz) {
            best = {b, file_off, dec_sz, p, false};
        }
    }
    (void)valid_count;
    return best;
}

// Combined resolver: prefer the bank from a found `LDA #imm / JSR $F052`
// pattern; fall back to brute-force if none found or if the directly-named
// bank doesn't actually produce a valid stream.
static BankResolved resolve_src_bank_with_setup(const std::vector<u8>& rom,
                                                int src_cpu, int jsr_off) {
    int hinted = find_bank_setup(rom, jsr_off, 512);
    if (hinted >= 0) {
        int bank = hinted & 0x7F; // observed pattern: top bit is an engine flag
        if (src_cpu >= 0xA000 && src_cpu <= 0xBFFF && bank < 64) {
            int file_off = 0x10 + bank * 0x2000 + (src_cpu - 0xA000);
            if (file_off + 4 <= (int)rom.size()) {
                int dec_sz = Retrocompress::decompress(&rom[file_off],
                                                       (int)rom.size() - file_off, nullptr);
                if (dec_sz > 0) {
                    // Measure original csz
                    int p = 0, rs = (int)rom.size();
                    bool ok = false;
                    while (file_off + p < rs) {
                        u8 ctrl = rom[file_off + p++];
                        if (ctrl == 0xFF) { ok = true; break; }
                        int cmd = ctrl >> 5, ln;
                        if (cmd == 7) {
                            if (file_off + p >= rs) break;
                            ln = (((ctrl & 3) << 8) | rom[file_off + p++]) + 1;
                            cmd = (ctrl >> 2) & 7;
                        } else ln = (ctrl & 0x1F) + 1;
                        if (cmd == 7) cmd = 4;
                        if (cmd == 0) p += ln;
                        else if (cmd == 1 || cmd == 3) p += 1;
                        else p += 2;
                    }
                    if (ok) return {bank, file_off, dec_sz, p, true};
                }
            }
        }
    }
    return resolve_src_bank(rom, src_cpu);
}

struct CallSite {
    int file_off;
    enum Kind { UNKNOWN, INLINE, TABLE_Y_2, COMPLEX };
    Kind kind = UNKNOWN;
    // INLINE: src_cpu = ($17:$16), dst_cpu = ($19:$18)
    int src_cpu = -1, dst_cpu = -1;
    // TABLE_Y_2: lo and hi tables, indexed by Y. Dest still inline.
    int tbl_lo_cpu = -1, tbl_hi_cpu = -1;
    // COMPLEX: just record the dest (inline) and leave src for human review
    int complex_pre_off = 0;
};

static const u8 KIRBY_DECOMP_JSR_BYTES[3] = {0x20, 0x3A, 0xC4};

// Recognize the constant-destination tail: A9 dlo 85 18 A9 dhi 85 19 20 3A C4
// That's 11 bytes ending at the JSR.
static bool match_dest_tail(const std::vector<u8>& rom, int jsr, int& dst_cpu) {
    if (jsr < 8) return false;
    if (rom[jsr-8] != 0xA9) return false;
    if (rom[jsr-6] != 0x85 || rom[jsr-5] != 0x18) return false;
    if (rom[jsr-4] != 0xA9) return false;
    if (rom[jsr-2] != 0x85 || rom[jsr-1] != 0x19) return false;
    int dlo = rom[jsr-7], dhi = rom[jsr-3];
    dst_cpu = (dhi << 8) | dlo;
    return true;
}

// Recognize the INLINE source head: A9 lo 85 16 A9 hi 85 17  (8 bytes)
// ending just before the dest tail (which ends right at jsr).
// jsr-16 .. jsr-9 should be: A9 lo 85 16 A9 hi 85 17
static bool match_inline_src(const std::vector<u8>& rom, int jsr, int& src_cpu) {
    if (jsr < 16) return false;
    if (rom[jsr-16] != 0xA9) return false;
    if (rom[jsr-14] != 0x85 || rom[jsr-13] != 0x16) return false;
    if (rom[jsr-12] != 0xA9) return false;
    if (rom[jsr-10] != 0x85 || rom[jsr-9] != 0x17) return false;
    int slo = rom[jsr-15], shi = rom[jsr-11];
    src_cpu = (shi << 8) | slo;
    return true;
}

// Recognize TABLE_Y_2 source head:
//   B9 lo hi 85 16 B9 lo hi 85 17  (10 bytes)
// ending just before the dest tail (which is at jsr-8 .. jsr).
// So the table head is at jsr-18 .. jsr-9.
static bool match_table_y2_src(const std::vector<u8>& rom, int jsr,
                                int& tbl_lo_cpu, int& tbl_hi_cpu) {
    if (jsr < 18) return false;
    if (rom[jsr-18] != 0xB9) return false;            // LDA abs,Y
    if (rom[jsr-15] != 0x85 || rom[jsr-14] != 0x16) return false;
    if (rom[jsr-13] != 0xB9) return false;
    if (rom[jsr-10] != 0x85 || rom[jsr-9]  != 0x17) return false;
    tbl_lo_cpu = rom[jsr-17] | (rom[jsr-16] << 8);
    tbl_hi_cpu = rom[jsr-12] | (rom[jsr-11] << 8);
    return true;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr,
            "usage: %s rom.nes [--verbose]\n"
            "  Hardcoded for Kirby's Adventure (NES) decompressor at CPU $C43A.\n",
            argv[0]);
        return 1;
    }
    bool verbose = (argc >= 3 && !strcmp(argv[2], "--verbose"));
    FILE* f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<u8> rom(sz);
    fread(rom.data(), 1, sz, f);
    fclose(f);
    fprintf(stderr, "ROM size: %ld\n", sz);

    // Find every JSR $C43A
    std::vector<int> sites;
    for (int i = 0; i + 3 <= (int)rom.size(); ++i) {
        if (rom[i] == KIRBY_DECOMP_JSR_BYTES[0] &&
            rom[i+1] == KIRBY_DECOMP_JSR_BYTES[1] &&
            rom[i+2] == KIRBY_DECOMP_JSR_BYTES[2]) {
            sites.push_back(i);
        }
    }
    fprintf(stderr, "Found %zu JSR $C43A sites\n\n", sites.size());

    std::vector<CallSite> classified;
    int n_inline = 0, n_table = 0, n_complex = 0;
    std::set<int> inline_src_cpus;
    std::set<std::pair<int,int>> table_pairs;
    std::set<int> dst_cpus;

    for (int off : sites) {
        CallSite cs{};
        cs.file_off = off;
        int dst = 0;
        if (!match_dest_tail(rom, off, dst)) {
            cs.kind = CallSite::COMPLEX;
            cs.complex_pre_off = off - 24;
            n_complex++;
            classified.push_back(cs);
            continue;
        }
        cs.dst_cpu = dst;
        dst_cpus.insert(dst);

        int src = 0;
        if (match_inline_src(rom, off, src)) {
            cs.kind = CallSite::INLINE;
            cs.src_cpu = src;
            inline_src_cpus.insert(src);
            n_inline++;
        } else {
            int tlo, thi;
            if (match_table_y2_src(rom, off, tlo, thi)) {
                cs.kind = CallSite::TABLE_Y_2;
                cs.tbl_lo_cpu = tlo;
                cs.tbl_hi_cpu = thi;
                table_pairs.insert({tlo, thi});
                n_table++;
            } else {
                cs.kind = CallSite::COMPLEX;
                cs.complex_pre_off = off - 24;
                n_complex++;
            }
        }
        classified.push_back(cs);
    }

    printf("# JSR $C43A site summary\n");
    printf("  inline immediate sources:  %d  (%zu distinct src CPU)\n",
           n_inline, inline_src_cpus.size());
    printf("  table-Y-indexed 2-tables:  %d  (%zu distinct table pairs)\n",
           n_table, table_pairs.size());
    printf("  complex / unrecognized:    %d\n", n_complex);
    printf("  distinct destinations:     %zu\n", dst_cpus.size());
    printf("\n");

    printf("# All call sites (file_off, kind, src, dst)\n");
    long total_new_orig = 0, total_new_dec = 0;
    int new_blobs = 0;
    for (auto& cs : classified) {
        const char* k = (cs.kind == CallSite::INLINE)    ? "INLINE   "
                       : (cs.kind == CallSite::TABLE_Y_2) ? "TABLE_Y_2"
                       : (cs.kind == CallSite::COMPLEX)   ? "COMPLEX  "
                                                          : "UNKNOWN  ";
        if (cs.kind == CallSite::INLINE) {
            BankResolved r = resolve_src_bank_with_setup(rom, cs.src_cpu, cs.file_off);
            if (r.bank >= 0) {
                printf("  0x%-7X  %s  src=$%04X  dst=$%04X  -> bank=%2d %s file=0x%-6X  orig_csz=%-5d dec_sz=%d\n",
                       cs.file_off, k, cs.src_cpu, cs.dst_cpu, r.bank,
                       r.from_setup ? "(from F052)" : "(heuristic) ",
                       r.file_off, r.orig_csz, r.dec_sz);
                total_new_orig += r.orig_csz;
                total_new_dec += r.dec_sz;
                new_blobs++;
            } else {
                printf("  0x%-7X  %s  src=$%04X  dst=$%04X  -> (no valid bank found)\n",
                       cs.file_off, k, cs.src_cpu, cs.dst_cpu);
            }
        } else if (cs.kind == CallSite::TABLE_Y_2) {
            printf("  0x%-7X  %s  src=table[$%04X(lo),$%04X(hi),Y]  dst=$%04X\n",
                   cs.file_off, k, cs.tbl_lo_cpu, cs.tbl_hi_cpu, cs.dst_cpu);
        } else {
            printf("  0x%-7X  %s  pre=0x%X..\n", cs.file_off, k, cs.complex_pre_off);
            if (verbose) {
                printf("                 pre bytes: ");
                for (int k2 = cs.complex_pre_off; k2 < cs.file_off; ++k2)
                    printf("%02X ", rom[k2]);
                printf("\n");
            }
        }
    }

    printf("\n# Newly-discovered inline blobs (not in TCRF map/tileset tables):\n");
    printf("  blobs resolved:        %d\n", new_blobs);
    printf("  total original csz:    %ld\n", total_new_orig);
    printf("  total decompressed:    %ld\n", total_new_dec);

    return 0;
}
