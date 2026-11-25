ROOT_DIR := $(realpath ../..)
include $(ROOT_DIR)/config.mk

CXXFLAGS := -std=c++17 -Wall -Wextra -Werror -pedantic
CXXFLAGS += -I$(VORTEX_HOME)/runtime/include -I$(VORTEX_HOME)/runtime/common
CXXFLAGS += -I$(VORTEX_HOME)/sim/common -I$(ROOT_DIR)/hw
SOURCES := $(VORTEX_HOME)/tests/power/test_export.cpp $(VORTEX_HOME)/runtime/stub/power.cpp

.PHONY: all run clean
all: test_export
test_export: $(SOURCES)
	$(CXX) $(CXXFLAGS) $(SOURCES) -o $@
run: test_export
	./test_export
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s $(VORTEX_HOME)/tests/power -v
clean:
	rm -f test_export
