// diff_one.cpp - encode a single Kirby block with both compresch and optkirby,
// print each token decision side-by-side to find where compresch is leaving
// bytes on the table.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "compresch_kirby.h"
#include "retrocompress.h"

using u8 = unsigned char;

struct Tok { int type; int enc_len; int out_bytes; int addr; int byte_cost; int src_pos; int dst_pos; };

static const char* type_name(int t) {
    static const char* names[8] = {"RAW","RLE","RLE2","RLE++","LZ","LZBR","LZREV","ext7"};
    return (t >= 0 && t < 8) ? names[t] : "?";
}

// Re-tokenize a compressed stream into the (type, enc_len, ...) sequence.
static std::vector<Tok> tokenize(const u8* src, int srclen, int dec_size) {
    std::vector<Tok> toks;
    int i = 0, j = 0;
    while (i < srclen) {
        int hdr_start = i;
        u8 ctrl = src[i++];
        if (ctrl == 0xFF) break;
        int cmd = ctrl >> 5, len;
        if (cmd == 7) { cmd = (ctrl >> 2) & 7; len = (((ctrl & 3) << 8) | src[i++]) + 1; }
        else len = (ctrl & 0x1F) + 1;
        // Original cmd may be 7 in the expanded form; treat as 4
        int real_cmd = (cmd == 7) ? 4 : cmd;
        Tok t{real_cmd, len, 0, 0, 0, j, j};
        switch (real_cmd) {
            case 0: t.out_bytes = len; i += len; j += len; break;
            case 1: t.out_bytes = len; i += 1; j += len; break;
            case 2: t.out_bytes = 2*len; i += 2; j += 2*len; break;
            case 3: t.out_bytes = len; i += 1; j += len; break;
            case 4: case 5: case 6:
                t.out_bytes = len; t.addr = (src[i]<<8)|src[i+1]; i += 2; j += len; break;
        }
        t.byte_cost = i - hdr_start;
        toks.push_back(t);
    }
    (void)dec_size;
    return toks;
}

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s rom.nes <map|tile> <idx>\n", argv[0]);
        return 1;
    }
    const char* rom_path = argv[1];
    const char* kind = (argc >= 4) ? argv[2] : "map";
    int idx = atoi((argc >= 4) ? argv[3] : argv[2]);

    FILE* f = fopen(rom_path, "rb");
    fseek(f, 0, SEEK_END);
    long fsz = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<u8> rom(fsz); fread(rom.data(), 1, fsz, f); fclose(f);

    int off_bank, off_hi, off_lo;
    if (!strcmp(kind, "tile")) {
        off_bank = 0x249FD; off_hi = 0x24A2E; off_lo = 0x24A5F;
    } else {
        off_bank = 0x244E1; off_hi = 0x2476F; off_lo = 0x248B6;
    }
    u8 b = rom[off_bank + idx], h = rom[off_hi + idx], l = rom[off_lo + idx];
    int addr = (h << 8) | l;
    int file_off = 0x10 + (b & 0x7F) * 0x2000 + (addr - 0xA000);

    int dsz = Retrocompress::decompress(&rom[file_off], (int)(fsz - file_off), nullptr);
    if (dsz <= 0) { fprintf(stderr, "block %d: decode failed\n", idx); return 1; }
    std::vector<u8> dec(dsz);
    Retrocompress::decompress(&rom[file_off], (int)(fsz - file_off), dec.data());

    std::vector<u8> cs(Compresch_Kirby::WorstCompressSize(dsz) + 16);
    int cs_csz = Compresch_Kirby::Compress(dec.data(), dsz, cs.data());

    std::vector<u8> opt(Retrocompress::worst_compress_size(dsz));
    int opt_csz = Retrocompress::compress(dec.data(), dsz, opt.data());

    printf("Block %d (file 0x%X): decompressed=%d  compresch=%d  optkirby=%d  saved=%+d\n\n",
           idx, file_off, dsz, cs_csz, opt_csz, cs_csz - opt_csz);

    auto cs_toks = tokenize(cs.data(), cs_csz, dsz);
    auto opt_toks = tokenize(opt.data(), opt_csz, dsz);

    // Walk both token streams in lockstep at the dec-byte level
    printf("%-6s | %-26s | %-26s\n", "dst", "compresch", "optkirby");
    printf("-------+----------------------------+--------------------------\n");
    size_t ci = 0, oi = 0;
    int cs_pos = 0, opt_pos = 0;
    int cs_bytes = 0, opt_bytes = 0;
    while (ci < cs_toks.size() || oi < opt_toks.size()) {
        char cline[64] = "(end)";
        char oline[64] = "(end)";
        int common_pos = -1;
        // Decide which side advances. The side whose next token ends earliest (in dec-bytes)
        // advances; if both end at same point, both advance.
        int cs_end = (ci < cs_toks.size()) ? (cs_toks[ci].src_pos + cs_toks[ci].out_bytes) : INT32_MAX;
        int op_end = (oi < opt_toks.size()) ? (opt_toks[oi].src_pos + opt_toks[oi].out_bytes) : INT32_MAX;
        if (cs_end <= op_end) {
            if (ci < cs_toks.size()) {
                auto& t = cs_toks[ci];
                snprintf(cline, sizeof cline, "%-5s len=%-4d cost=%d%s",
                         type_name(t.type), t.enc_len, t.byte_cost,
                         (t.type >= 4 && t.type <= 6) ? "" : "");
                cs_bytes += t.byte_cost;
                cs_pos = cs_end;
                ++ci;
            }
        }
        if (op_end <= cs_end) {
            if (oi < opt_toks.size()) {
                auto& t = opt_toks[oi];
                snprintf(oline, sizeof oline, "%-5s len=%-4d cost=%d",
                         type_name(t.type), t.enc_len, t.byte_cost);
                opt_bytes += t.byte_cost;
                opt_pos = op_end;
                ++oi;
            }
        }
        common_pos = (cs_end <= op_end) ? cs_pos : opt_pos;
        printf("%-6d | %-26s | %-26s\n", common_pos, cline, oline);
    }
    printf("\nTotals (excl 0xFF terminator): compresch=%d  optkirby=%d\n", cs_bytes, opt_bytes);
    return 0;
}
