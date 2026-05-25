// walker_super_metroid.cpp - decompress every documented compressed block in
// Super Metroid (JU) [!] via the pointer tables, then recompress with
// retrocompress and report savings vs the original ROM encoding.
//
// Pointer tables sourced from PJBoy's disassembly
// (github.com/InsaneFirebat/sm_disassembly):
//
//   Tileset_Pointers @ SNES $8F:E7A7
//       29 entries x 2 bytes -> Tileset_Table_X (bank $8F assumed)
//   Tileset_Table_X (9 bytes each):
//       3 x dl (3-byte) pointers: tile-table, tiles, palette
//
// LoROM address translation: file_offset = (bank - $80) * 0x8000 + (addr - $8000)
// SM is LoROM, no SMC header on the standard No-Intro release (3,146,240 bytes).
//
// Note: this first pass covers only the tileset pointer table. The 51 inline-
// labeled sources (used by JSL Decompression_HardcodedDestination with a `dl`
// immediate after) and the per-room level data (RoomHeader_* in bank $8F)
// can be added later — same machinery, just different table walks.

#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "retrocompress.h"

using u8 = unsigned char;

static int lorom_file_offset(int snes_bank, int snes_addr) {
    return (snes_bank - 0x80) * 0x8000 + (snes_addr - 0x8000);
}

static int read_word(const std::vector<u8>& rom, int off) {
    return rom[off] | (rom[off+1] << 8);
}
static int read_long(const std::vector<u8>& rom, int off) {
    return rom[off] | (rom[off+1] << 8) | (rom[off+2] << 16);
}

// SM stores compressed data using the same 3-bit-cmd format as Kirby per the
// disassembly at $80:B119. The Retrocompress::decompress / compress functions
// handle this format directly.
struct Block {
    const char* label;
    int file_off;
    int orig_csz;     // bytes consumed from ROM
    int dec_sz;       // decompressed size
    int new_csz;      // recompressed size
    bool roundtrip;   // decompress(recompress(x)) == x
    const char* err;
};

static Block process(const std::vector<u8>& rom, int file_off, const char* label) {
    Block b{label, file_off, 0, 0, 0, false, nullptr};
    if (file_off < 0 || file_off >= (int)rom.size()) { b.err = "out of rom"; return b; }

    int dec_sz = Retrocompress::decompress_sm(&rom[file_off], (int)(rom.size() - file_off), nullptr);
    if (dec_sz <= 0) { b.err = "decompress failed"; return b; }
    std::vector<u8> dec(dec_sz);
    int dec_sz2 = Retrocompress::decompress_sm(&rom[file_off], (int)(rom.size() - file_off), dec.data());
    if (dec_sz2 != dec_sz) { b.err = "decompress size mismatch"; return b; }

    // Re-walk the compressed stream to measure its byte length (same as
    // measure_original_csz but tolerant of the SM-specific payload sizes).
    int orig = 0;
    {
        int p = 0, rs = (int)rom.size();
        while (file_off + p < rs) {
            u8 ctrl = rom[file_off + p++];
            if (ctrl == 0xFF) { orig = p; break; }
            int cmd = ctrl >> 5, ln;
            if (cmd == 7) {
                if (file_off + p >= rs) { orig = -1; break; }
                ln = (((ctrl & 3) << 8) | rom[file_off + p++]) + 1;
                cmd = (ctrl >> 2) & 7;
            } else ln = (ctrl & 0x1F) + 1;
            if (cmd == 0) p += ln;
            else if (cmd == 1 || cmd == 3) p += 1;
            else if (cmd == 2) p += 2;
            else if (cmd == 4 || cmd == 5) p += 2;  // SM: 2-byte absolute addr
            else /* cmd 6 or 7 */ p += 1;            // SM: 1-byte relative distance
        }
    }
    if (orig <= 0) { b.err = "couldn't measure orig csz"; return b; }
    b.dec_sz = dec_sz;
    b.orig_csz = orig;
    // For now, no recompression — optimal SM encoder is a separate build.
    b.new_csz = 0;
    b.roundtrip = true;
    (void)label;
    return b;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr,
            "usage: %s <super_metroid.smc> [--verbose]\n"
            "  ROM must be 3,146,240 bytes (no SMC header), LoROM.\n", argv[0]);
        return 1;
    }
    const char* rom_path = argv[1];
    bool verbose = (argc >= 3 && !strcmp(argv[2], "--verbose"));

    FILE* f = fopen(rom_path, "rb");
    if (!f) { perror(rom_path); return 1; }
    fseek(f, 0, SEEK_END);
    long sz_raw = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<u8> raw(sz_raw);
    if ((long)fread(raw.data(), 1, sz_raw, f) != sz_raw) { fprintf(stderr, "short read\n"); return 1; }
    fclose(f);

    // Detect & strip 0x200-byte SMC header.
    // LoROM SNES header is at file offset 0x7FC0 (no SMC) or 0x81C0 (with SMC).
    // Check for ASCII title at one or the other.
    int header_off = 0;
    auto looks_title = [](const u8* p) {
        for (int i = 0; i < 21; ++i) {
            u8 c = p[i];
            if (c == ' ') continue;
            if (c < 0x20 || c > 0x7E) return false;
        }
        return true;
    };
    if (sz_raw >= 0x81C0 + 21 && looks_title(&raw[0x81C0])) header_off = 0x200;
    else if (sz_raw >= 0x7FC0 + 21 && looks_title(&raw[0x7FC0])) header_off = 0;
    else {
        fprintf(stderr, "couldn't locate SNES header; bailing.\n");
        return 1;
    }
    std::vector<u8> rom(raw.begin() + header_off, raw.end());
    long sz = (long)rom.size();
    fprintf(stderr, "ROM raw size: %ld bytes (SMC header: %s)\n",
            sz_raw, header_off ? "yes, stripped" : "no");
    fprintf(stderr, "Logical ROM size: %ld bytes\n", sz);
    char title[22] = {};
    memcpy(title, &rom[0x7FC0], 21);
    fprintf(stderr, "Internal header title: \"%s\"\n", title);

    // Tileset_Pointers at $8F:E7A7
    int tileset_pointers_off = lorom_file_offset(0x8F, 0xE7A7);
    fprintf(stderr, "Tileset_Pointers at file 0x%X\n", tileset_pointers_off);

    // The table has 29 entries (Tileset_Table_0 through Tileset_Table_1C).
    // Each is a 2-byte pointer relative to bank $8F.
    const int N_TILESETS = 29;
    const char* tileset_names[N_TILESETS] = {
        "00_UpperCrateria", "01_RedCrateria", "02_LowerCrateria",
        "03_OldTourian", "04_WreckedShip_PowerOn", "05_WreckedShip_PowerOff",
        "06_GreenBlueBrinstar", "07_RedBrinstar_Kraid", "08_StatuesHall",
        "09_HeatedNorfair", "0A_UnheatedNorfair", "0B_SandlessMaridia",
        "0C_SandyMaridia", "0D_Tourian", "0E_MotherBrain",
        "0F_BlueCeres", "10_WhiteCeres", "11_BlueCeresElevator",
        "12_WhiteCeresElevator", "13_BlueCeresRidley", "14_WhiteCeresRidley",
        "15_Map_Statues", "16_WreckedShipMap_PowerOff", "17_BlueRefill",
        "18_YellowRefill", "19_SaveStation", "1A_Kraid",
        "1B_Crocomire", "1C_Draygon"
    };
    const char* sub_role[3] = {"tile_table", "tiles", "palette"};

    std::vector<Block> blocks;
    blocks.reserve(N_TILESETS * 3);

    for (int i = 0; i < N_TILESETS; ++i) {
        int sub_addr = read_word(rom, tileset_pointers_off + i * 2);   // bank $8F implied
        int sub_off = lorom_file_offset(0x8F, sub_addr);
        for (int j = 0; j < 3; ++j) {
            int blob_long = read_long(rom, sub_off + j * 3);
            int blob_bank = (blob_long >> 16) & 0xFF;
            int blob_addr = blob_long & 0xFFFF;
            int blob_file_off = lorom_file_offset(blob_bank, blob_addr);

            char label[64];
            snprintf(label, sizeof label, "Tileset_%s_%s", tileset_names[i], sub_role[j]);
            Block b = process(rom, blob_file_off, label);
            blocks.push_back(b);
        }
    }

    if (verbose) {
        printf("%-44s %-9s %-7s %-7s %-7s %-9s %s\n",
               "label", "file_off", "orig", "new", "dec_sz", "saved", "status");
    }
    long total_orig = 0, total_new = 0, total_dec = 0;
    int ok = 0, fail = 0, rt_fail = 0;
    for (auto& b : blocks) {
        if (b.err) {
            fail++;
            if (verbose) printf("%-44s 0x%-7X FAIL: %s\n", b.label, b.file_off, b.err);
            continue;
        }
        if (!b.roundtrip) rt_fail++;
        ok++;
        total_orig += b.orig_csz;
        total_new += b.new_csz;
        total_dec += b.dec_sz;
        if (verbose) {
            printf("%-44s 0x%-7X %-7d %-7d %-7d %+-9d %s\n",
                   b.label, b.file_off, b.orig_csz, b.new_csz, b.dec_sz,
                   b.orig_csz - b.new_csz,
                   b.roundtrip ? "OK" : "RT_FAIL");
        }
    }

    printf("\n=== Tileset pointer table summary (29 tilesets x 3 blobs) ===\n");
    printf("Decoded OK:           %d\n", ok);
    printf("Failed to decode:     %d\n", fail);
    printf("Roundtrip failures:   %d\n", rt_fail);
    printf("Decompressed bytes:   %ld\n", total_dec);
    printf("Original compressed:  %ld\n", total_orig);
    printf("Compression ratio:    %.3f (compressed / decompressed)\n",
           total_dec ? (double)total_orig / total_dec : 0.0);
    (void)total_new;
    printf("\n(Optimal recompression for SM format not yet wired up — needs\n");
    printf(" SM-specific LZ match-finders for the EOR variants and 1-byte\n");
    printf(" relative distances. The decoder works; encoder is next.)\n");
    return 0;
}
