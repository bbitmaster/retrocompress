// Quick correctness + sanity test: encode known patterns, decode, verify.
#include "retrocompress.h"
#include <cstdio>
#include <cstring>
#include <vector>

using u8 = Retrocompress::u8;

static int roundtrip(const u8* data, int N, int& out_compressed) {
    std::vector<u8> c(Retrocompress::worst_compress_size(N));
    int csz = Retrocompress::compress(data, N, c.data());
    if (csz < 0) { printf("  compress returned %d\n", csz); return -1; }
    out_compressed = csz;
    std::vector<u8> d(N + 16);
    int dsz = Retrocompress::decompress(c.data(), csz, d.data());
    if (dsz != N) { printf("  decompress returned %d, want %d\n", dsz, N); return -1; }
    if (std::memcmp(d.data(), data, N) != 0) {
        printf("  data mismatch!\n");
        for (int k = 0; k < N && k < 16; k++) printf("    [%d] want=%02X got=%02X\n", k, data[k], d[k]);
        return -1;
    }
    return 0;
}

static void test_case(const char* name, std::vector<u8> data) {
    int csz = 0;
    int rc = roundtrip(data.data(), (int)data.size(), csz);
    printf("%-30s  N=%-5d  csz=%-5d  ratio=%.3f  %s\n",
           name, (int)data.size(), csz,
           data.size() ? (double)csz / data.size() : 0.0,
           rc == 0 ? "OK" : "FAIL");
}

int main(int argc, char** argv) {
    if (argc >= 2) {
        // Compress a single file
        FILE* f = fopen(argv[1], "rb");
        if (!f) { perror(argv[1]); return 1; }
        fseek(f, 0, SEEK_END);
        long sz = ftell(f);
        fseek(f, 0, SEEK_SET);
        std::vector<u8> buf(sz);
        fread(buf.data(), 1, sz, f);
        fclose(f);
        int csz = 0;
        if (roundtrip(buf.data(), (int)sz, csz) == 0)
            printf("%s: N=%ld csz=%d ratio=%.3f OK\n", argv[1], sz, csz, (double)csz/sz);
        else
            printf("%s: FAIL\n", argv[1]);
        return 0;
    }

    // Pattern tests
    test_case("all zeros, 100", std::vector<u8>(100, 0));
    test_case("all zeros, 2000", std::vector<u8>(2000, 0));
    test_case("random walk pattern", []{ std::vector<u8> v(256); for (int k = 0; k < 256; k++) v[k] = k; return v; }());
    test_case("AB repeated, 100", []{ std::vector<u8> v; for (int k = 0; k < 50; k++) { v.push_back(0xAB); v.push_back(0xCD); } return v; }());
    test_case("ABC pattern, 99", []{ std::vector<u8> v; for (int k = 0; k < 33; k++) { v.push_back('A'); v.push_back('B'); v.push_back('C'); } return v; }());
    test_case("short literal", std::vector<u8>{1,2,3,4,5,6,7,8,9,10});
    test_case("inc 0..127", []{ std::vector<u8> v(128); for (int k = 0; k < 128; k++) v[k] = k; return v; }());
    test_case("palindrome", []{ std::vector<u8> v; for (int k = 0; k < 50; k++) v.push_back(k); for (int k = 49; k >= 0; k--) v.push_back(k); return v; }());
    test_case("bitrev pair", []{ std::vector<u8> v; for (int k = 0; k < 50; k++) v.push_back(0xA5); for (int k = 0; k < 50; k++) v.push_back(0xA5); return v; }());
    return 0;
}
