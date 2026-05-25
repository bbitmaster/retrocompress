// retrocompress.h - optimal Kirby-format encoder (DP shortest path).
#pragma once
#include <cstdint>

namespace Retrocompress {

using u8 = unsigned char;

// Compress src[0..srclen-1] into dst, returning bytes written (incl 0xFF terminator).
// dst must have room for at least worst_compress_size(srclen) bytes.
int compress(const u8* src, int srclen, u8* dst);

// Conservative upper bound on output size.
int worst_compress_size(int srclen);

// Standard Kirby-format decompressor with bounds checks. Returns decompressed
// size on success, -1 on error. If dst is null, just returns the size.
int decompress(const u8* src, int srclen, u8* dst);

// Super Metroid format decompressor. Same 3-bit-cmd / 5-bit-len skeleton but
// the LZ variants (4..7) have different semantics:
//   4 = LZ-copy (2-byte absolute addr)
//   5 = LZ-copy (2-byte absolute addr), each byte EOR'd with $FF
//   6 = LZ-copy (1-byte relative distance: src = j - distance)
//   7 = LZ-copy (1-byte relative distance), each byte EOR'd with $FF
int decompress_sm(const u8* src, int srclen, u8* dst);

} // namespace Retrocompress
