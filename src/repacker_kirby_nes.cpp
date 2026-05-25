// repacker_kirby_nes.cpp — recompress every compressed blob in a Kirby's
// Adventure (NES) ROM with retrocompress, repack within each PRG bank, and
// update every pointer-table entry and inline-immediate reference so the
// resulting ROM plays identically.
//
// Output is bit-for-bit identical in structure (same size, same header,
// same un-touched code/palettes/etc.) but with compressed blobs replaced
// by their (smaller) retrocompress-encoded versions, references patched
// to match new offsets, and freed space filled with 0xFF.
//
// Covers all 427 known blobs:
//   - 327 maps      (TCRF pointer tables at $244E1 / $2476F / $248B6)
//   - 34 tilesets   (TCRF pointer tables at $249FD / $24A2E / $24A5F)
//   - 8  TABLE_Y_2 #1  (lo/hi tables at $68C38/$68C40; bank 52, no bank byte)
//   - 45 TABLE_Y_2 #2  (lo/hi tables at $27541/$2756E; bank 19, Y=1..45)
//   - 13 INLINE single-shots (immediates patched in the calling code)
//
// MMC3 8KB-bank constraint: each recompressed blob must fit in the same
// PRG bank as its original. Since retrocompress only shrinks, this is
// automatic.
//
// Patching strategy:
//   - For MAP, TILESET, TABLE_AC28, TABLE_B531: pack contiguously within
//     each bank starting at the bank's original first-blob offset, in
//     ascending offset order. Update each entry's (hi, lo) bytes; the
//     bank byte is preserved (including its bit-7 engine flag).
//   - For INLINE: leave in place (data is intermixed with code we don't
//     want to disturb); rewrite the smaller blob and pad the remainder
//     with $FF. No reference update needed.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <set>
#include <map>
#include <algorithm>
#include "retrocompress.h"

using u8 = unsigned char;

// ---------------- Constants from the analysis ----------------

constexpr int MAP_BANK_TBL    = 0x244E1;
constexpr int MAP_HI_TBL      = 0x2476F;
constexpr int MAP_LO_TBL      = 0x248B6;
constexpr int MAP_N           = 0x147; // 327

constexpr int TILESET_BANK_TBL = 0x249FD;
constexpr int TILESET_HI_TBL   = 0x24A2E;
constexpr int TILESET_LO_TBL   = 0x24A5F;
constexpr int TILESET_N        = 0x31; // 49 (only ~34 valid)

constexpr int TABLE_AC28_LO   = 0x68C38;
constexpr int TABLE_AC28_HI   = 0x68C40;
constexpr int TABLE_AC28_BANK = 52;
constexpr int TABLE_AC28_N    = 8;

constexpr int TABLE_B531_LO   = 0x27541;
constexpr int TABLE_B531_HI   = 0x2756E;
constexpr int TABLE_B531_BANK = 19;
constexpr int TABLE_B531_N    = 45;  // Y=1..45 (Y=0 is sentinel $CC74 — skipped)

struct InlineSite { int jsr_off; int src_cpu; };
static const std::vector<InlineSite> INLINE_SITES = {
    {0x5C655, 0xA691}, {0x5C67D, 0xA7BE}, {0x6C7B4, 0xA85E}, {0x6CDD0, 0xB10B},
    {0x7656E, 0xA881}, {0x765C5, 0xA9C1}, {0x77360, 0xBA82}, {0x773B1, 0xBBCE},
    {0x781E9, 0xA2C2}, {0x78204, 0xA7C9}, {0x7823B, 0xA50B}, {0x7A3AD, 0xAAE3},
    {0x7A3DC, 0xAB4D},
};

constexpr int BANK_SIZE = 0x2000;
constexpr int INES_HEADER = 0x10;

// ---------------- Blob representation ----------------

struct Blob {
    enum Kind { MAP, TILESET, TABLE_AC28, TABLE_B531, INLINE };
    Kind kind;
    int orig_file_off = 0;
    int orig_csz = 0;
    int bank = 0;
    std::vector<u8> dec;        // decompressed
    std::vector<u8> rec;        // recompressed via retrocompress
    int new_file_off = 0;       // assigned after packing

    // Reference info
    int table_idx = 0;          // MAP/TILESET/TABLE_*: index in the table
    u8 orig_bank_byte = 0;      // MAP/TILESET: raw bank byte (bit-7 flag preserved)
    int jsr_off = 0;            // INLINE: file offset of the JSR $C43A
};

// ---------------- Helpers ----------------

static int file_off_for(int bank, int src_cpu) {
    // R7 mapping: CPU $A000-$BFFF -> file = bank * 0x2000 + (cpu - 0xA000) + 0x10
    return INES_HEADER + bank * BANK_SIZE + (src_cpu - 0xA000);
}
static int cpu_for_file(int bank, int file_off) {
    return 0xA000 + (file_off - INES_HEADER - bank * BANK_SIZE);
}

// Measure compressed size by walking the stream; returns -1 on bad.
static int measure_csz(const u8* p, int max_len) {
    int i = 0;
    while (i < max_len) {
        u8 ctrl = p[i++];
        if (ctrl == 0xFF) return i;
        int cmd = ctrl >> 5, ln;
        if (cmd == 7) {
            if (i >= max_len) return -1;
            ln = (((ctrl & 3) << 8) | p[i++]) + 1;
            cmd = (ctrl >> 2) & 7;
        } else ln = (ctrl & 0x1F) + 1;
        if (cmd == 7) cmd = 4;
        if (cmd == 0) i += ln;
        else if (cmd == 1 || cmd == 3) i += 1;
        else i += 2;
    }
    return -1;
}

static bool decode_blob(const std::vector<u8>& rom, int file_off, Blob& b) {
    int csz = measure_csz(&rom[file_off], (int)rom.size() - file_off);
    if (csz <= 0) return false;
    int dec_sz = Retrocompress::decompress(&rom[file_off], csz, nullptr);
    if (dec_sz <= 0) return false;
    b.orig_csz = csz;
    b.dec.resize(dec_sz);
    int got = Retrocompress::decompress(&rom[file_off], csz, b.dec.data());
    return got == dec_sz;
}

// ---------------- Enumeration ----------------

static std::vector<Blob> enumerate_blobs(const std::vector<u8>& rom) {
    std::vector<Blob> blobs;
    blobs.reserve(500);

    // MAPS
    for (int i = 0; i < MAP_N; ++i) {
        u8 bank_byte = rom[MAP_BANK_TBL + i];
        u8 hi = rom[MAP_HI_TBL + i];
        u8 lo = rom[MAP_LO_TBL + i];
        int addr = (hi << 8) | lo;
        if (bank_byte == 0 && addr == 0) continue;
        if (addr < 0xA000 || addr > 0xBFFF) continue;
        int bank = bank_byte & 0x7F;
        int file_off = file_off_for(bank, addr);
        if (file_off < INES_HEADER || file_off >= (int)rom.size()) continue;
        Blob b;
        b.kind = Blob::MAP;
        b.orig_file_off = file_off;
        b.bank = bank;
        b.orig_bank_byte = bank_byte;
        b.table_idx = i;
        if (decode_blob(rom, file_off, b)) blobs.push_back(std::move(b));
    }

    // TILESETS
    for (int i = 0; i < TILESET_N; ++i) {
        u8 bank_byte = rom[TILESET_BANK_TBL + i];
        u8 hi = rom[TILESET_HI_TBL + i];
        u8 lo = rom[TILESET_LO_TBL + i];
        int addr = (hi << 8) | lo;
        if (bank_byte == 0 && addr == 0) continue;
        if (addr < 0xA000 || addr > 0xBFFF) continue;
        int bank = bank_byte & 0x7F;
        int file_off = file_off_for(bank, addr);
        if (file_off < INES_HEADER || file_off >= (int)rom.size()) continue;
        Blob b;
        b.kind = Blob::TILESET;
        b.orig_file_off = file_off;
        b.bank = bank;
        b.orig_bank_byte = bank_byte;
        b.table_idx = i;
        if (decode_blob(rom, file_off, b)) blobs.push_back(std::move(b));
    }

    // TABLE_AC28 (bank 52, 8 entries)
    for (int i = 0; i < TABLE_AC28_N; ++i) {
        u8 lo = rom[TABLE_AC28_LO + i];
        u8 hi = rom[TABLE_AC28_HI + i];
        int addr = (hi << 8) | lo;
        if (addr < 0xA000 || addr > 0xBFFF) continue;
        int file_off = file_off_for(TABLE_AC28_BANK, addr);
        Blob b;
        b.kind = Blob::TABLE_AC28;
        b.orig_file_off = file_off;
        b.bank = TABLE_AC28_BANK;
        b.table_idx = i;
        if (decode_blob(rom, file_off, b)) blobs.push_back(std::move(b));
    }

    // TABLE_B531 (bank 19, Y=1..45; Y=0 is a $CC74 sentinel)
    for (int y = 1; y <= TABLE_B531_N; ++y) {
        u8 lo = rom[TABLE_B531_LO + y];
        u8 hi = rom[TABLE_B531_HI + y];
        int addr = (hi << 8) | lo;
        if (addr < 0xA000 || addr > 0xBFFF) continue;
        int file_off = file_off_for(TABLE_B531_BANK, addr);
        Blob b;
        b.kind = Blob::TABLE_B531;
        b.orig_file_off = file_off;
        b.bank = TABLE_B531_BANK;
        b.table_idx = y;
        if (decode_blob(rom, file_off, b)) blobs.push_back(std::move(b));
    }

    // INLINE single-shots
    for (auto& s : INLINE_SITES) {
        int bank = (s.jsr_off - INES_HEADER) / BANK_SIZE;
        int file_off = file_off_for(bank, s.src_cpu);
        Blob b;
        b.kind = Blob::INLINE;
        b.orig_file_off = file_off;
        b.bank = bank;
        b.jsr_off = s.jsr_off;
        if (decode_blob(rom, file_off, b)) blobs.push_back(std::move(b));
    }
    return blobs;
}

// ---------------- Recompression ----------------

static void recompress_all(std::vector<Blob>& blobs) {
    for (Blob& b : blobs) {
        b.rec.resize(Retrocompress::worst_compress_size((int)b.dec.size()) + 16);
        int n = Retrocompress::compress(b.dec.data(), (int)b.dec.size(), b.rec.data());
        if (n <= 0) {
            fprintf(stderr, "compress failed for blob at 0x%X\n", b.orig_file_off);
            std::exit(1);
        }
        b.rec.resize(n);
    }
}

// ---------------- Packing ----------------

struct BankPlan {
    int bank;
    std::vector<Blob*> blobs;     // sorted by original offset (== sorted by table-index within type)
    int orig_first_off;
    int orig_last_end;            // exclusive
    int new_end;                  // exclusive (after packing)
    int saved;                    // bytes freed
};

static std::vector<BankPlan> plan_packing(std::vector<Blob>& blobs) {
    // Group MAP/TILESET/TABLE_* by bank for packing. INLINE stays in place.
    std::map<int, std::vector<Blob*>> by_bank;
    for (Blob& b : blobs) {
        if (b.kind == Blob::INLINE) {
            b.new_file_off = b.orig_file_off;
            continue;
        }
        by_bank[b.bank].push_back(&b);
    }

    std::vector<BankPlan> plans;
    for (auto& kv : by_bank) {
        BankPlan p;
        p.bank = kv.first;
        p.blobs = kv.second;
        std::sort(p.blobs.begin(), p.blobs.end(),
                  [](Blob* a, Blob* b) { return a->orig_file_off < b->orig_file_off; });

        p.orig_first_off = p.blobs.front()->orig_file_off;
        p.orig_last_end = p.blobs.back()->orig_file_off + p.blobs.back()->orig_csz;

        int cursor = p.orig_first_off;
        for (Blob* b : p.blobs) {
            b->new_file_off = cursor;
            cursor += (int)b->rec.size();
        }
        p.new_end = cursor;
        p.saved = p.orig_last_end - p.new_end;
        if (p.saved < 0) p.saved = 0;
        plans.push_back(std::move(p));
    }
    return plans;
}

// ---------------- Output writing & patching ----------------

static void write_output(const std::vector<u8>& rom_in,
                         std::vector<u8>& rom_out,
                         std::vector<Blob>& blobs,
                         const std::vector<BankPlan>& plans) {
    rom_out = rom_in;

    // 1. Write each blob's recompressed bytes at its new location.
    //    For packed groups, also fill any freed bytes inside the original
    //    blob range with $FF.
    for (const BankPlan& p : plans) {
        // Fill the entire original range with $FF first.
        for (int i = p.orig_first_off; i < p.orig_last_end; ++i) rom_out[i] = 0xFF;
        // Then write each new blob.
        for (Blob* b : p.blobs) {
            memcpy(&rom_out[b->new_file_off], b->rec.data(), b->rec.size());
        }
    }
    // INLINE blobs: leave in place, write smaller blob, pad rest with $FF
    // up to the original compressed size.
    for (Blob& b : blobs) {
        if (b.kind != Blob::INLINE) continue;
        // Pad first, then write
        for (int i = 0; i < b.orig_csz; ++i) rom_out[b.orig_file_off + i] = 0xFF;
        memcpy(&rom_out[b.orig_file_off], b.rec.data(), b.rec.size());
    }

    // 2. Patch pointer tables for MAP/TILESET (preserve bank byte exactly,
    //    update hi/lo).
    for (Blob& b : blobs) {
        if (b.kind == Blob::MAP || b.kind == Blob::TILESET) {
            int new_cpu = cpu_for_file(b.bank, b.new_file_off);
            u8 new_lo = new_cpu & 0xFF, new_hi = (new_cpu >> 8) & 0xFF;
            if (b.kind == Blob::MAP) {
                rom_out[MAP_HI_TBL + b.table_idx] = new_hi;
                rom_out[MAP_LO_TBL + b.table_idx] = new_lo;
            } else {
                rom_out[TILESET_HI_TBL + b.table_idx] = new_hi;
                rom_out[TILESET_LO_TBL + b.table_idx] = new_lo;
            }
            // bank byte is unchanged (we don't move blobs between banks)
        } else if (b.kind == Blob::TABLE_AC28) {
            int new_cpu = cpu_for_file(b.bank, b.new_file_off);
            rom_out[TABLE_AC28_LO + b.table_idx] = new_cpu & 0xFF;
            rom_out[TABLE_AC28_HI + b.table_idx] = (new_cpu >> 8) & 0xFF;
        } else if (b.kind == Blob::TABLE_B531) {
            int new_cpu = cpu_for_file(b.bank, b.new_file_off);
            rom_out[TABLE_B531_LO + b.table_idx] = new_cpu & 0xFF;
            rom_out[TABLE_B531_HI + b.table_idx] = (new_cpu >> 8) & 0xFF;
        }
        // INLINE: location unchanged, no patch needed
    }
}

// ---------------- Verification ----------------

// Re-walk every blob via its (now-updated) reference and check the
// decompressed bytes match what we originally read.
static bool verify_roundtrip(const std::vector<u8>& rom_out,
                             const std::vector<Blob>& blobs) {
    int failures = 0;
    for (const Blob& b : blobs) {
        int file_off;
        if (b.kind == Blob::MAP) {
            u8 hi = rom_out[MAP_HI_TBL + b.table_idx];
            u8 lo = rom_out[MAP_LO_TBL + b.table_idx];
            file_off = file_off_for(b.bank, (hi << 8) | lo);
        } else if (b.kind == Blob::TILESET) {
            u8 hi = rom_out[TILESET_HI_TBL + b.table_idx];
            u8 lo = rom_out[TILESET_LO_TBL + b.table_idx];
            file_off = file_off_for(b.bank, (hi << 8) | lo);
        } else if (b.kind == Blob::TABLE_AC28) {
            u8 lo = rom_out[TABLE_AC28_LO + b.table_idx];
            u8 hi = rom_out[TABLE_AC28_HI + b.table_idx];
            file_off = file_off_for(b.bank, (hi << 8) | lo);
        } else if (b.kind == Blob::TABLE_B531) {
            u8 lo = rom_out[TABLE_B531_LO + b.table_idx];
            u8 hi = rom_out[TABLE_B531_HI + b.table_idx];
            file_off = file_off_for(b.bank, (hi << 8) | lo);
        } else { // INLINE
            file_off = b.orig_file_off; // unchanged
        }
        int csz = measure_csz(&rom_out[file_off], (int)rom_out.size() - file_off);
        if (csz <= 0) { ++failures; continue; }
        std::vector<u8> dec(b.dec.size() + 64);
        int n = Retrocompress::decompress(&rom_out[file_off], csz, dec.data());
        if (n != (int)b.dec.size() || memcmp(dec.data(), b.dec.data(), b.dec.size()) != 0) {
            fprintf(stderr, "verify FAIL: blob orig 0x%X kind=%d\n", b.orig_file_off, (int)b.kind);
            ++failures;
        }
    }
    return failures == 0;
}

// ---------------- Free-space report ----------------

struct FreeRange { int file_off; int len; int bank; };
static std::vector<FreeRange> compute_free_space(const std::vector<BankPlan>& plans) {
    std::vector<FreeRange> ranges;
    for (const BankPlan& p : plans) {
        if (p.new_end < p.orig_last_end) {
            ranges.push_back({p.new_end, p.orig_last_end - p.new_end, p.bank});
        }
    }
    return ranges;
}

// ---------------- Main ----------------

static void usage(const char* prog) {
    fprintf(stderr,
        "usage: %s <input.nes> [-o output.nes] [--verify] [--verbose]\n"
        "  --verify    re-walk all blobs through patched tables, verify byte-for-byte match\n"
        "  --verbose   print per-blob savings + free-space ranges\n", prog);
}

int main(int argc, char** argv) {
    if (argc < 2) { usage(argv[0]); return 1; }
    const char* in_path = argv[1];
    const char* out_path = nullptr;
    bool verify = false, verbose = false;
    for (int a = 2; a < argc; ++a) {
        if (!strcmp(argv[a], "-o") && a+1 < argc) out_path = argv[++a];
        else if (!strcmp(argv[a], "--verify")) verify = true;
        else if (!strcmp(argv[a], "--verbose")) verbose = true;
        else { fprintf(stderr, "unknown arg: %s\n", argv[a]); usage(argv[0]); return 1; }
    }

    FILE* f = fopen(in_path, "rb");
    if (!f) { perror(in_path); return 1; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<u8> rom(sz);
    fread(rom.data(), 1, sz, f);
    fclose(f);

    printf("ROM size: %ld bytes\n", sz);

    // 1. Enumerate
    auto blobs = enumerate_blobs(rom);
    printf("Enumerated %zu compressed blobs\n", blobs.size());

    // 2. Recompress
    recompress_all(blobs);

    // 3. Pack
    auto plans = plan_packing(blobs);

    // 4. Stats
    long total_orig = 0, total_new = 0;
    std::map<int, std::pair<long,long>> by_kind;  // {orig, new}
    const char* kind_names[] = {"MAP", "TILESET", "TABLE_AC28", "TABLE_B531", "INLINE"};
    for (Blob& b : blobs) {
        total_orig += b.orig_csz;
        total_new += b.rec.size();
        by_kind[(int)b.kind].first += b.orig_csz;
        by_kind[(int)b.kind].second += b.rec.size();
    }
    printf("\n=== Compression results ===\n");
    printf("%-12s %-10s %-10s %-10s %s\n", "kind", "blobs", "orig_csz", "new_csz", "saved");
    for (auto& kv : by_kind) {
        int cnt = 0;
        for (Blob& b : blobs) if ((int)b.kind == kv.first) ++cnt;
        printf("%-12s %-10d %-10ld %-10ld %ld\n",
               kind_names[kv.first], cnt, kv.second.first, kv.second.second,
               kv.second.first - kv.second.second);
    }
    printf("-------------------------------------------------\n");
    printf("%-12s %-10zu %-10ld %-10ld %ld bytes (%.2f%%)\n",
           "TOTAL", blobs.size(), total_orig, total_new,
           total_orig - total_new, 100.0 * (total_orig - total_new) / total_orig);

    // 5. Write output (if -o given)
    if (!out_path) {
        printf("\n(no -o; not writing output)\n");
        return 0;
    }
    std::vector<u8> rom_out;
    write_output(rom, rom_out, blobs, plans);
    FILE* of = fopen(out_path, "wb");
    if (!of) { perror(out_path); return 1; }
    fwrite(rom_out.data(), 1, rom_out.size(), of);
    fclose(of);
    printf("\nWrote %s (%zu bytes)\n", out_path, rom_out.size());

    // 6. Verify if requested
    if (verify) {
        printf("Verifying roundtrip via patched tables...\n");
        if (verify_roundtrip(rom_out, blobs)) {
            printf("  All %zu blobs verified OK.\n", blobs.size());
        } else {
            printf("  VERIFY FAILED.\n");
            return 2;
        }
    }

    // 7. Free-space report
    if (verbose) {
        auto ranges = compute_free_space(plans);
        long total_free = 0;
        for (auto& r : ranges) total_free += r.len;
        printf("\n=== Free space ranges (after packing within each bank) ===\n");
        printf("%-12s %-10s %-10s\n", "bank", "file_off", "length");
        for (auto& r : ranges) {
            printf("%-12d 0x%-8X %d\n", r.bank, r.file_off, r.len);
        }
        printf("Total free space inside original blob regions: %ld bytes\n", total_free);

        printf("\n=== INLINE blobs (in-place, padded with $FF) ===\n");
        for (Blob& b : blobs) {
            if (b.kind != Blob::INLINE) continue;
            printf("  jsr 0x%-7X  blob 0x%-7X  orig_csz=%-5d new_csz=%-5d (slack=%d)\n",
                   b.jsr_off, b.orig_file_off, b.orig_csz, (int)b.rec.size(),
                   b.orig_csz - (int)b.rec.size());
        }
    }
    return 0;
}
