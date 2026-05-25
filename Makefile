CXX = g++
CXXFLAGS = -O2 -std=c++11 -Wall

# Compresch source dir (for the comparison binaries: walker_kirby_nes, bench,
# diff_one, scanner). Default: auto-extracted from the bundled rel2 zip.
# Override on the command line: make COMPRESCH_DIR=/path/to/your/src
COMPRESCH_VENDOR = third_party/compresch
COMPRESCH_DIR ?= $(COMPRESCH_VENDOR)/extracted/src

INC = -Isrc -I$(COMPRESCH_DIR)
CSFLAGS = -O2 -std=c++11 -Wno-write-strings -Wno-narrowing -I$(COMPRESCH_DIR)

CS_OBJS = blocklist.o block_lzbitrev.o compresch_kirby.o compresch_stdblock.o crunchtree.o

CORE_BINS = test_basic walker_kirby_nes walker_super_metroid jsr_tracer_kirby
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

retrocompress.o: src/retrocompress.cpp src/retrocompress.h
	$(CXX) $(CXXFLAGS) -c $< -o $@

# Compresch object files - static pattern rule, one per CS_OBJS entry.
$(CS_OBJS): %.o: $(COMPRESCH_DIR)/%.cpp
	$(CXX) $(CSFLAGS) -c $< -o $@

test_basic: src/test_basic.cpp retrocompress.o
	$(CXX) $(CXXFLAGS) -Isrc -o $@ $^

walker_kirby_nes: src/walker_kirby_nes.cpp retrocompress.o $(CS_OBJS)
	$(CXX) $(CXXFLAGS) $(INC) -o $@ $^

walker_super_metroid: src/walker_super_metroid.cpp retrocompress.o
	$(CXX) $(CXXFLAGS) -Isrc -o $@ $^

jsr_tracer_kirby: src/jsr_tracer_kirby.cpp retrocompress.o
	$(CXX) $(CXXFLAGS) -Isrc -o $@ $^

bench: src/bench.cpp retrocompress.o $(CS_OBJS)
	$(CXX) $(CXXFLAGS) $(INC) -o $@ $^

diff_one: src/diff_one.cpp retrocompress.o $(CS_OBJS)
	$(CXX) $(CXXFLAGS) $(INC) -o $@ $^

scanner: src/scanner.cpp retrocompress.o $(CS_OBJS)
	$(CXX) $(CXXFLAGS) $(INC) -o $@ $^

clean:
	rm -f *.o $(CORE_BINS) $(COMP_BINS)

clean-vendor:
	rm -rf $(COMPRESCH_VENDOR)/extracted

.PHONY: all core clean clean-vendor
