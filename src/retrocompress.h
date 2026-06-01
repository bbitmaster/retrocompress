// retrocompress.h - optimal LZ encoder for the HAL/Nintendo "3-bit cmd + 5-bit
// length" compression family (Kirby NES/SNES, SMW LC_LZ2, Super Metroid, etc.).
#pragma once
#include <cstdint>

namespace Retrocompress {

using u8 = unsigned char;

// Which dialect of the family to encode/decode.
//
//   KIRBY  : Kirby's Adventure (NES), Kirby Super Star (SNES). Supports inc-fill
//            (cmd 3), plain backref (cmd 4), bit-reverse backref (cmd 5), and
//            reverse-order backref (cmd 6).
//   LZ2    : Super Mario World base ROM (a.k.a. Lunar Compress LC_LZ2). Same
//            as Kirby minus the two alt-backref kinds.
enum class Format { KIRBY, LZ2 };

// Compress src[0..srclen-1] into dst, returning bytes written (incl 0xFF terminator).
// dst must have room for at least worst_compress_size(srclen) bytes.
// fmt defaults to KIRBY so existing call sites keep working.
int compress(const u8* src, int srclen, u8* dst, Format fmt = Format::KIRBY);

// Conservative upper bound on output size (same for every format in the family).
int worst_compress_size(int srclen);

// Decompressor with bounds checks. Returns decompressed size on success,
// -1 on error. If dst is null, just returns the size.
int decompress(const u8* src, int srclen, u8* dst, Format fmt = Format::KIRBY);

// Super Metroid format decompressor. Same 3-bit-cmd / 5-bit-len skeleton but
// the LZ variants (4..7) have different semantics:
//   4 = LZ-copy (2-byte absolute addr)
//   5 = LZ-copy (2-byte absolute addr), each byte EOR'd with $FF
//   6 = LZ-copy (1-byte relative distance: src = j - distance)
//   7 = LZ-copy (1-byte relative distance), each byte EOR'd with $FF
int decompress_sm(const u8* src, int srclen, u8* dst);

} // namespace Retrocompress
