// SPDX-License-Identifier: Apache-2.0
// Link with runtime/stub/power.cpp, not a real device backend.
#include <vortex.h>
#include <VX_types.h>
#include <cassert>
#include <cstdlib>
#include <cstring>

static uint32_t bank = VX_DCR_MPM_CLASS_POWER;
static bool missing_abi = false;
static bool query_error = false;

int vx_dcr_read(vx_device_h, uint32_t address, uint32_t* value) {
  assert(address == VX_DCR_BASE_MPM_CLASS);
  *value = bank;
  return 0;
}

int vx_dev_caps(vx_device_h, uint32_t capability, uint64_t* value) {
  switch (capability) {
  case VX_CAPS_NUM_CORES: *value = 2; break;
  case VX_CAPS_NUM_WARPS: *value = 4; break;
  case VX_CAPS_NUM_THREADS: *value = 8; break;
  case VX_CAPS_ISA_FLAGS: *value = 123; break;
  default: return -1;
  }
  return 0;
}

int vx_mpm_query(vx_device_h, uint32_t address, uint32_t core, uint64_t* value) {
  if (query_error)
    return -7;
  assert(core < 2);
  if (address == VX_CSR_POWER_ABI) {
    *value = missing_abi ? 0 : VX_POWER_ABI;
  } else {
    auto slot = address - VX_CSR_POWER_SNAPSHOT;
    assert(slot < VX_POWER_SNAPSHOT_WORDS);
    *value = slot == 27 ? 44 : (slot == 26 ? 64 : (1ull << 40) + slot);
  }
  return 0;
}

int main() {
  int sentinel;
  auto device = reinterpret_cast<vx_device_h>(&sentinel);
  for (bank = 3; bank <= 7; ++bank) {
    char* output = nullptr;
    size_t size = 0;
    auto stream = open_memstream(&output, &size);
    assert(stream);
    assert(vx_dump_power(device, stream) == 0);
    assert(fclose(stream) == 0);
    assert(strstr(output, "\"core\":0"));
    assert(strstr(output, "\"core\":1"));
    assert(strstr(output, "\"snapshot_cycles\":1099511627776"));
    assert(strstr(output, "\"counter_bits\":44"));
    if (bank == 7)
      assert(strstr(output, "\"scope\":\"socket\""));
    free(output);
  }
  bank = 3;
  missing_abi = true;
  assert(vx_dump_power(device, stdout) != 0);
  missing_abi = false;
  query_error = true;
  assert(vx_dump_power(device, stdout) == -7);
  query_error = false;
  bank = 2;
  assert(vx_dump_power(device, stdout) != 0);
  assert(vx_dump_power(nullptr, stdout) != 0);
  assert(vx_dump_power(device, nullptr) != 0);
  puts("Runtime power exporter tests passed");
}
