// scanner.cpp — brute-force scanner for Kirby-format compressed blocks in an NES ROM.
//
// For each offset, runs a bounds-checked decompressor matching Parasyte's spec
// (kirbycmp.txt) and the github dekirby.c semantics: 0xFF terminator, type-7
// expanded -> LZ-copy. Accepts a block only if it passes strict filters and
// roundtrips through disch's compresch (decompress -> recompress -> decompress
// matches original decompressed bytes).
//
// Reports per-block: original compressed size, new compresch size, decompressed
// size, savings.

#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "compresch_kirby.h"

using u8 = unsigned char;

struct DecompResult {
    bool ok = false;
    int consumed = 0;       // includes 0xFF terminator
    int decompressed = 0;
    const char* err = nullptr;
};

static DecompResult safe_decompress(const u8* src, int srclen, std::vector<u8>& out, int max_out) {
    DecompResult r;
    int i = 0;
    out.clear();
    out.reserve(0x2000);

    while (i < srclen) {
        u8 ctrl = src[i++];
        if (ctrl == 0xFF) { r.ok = true; r.consumed = i; r.decompressed = (int)out.size(); return r; }

        int cmd = ctrl >> 5;
        int len;
        if (cmd == 7) {
            if (i >= srclen) { r.err = "eof in ext hdr"; return r; }
            cmd = (ctrl >> 2) & 7;
            len = (((ctrl & 3) << 8) | src[i++]) + 1;
        } else {
            len = (ctrl & 0x1F) + 1;
        }
        if (cmd == 7) cmd = 4; // expanded-7 -> LZ-copy

        // sanity: never emit more than max_out
        int produce = (cmd == 2) ? len * 2 : len;
        if ((int)out.size() + produce > max_out) { r.err = "out too large"; return r; }

        switch (cmd) {
            case 0:
                if (i + len > srclen) { r.err = "eof raw"; return r; }
                out.insert(out.end(), src + i, src + i + len);
                i += len;
                break;
            case 1: {
                if (i >= srclen) { r.err = "eof rle"; return r; }
                u8 b = src[i++];
                out.insert(out.end(), len, b);
                break;
            }
            case 2: {
                if (i + 1 >= srclen) { r.err = "eof 2rle"; return r; }
                u8 a = src[i++], b = src[i++];
                for (int k = 0; k < len; k++) { out.push_back(a); out.push_back(b); }
                break;
            }
            case 3: {
                if (i >= srclen) { r.err = "eof rle++"; return r; }
                u8 b = src[i++];
                for (int k = 0; k < len; k++) out.push_back(b++);
                break;
            }
            case 4: case 5: case 6: {
                if (i + 1 >= srclen) { r.err = "eof lz hdr"; return r; }
                int addr = (src[i] << 8) | src[i+1];
                i += 2;
                if (cmd == 4) {
                    if (addr + len > (int)out.size()) { r.err = "lz oob"; return r; }
                    for (int k = 0; k < len; k++) out.push_back(out[addr++]);
                } else if (cmd == 5) {
                    if (addr + len > (int)out.size()) { r.err = "lzbitrev oob"; return r; }
                    for (int k = 0; k < len; k++) {
                        u8 b = out[addr++];
                        u8 rv = 0;
                        for (int bit = 0; bit < 8; bit++) { rv = (rv << 1) | (b & 1); b >>= 1; }
                        out.push_back(rv);
                    }
                } else { // 6
                    if (addr - (len - 1) < 0) { r.err = "lzrev oob low"; return r; }
                    if (addr >= (int)out.size()) { r.err = "lzrev oob high"; return r; }
                    for (int k = 0; k < len; k++) out.push_back(out[addr--]);
                }
                break;
            }
        }
    }
    r.err = "no terminator";
    return r;
}

struct Filters {
    int min_consumed = 16;
    int min_decompressed = 64;
    int max_decompressed = 0x4000; // 16 KiB
    bool require_savings = true;   // require consumed < decompressed
};

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr,
            "usage: %s rom.nes [--min-c N] [--min-d N] [--max-d N] [--allow-bloat]\n"
            "  --min-c N        minimum compressed block size to consider (default 16)\n"
            "  --min-d N        minimum decompressed size (default 64)\n"
            "  --max-d N        maximum decompressed size (default 16384)\n"
            "  --allow-bloat    accept blocks where compressed >= decompressed\n", argv[0]);
        return 1;
    }
    const char* rom_path = argv[1];
    Filters F;
    long arg_scan_start = -1, arg_scan_end = -1;
    for (int a = 2; a < argc; a++) {
        if (!strcmp(argv[a], "--min-c") && a+1 < argc) F.min_consumed = atoi(argv[++a]);
        else if (!strcmp(argv[a], "--min-d") && a+1 < argc) F.min_decompressed = atoi(argv[++a]);
        else if (!strcmp(argv[a], "--max-d") && a+1 < argc) F.max_decompressed = atoi(argv[++a]);
        else if (!strcmp(argv[a], "--allow-bloat")) F.require_savings = false;
        else if (!strcmp(argv[a], "--start") && a+1 < argc) arg_scan_start = strtol(argv[++a], 0, 0);
        else if (!strcmp(argv[a], "--end") && a+1 < argc) arg_scan_end = strtol(argv[++a], 0, 0);
        else { fprintf(stderr, "unknown arg: %s\n", argv[a]); return 1; }
    }

    FILE* f = fopen(rom_path, "rb");
    if (!f) { perror("fopen"); return 1; }
    fseek(f, 0, SEEK_END);
    long fsz = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<u8> rom(fsz);
    if ((long)fread(rom.data(), 1, fsz, f) != fsz) { fprintf(stderr, "short read\n"); return 1; }
    fclose(f);

    int rom_start = 16; // iNES header (file offset where ROM data begins)
    long rom_end = fsz;
    if (arg_scan_start >= 0) rom_start = 16 + arg_scan_start;
    if (arg_scan_end   >= 0) rom_end   = 16 + arg_scan_end;

    fprintf(stderr, "ROM: %s\n", rom_path);
    fprintf(stderr, "Size (post-header): %ld bytes (0x%lX)\n", fsz - rom_start, fsz - rom_start);
    fprintf(stderr, "Filters: min_c=%d min_d=%d max_d=%d require_savings=%d\n\n",
        F.min_consumed, F.min_decompressed, F.max_decompressed, F.require_savings);

    printf("# rom_off       file_off    orig_csz    new_csz     dec_sz      delta       ratio\n");

    std::vector<u8> out;
    long off = rom_start;
    int found = 0, roundtrip_fail = 0;
    long total_orig = 0, total_new = 0;
    long total_dec = 0;

    while (off < rom_end - 4) {
        auto r = safe_decompress(rom.data() + off, (int)(rom_end - off), out, F.max_decompressed + 1);
        if (!r.ok || r.consumed < F.min_consumed
                  || r.decompressed < F.min_decompressed
                  || r.decompressed > F.max_decompressed
                  || (F.require_savings && r.consumed >= r.decompressed)) {
            off++;
            continue;
        }

        // recompress
        int worst = Compresch_Kirby::WorstCompressSize(r.decompressed);
        std::vector<u8> recomp(worst + 16);
        int new_csz = Compresch_Kirby::Compress(out.data(), r.decompressed, recomp.data());

        // roundtrip verify
        std::vector<u8> rt(r.decompressed + 256);
        int rt_size = Compresch_Kirby::Decompress(recomp.data(), new_csz, rt.data());
        bool rt_ok = (rt_size == r.decompressed) && (memcmp(rt.data(), out.data(), r.decompressed) == 0);

        long delta = (long)r.consumed - (long)new_csz;
        double ratio = (double)new_csz / r.consumed;
        printf("0x%-12lX 0x%-9lX  %-10d %-10d %-10d %+-10ld %.3f%s\n",
               off - rom_start, off, r.consumed, new_csz, r.decompressed, delta, ratio,
               rt_ok ? "" : "  RT_FAIL");

        found++;
        if (!rt_ok) roundtrip_fail++;
        if (rt_ok) {
            total_orig += r.consumed;
            total_new += new_csz;
            total_dec += r.decompressed;
        }
        off += r.consumed; // skip ahead — don't double-count overlapping candidates
    }

    fprintf(stderr, "\n=== Summary ===\n");
    fprintf(stderr, "Blocks found: %d  (roundtrip failures: %d)\n", found, roundtrip_fail);
    fprintf(stderr, "Total decompressed:        %10ld bytes\n", total_dec);
    fprintf(stderr, "Total original compressed: %10ld bytes  (ratio %.3f)\n",
            total_orig, total_dec ? (double)total_orig / total_dec : 0.0);
    fprintf(stderr, "Total compresch:           %10ld bytes  (ratio %.3f)\n",
            total_new, total_dec ? (double)total_new / total_dec : 0.0);
    if (total_orig)
        fprintf(stderr, "Savings vs original:       %+10ld bytes  (%.2f%%)\n",
                total_orig - total_new, 100.0 * (total_orig - total_new) / total_orig);
    return 0;
}
