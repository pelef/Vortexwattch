ROOT_DIR := $(realpath ../../..)
include $(ROOT_DIR)/config.mk

RTL := $(VORTEX_HOME)/hw/rtl
TOP := power_counters_tb
CONFIGS ?= -DNUM_THREADS=8 -DSIMD_WIDTH=4 -DISSUE_WIDTH=2 -DNUM_CORES=8 -DNUM_CLUSTERS=2
FLAGS := --binary --timing --assert -Wall -Wpedantic --timescale-override 1ns/1ps
# Existing RTL libraries have multi-module files and unnamed generate blocks
# (same structural naming rules as hw/unittest/common.mk).
FLAGS += -Wno-DECLFILENAME -Wno-GENUNNAMED
FLAGS += -DSIMULATION -DPOWER_ENABLE -DXLEN_$(XLEN) $(CONFIGS)
FLAGS += -I$(RTL) -I$(RTL)/core -I$(RTL)/interfaces -I$(RTL)/fpu -I$(RTL)/libs
SOURCES := $(RTL)/VX_gpu_pkg.sv $(RTL)/fpu/VX_fpu_pkg.sv
SOURCES += $(VORTEX_HOME)/hw/unittest/power_counters/$(TOP).sv

.PHONY: all run clean
all:
	verilator $(FLAGS) --top-module $(TOP) -j 2 $(SOURCES)
run: all
	./obj_dir/V$(TOP)
clean:
	rm -rf obj_dir
