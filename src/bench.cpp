// Bench: compare encode speed of compresch vs retrocompress across all documented blocks.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <vector>
#include "compresch_kirby.h"
#include "retrocompress.h"

using u8 = unsigned char;
struct Table { const char* name; int n; int off_bank, off_hi, off_lo; };

int main(int argc, char** argv) {
    const char* rom_path = argc > 1 ? argv[1] : "../reference/rom/kirby.nes";
    int iters = argc > 2 ? atoi(argv[2]) : 5;

    FILE* f = fopen(rom_path, "rb");
    fseek(f, 0, SEEK_END);
    long fsz = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<u8> rom(fsz); fread(rom.data(), 1, fsz, f); fclose(f);

    Table tables[] = {
        {"MAPS",     0x147, 0x244E1, 0x2476F, 0x248B6},
        {"TILESETS", 0x031, 0x249FD, 0x24A2E, 0x24A5F},
    };

    // Collect all decompressed blocks once
    std::vector<std::vector<u8>> blocks;
    for (auto& T : tables) {
        for (int i = 0; i < T.n; ++i) {
            u8 b = rom[T.off_bank + i], h = rom[T.off_hi + i], l = rom[T.off_lo + i];
            int addr = (h << 8) | l;
            if (b == 0 && addr == 0) continue;
            if (addr < 0xA000 || addr > 0xBFFF) continue;
            int off = 0x10 + (b & 0x7F) * 0x2000 + (addr - 0xA000);
            int dsz = Retrocompress::decompress(&rom[off], (int)(fsz - off), nullptr);
            if (dsz <= 0) continue;
            std::vector<u8> dec(dsz);
            Retrocompress::decompress(&rom[off], (int)(fsz - off), dec.data());
            blocks.push_back(std::move(dec));
        }
    }
    long total_in = 0;
    for (auto& b : blocks) total_in += b.size();
    printf("Loaded %zu blocks, total input %ld bytes\n\n", blocks.size(), total_in);

    using clk = std::chrono::steady_clock;

    // Warmup
    {
        std::vector<u8> dst(64*1024);
        for (auto& b : blocks) Compresch_Kirby::Compress(b.data(), (int)b.size(), dst.data());
        for (auto& b : blocks) Retrocompress::compress(b.data(), (int)b.size(), dst.data());
    }

    // Time compresch
    auto t0 = clk::now();
    long cs_out = 0;
    for (int it = 0; it < iters; ++it) {
        cs_out = 0;
        std::vector<u8> dst(64*1024);
        for (auto& b : blocks) cs_out += Compresch_Kirby::Compress(b.data(), (int)b.size(), dst.data());
    }
    auto t1 = clk::now();
    double cs_ms = std::chrono::duration<double, std::milli>(t1 - t0).count() / iters;

    // Time retrocompress
    auto t2 = clk::now();
    long opt_out = 0;
    for (int it = 0; it < iters; ++it) {
        opt_out = 0;
        std::vector<u8> dst(Retrocompress::worst_compress_size(64*1024));
        for (auto& b : blocks) opt_out += Retrocompress::compress(b.data(), (int)b.size(), dst.data());
    }
    auto t3 = clk::now();
    double opt_ms = std::chrono::duration<double, std::milli>(t3 - t2).count() / iters;

    printf("Iters: %d\n", iters);
    printf("compresch:  %.2f ms/run  (%.1f MB/s in, %ld bytes out)\n",
           cs_ms, total_in / (cs_ms * 1000.0), cs_out);
    printf("retrocompress:   %.2f ms/run  (%.1f MB/s in, %ld bytes out)\n",
           opt_ms, total_in / (opt_ms * 1000.0), opt_out);
    printf("\nSpeedup: compresch is %.1fx %s than retrocompress\n",
           opt_ms > cs_ms ? opt_ms / cs_ms : cs_ms / opt_ms,
           opt_ms > cs_ms ? "FASTER" : "SLOWER");
    return 0;
}
