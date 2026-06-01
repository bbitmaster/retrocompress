CXX = g++
CC = gcc
CXXFLAGS = -O2 -std=c++11 -Wall
CFLAGS = -O2 -Wall

# Compresch source dir (for the comparison binaries: walker_kirby_nes, bench,
# diff_one, scanner). Default: auto-extracted from the bundled rel2 zip.
# Override on the command line: make COMPRESCH_DIR=/path/to/your/src
COMPRESCH_VENDOR = third_party/compresch
COMPRESCH_DIR ?= $(COMPRESCH_VENDOR)/extracted/src

# libsais (Ilya Grebnov, MIT) - modern SA-IS suffix-array library, vendored.
LIBSAIS_DIR = third_party/libsais

INC = -Isrc -I$(COMPRESCH_DIR) -I$(LIBSAIS_DIR)
CSFLAGS = -O2 -std=c++11 -Wno-write-strings -Wno-narrowing -I$(COMPRESCH_DIR)

CS_OBJS = blocklist.o block_lzbitrev.o compresch_kirby.o compresch_stdblock.o crunchtree.o
# Everything that links retrocompress.o also needs libsais.o.
RC_OBJS = retrocompress.o libsais.o

CORE_BINS = test_basic walker_kirby_nes walker_super_metroid jsr_tracer_kirby repacker_kirby_nes
COMP_BINS = bench diff_one scanner

# Auto-extract compresch from the vendored zip at Makefile parse time if not
# already done. (Doing it via a shell directive here ensures the source files
# exist before pattern-rule resolution attempts to find them.)
ifeq ($(wildcard $(COMPRESCH_DIR)/compresch_kirby.h),)
    $(info Extracting third_party/compresch/compresch_rel2.zip ...)
    $(shell mkdir -p $(COMPRESCH_VENDOR)/extracted && cd $(COMPRESCH_VENDOR)/extracted && unzip -oq ../compresch_rel2.zip)
endif

all: $(CORE_BINS) $(COMP_BINS)
core: $(CORE_BINS)

retrocompress.o: src/retrocompress.cpp src/retrocompress.h $(LIBSAIS_DIR)/libsais.h
	$(CXX) $(CXXFLAGS) -I$(LIBSAIS_DIR) -c $< -o $@

libsais.o: $(LIBSAIS_DIR)/libsais.c $(LIBSAIS_DIR)/libsais.h
	$(CC) $(CFLAGS) -c $< -o $@

# Compresch object files - static pattern rule, one per CS_OBJS entry.
$(CS_OBJS): %.o: $(COMPRESCH_DIR)/%.cpp
	$(CXX) $(CSFLAGS) -c $< -o $@

test_basic: src/test_basic.cpp $(RC_OBJS)
	$(CXX) $(CXXFLAGS) -Isrc -o $@ $^

walker_kirby_nes: src/walker_kirby_nes.cpp $(RC_OBJS) $(CS_OBJS)
	$(CXX) $(CXXFLAGS) $(INC) -o $@ $^

walker_super_metroid: src/walker_super_metroid.cpp $(RC_OBJS)
	$(CXX) $(CXXFLAGS) -Isrc -o $@ $^

jsr_tracer_kirby: src/jsr_tracer_kirby.cpp $(RC_OBJS)
	$(CXX) $(CXXFLAGS) -Isrc -o $@ $^

repacker_kirby_nes: src/repacker_kirby_nes.cpp $(RC_OBJS)
	$(CXX) $(CXXFLAGS) -Isrc -o $@ $^

bench: src/bench.cpp $(RC_OBJS) $(CS_OBJS)
	$(CXX) $(CXXFLAGS) $(INC) -o $@ $^

diff_one: src/diff_one.cpp $(RC_OBJS) $(CS_OBJS)
	$(CXX) $(CXXFLAGS) $(INC) -o $@ $^

scanner: src/scanner.cpp $(RC_OBJS) $(CS_OBJS)
	$(CXX) $(CXXFLAGS) $(INC) -o $@ $^

clean:
	rm -f *.o $(CORE_BINS) $(COMP_BINS)

clean-vendor:
	rm -rf $(COMPRESCH_VENDOR)/extracted

.PHONY: all core clean clean-vendor
