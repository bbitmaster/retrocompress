// pointer_walker_opt.cpp - decompress every documented compressed block in
// Kirby's Adventure, then compress with BOTH our optimal DP encoder AND
// disch's compresch, and compare against the original ROM-stored size.

#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "compresch_kirby.h"
#include "retrocompress.h"

using u8 = unsigned char;

struct Table { const char* name; int n; int off_bank, off_hi, off_lo; };

int main(int argc, char** argv) {
    const char* rom_path = argc > 1 ? argv[1] : "../reference/rom/kirby.nes";
    bool verbose = (argc > 2 && !strcmp(argv[2], "-v"));

    FILE* f = fopen(rom_path, "rb");
    if (!f) { perror("fopen"); return 1; }
    fseek(f, 0, SEEK_END);
    long fsz = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<u8> rom(fsz);
    fread(rom.data(), 1, fsz, f);
    fclose(f);

    Table tables[] = {
        {"MAPS",     0x147, 0x244E1, 0x2476F, 0x248B6},
        {"TILESETS", 0x031, 0x249FD, 0x24A2E, 0x24A5F},
    };

    long g_orig = 0, g_cs = 0, g_opt = 0, g_dec = 0;
    int g_valid = 0, g_skip = 0, g_rtfail = 0, g_opt_worse = 0, g_opt_beats_cs = 0, g_cs_beats_opt = 0, g_tie = 0;

    for (auto& T : tables) {
        long t_orig = 0, t_cs = 0, t_opt = 0, t_dec = 0;
        int t_valid = 0, t_skip = 0, t_rtfail = 0;
        int t_opt_worse = 0, t_opt_beats_cs = 0, t_cs_beats_opt = 0, t_tie = 0;
        printf("\n========== %s ==========\n", T.name);
        if (verbose)
            printf("%-4s %-9s %-8s %-8s %-8s %-9s %-9s %-9s\n",
                   "idx", "file_off", "orig", "compresch", "retrocompress", "decomp_sz", "opt_vs_orig", "opt_vs_cs");

        for (int i = 0; i < T.n; ++i) {
            u8 b = rom[T.off_bank + i], h = rom[T.off_hi + i], l = rom[T.off_lo + i];
            int addr = (h << 8) | l;
            if (b == 0 && addr == 0) { t_skip++; continue; }
            if (addr < 0xA000 || addr > 0xBFFF) { t_skip++; continue; }
            int file_off = 0x10 + (b & 0x7F) * 0x2000 + (addr - 0xA000);

            // Decode original block to know its bounds and content
            int dsz = Retrocompress::decompress(&rom[file_off], (int)(fsz - file_off), nullptr);
            if (dsz <= 0) { t_skip++; continue; }
            std::vector<u8> dec(dsz);
            int dsz2 = Retrocompress::decompress(&rom[file_off], (int)(fsz - file_off), dec.data());
            if (dsz2 != dsz) { t_skip++; continue; }
            // We need the original compressed size — re-decode counting bytes consumed.
            // Simplest: walk the stream the same way.
            int orig_csz = 0;
            {
                int p = 0;
                while (file_off + p < fsz) {
                    u8 ctrl = rom[file_off + p++];
                    if (ctrl == 0xFF) { orig_csz = p; break; }
                    int cmd = ctrl >> 5, ln;
                    if (cmd == 7) { ln = (((ctrl & 3) << 8) | rom[file_off + p++]) + 1; cmd = (ctrl >> 2) & 7; }
                    else ln = (ctrl & 0x1F) + 1;
                    if (cmd == 7) cmd = 4;
                    if (cmd == 0) p += ln;
                    else if (cmd == 1 || cmd == 3) p += 1;
                    else p += 2;
                }
            }

            // compresch encode
            int cs_worst = Compresch_Kirby::WorstCompressSize(dsz);
            std::vector<u8> cs(cs_worst + 16);
            int cs_csz = Compresch_Kirby::Compress(dec.data(), dsz, cs.data());

            // retrocompress encode
            std::vector<u8> opt(Retrocompress::worst_compress_size(dsz));
            int opt_csz = Retrocompress::compress(dec.data(), dsz, opt.data());

            // Verify retrocompress roundtrip (via our own decompressor and via compresch's)
            std::vector<u8> rt(dsz + 64);
            int rt1 = Retrocompress::decompress(opt.data(), opt_csz, rt.data());
            bool ok1 = (rt1 == dsz) && (memcmp(rt.data(), dec.data(), dsz) == 0);
            int rt2 = Compresch_Kirby::Decompress(opt.data(), opt_csz, rt.data());
            bool ok2 = (rt2 == dsz) && (memcmp(rt.data(), dec.data(), dsz) == 0);
            bool rt_ok = ok1 && ok2;
            if (!rt_ok) t_rtfail++;

            if (verbose)
                printf("%-4d 0x%-7X %-8d %-8d %-8d %-8d  %+5d     %+5d%s\n",
                       i, file_off, orig_csz, cs_csz, opt_csz, dsz,
                       orig_csz - opt_csz, cs_csz - opt_csz,
                       rt_ok ? "" : "  RT_FAIL");

            t_valid++;
            t_orig += orig_csz; t_cs += cs_csz; t_opt += opt_csz; t_dec += dsz;
            if (opt_csz > orig_csz) t_opt_worse++;
            if (opt_csz < cs_csz) t_opt_beats_cs++;
            else if (cs_csz < opt_csz) t_cs_beats_opt++;
            else t_tie++;
        }
        printf("Summary: valid=%d skip=%d rt_fail=%d\n", t_valid, t_skip, t_rtfail);
        printf("  decompressed:    %ld\n", t_dec);
        printf("  original csz:    %ld  (ratio %.3f)\n", t_orig, t_dec? (double)t_orig/t_dec:0.0);
        printf("  compresch:       %ld  (saved %ld vs original, %.2f%%)\n", t_cs, t_orig-t_cs, t_orig?100.0*(t_orig-t_cs)/t_orig:0.0);
        printf("  retrocompress:        %ld  (saved %ld vs original, %.2f%%)\n", t_opt, t_orig-t_opt, t_orig?100.0*(t_orig-t_opt)/t_orig:0.0);
        printf("  retrocompress vs cs:  %d beats / %d loses / %d ties, opt_worse_than_orig=%d\n",
               t_opt_beats_cs, t_cs_beats_opt, t_tie, t_opt_worse);
        g_orig+=t_orig; g_cs+=t_cs; g_opt+=t_opt; g_dec+=t_dec;
        g_valid+=t_valid; g_skip+=t_skip; g_rtfail+=t_rtfail;
        g_opt_worse+=t_opt_worse; g_opt_beats_cs+=t_opt_beats_cs; g_cs_beats_opt+=t_cs_beats_opt; g_tie+=t_tie;
    }

    printf("\n=============================================\n");
    printf("GRAND TOTAL (maps + tilesets, pointer-validated):\n");
    printf("  valid=%d skip=%d rt_fail=%d\n", g_valid, g_skip, g_rtfail);
    printf("  decompressed:   %ld bytes\n", g_dec);
    printf("  original csz:   %ld bytes (ratio %.3f)\n", g_orig, g_dec? (double)g_orig/g_dec:0.0);
    printf("  compresch:      %ld bytes (ratio %.3f, saved %ld = %.2f%%)\n",
           g_cs, g_dec? (double)g_cs/g_dec:0.0, g_orig-g_cs, g_orig?100.0*(g_orig-g_cs)/g_orig:0.0);
    printf("  retrocompress (DP):  %ld bytes (ratio %.3f, saved %ld = %.2f%%)\n",
           g_opt, g_dec? (double)g_opt/g_dec:0.0, g_orig-g_opt, g_orig?100.0*(g_orig-g_opt)/g_orig:0.0);
    printf("  retrocompress vs compresch: %d wins / %d losses / %d ties / opt_worse_than_orig=%d\n",
           g_opt_beats_cs, g_cs_beats_opt, g_tie, g_opt_worse);
    return 0;
}
