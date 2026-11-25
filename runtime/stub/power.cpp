// SPDX-License-Identifier: Apache-2.0
#include <common.h>
#include <inttypes.h>

namespace {
struct Metric {
  const char* name;
  const char* unit;
  const char* scope;
};

constexpr const char* operations[] = {
  "int_alu",
  "int_mul",
  "int_div",
  "branch",
  "fp32_add",
  "fp32_mul",
  "fp32_fma",
  "fp32_div",
  "fp32_sqrt",
  "fp32_misc",
  "fp64_add",
  "fp64_mul",
  "fp64_fma",
  "fp64_div",
  "fp64_sqrt",
  "fp64_misc",
  "load",
  "store",
  "fence",
  "csr",
  "warp_control",
  "tensor",
  "other"
};
static_assert(sizeof(operations) / sizeof(*operations) == VX_POWER_NUM_OPS, "power ABI");

constexpr Metric activity[] = {
  {"busy_cycles", "cycles", "core"},
  {"warp_cycles", "warp_cycles", "core"},
  {"resident_thread_cycles", "thread_cycles", "core"},
  {"issue_cycles", "cycles", "core"},
  {"decoded_warps", "warp_instructions", "core"},
  {"logical_read_words", "lane_words", "core"},
  {"writeback_words", "lane_words", "core"},
  {"issued_warps", "warp_instructions", "core"},
  {"issued_lanes", "lane_instructions", "core"},
  {"ifetch_requests", "requests", "core"},
  {"load_requests", "lane_requests", "core"},
  {"store_requests", "lane_requests", "core"}
};
constexpr Metric memory[] = {
  {"icache_reads", "requests", "socket"},
  {"icache_read_misses", "requests", "socket"},
  {"dcache_reads", "requests", "socket"},
  {"dcache_writes", "requests", "socket"},
  {"dcache_read_misses", "requests", "socket"},
  {"dcache_write_misses", "requests", "socket"},
  {"l2_reads", "requests", "cluster"},
  {"l2_writes", "requests", "cluster"},
  {"l2_read_misses", "requests", "cluster"},
  {"l2_write_misses", "requests", "cluster"},
  {"l3_reads", "requests", "device"},
  {"l3_writes", "requests", "device"},
  {"l3_read_misses", "requests", "device"},
  {"l3_write_misses", "requests", "device"},
  {"local_reads", "lane_requests", "core"},
  {"local_writes", "lane_requests", "core"},
  {"memory_reads", "blocks", "device"},
  {"memory_writes", "blocks", "device"}
};
}

extern int vx_dump_power(vx_device_h hdevice, FILE* stream) {
  if (!hdevice || !stream)
    return -1;
  uint32_t bank;
  CHECK_ERR(vx_dcr_read(hdevice, VX_DCR_BASE_MPM_CLASS, &bank), { return err; });
  if (bank < VX_DCR_MPM_CLASS_POWER || bank > VX_DCR_MPM_CLASS_POWER_MEM)
    return -1;
  uint64_t cores, threads, warps, isa;
  CHECK_ERR(vx_dev_caps(hdevice, VX_CAPS_NUM_CORES, &cores), { return err; });
  CHECK_ERR(vx_dev_caps(hdevice, VX_CAPS_NUM_THREADS, &threads), { return err; });
  CHECK_ERR(vx_dev_caps(hdevice, VX_CAPS_NUM_WARPS, &warps), { return err; });
  CHECK_ERR(vx_dev_caps(hdevice, VX_CAPS_ISA_FLAGS, &isa), { return err; });
  if (cores == 0 || threads == 0 || warps == 0)
    return -1;
  for (uint32_t core = 0; core < cores; ++core) {
    uint64_t abi, values[VX_POWER_SNAPSHOT_WORDS];
    CHECK_ERR(vx_mpm_query(hdevice, VX_CSR_POWER_ABI, core, &abi), { return err; });
    if (abi != VX_POWER_ABI) {
      fprintf(stderr, "Power counter ABI missing on core %u: rebuild RTL with POWER_ENABLE (got 0x%" PRIx64 ").\n", core, abi);
      return -1;
    }
    for (uint32_t i = 0; i < VX_POWER_SNAPSHOT_WORDS; ++i) {
      CHECK_ERR(vx_mpm_query(hdevice, VX_CSR_POWER_SNAPSHOT + i, core, &values[i]), { return err; });
    }
    if (values[27] != 44 || values[26] == 0) {
      fprintf(stderr, "Invalid power snapshot metadata on core %u.\n", core);
      return -1;
    }
    fprintf(stream, "POWER: {\"schema\":1,\"abi\":%" PRIu64 ",\"bank\":%u,\"core\":%u,"
            "\"socket\":%" PRIu64 ",\"cluster\":%" PRIu64 ",\"counter_bits\":%" PRIu64 ","
            "\"memory_block_bytes\":%" PRIu64 ",\"snapshot_cycles\":%" PRIu64 ","
            "\"cores\":%" PRIu64 ",\"threads\":%" PRIu64 ",\"warps\":%" PRIu64 ",\"isa_flags\":%" PRIu64 ",\"counters\":{",
            abi, bank, core, values[24], values[25], values[27], values[26], values[0], cores, threads, warps, isa);
    size_t count = VX_POWER_NUM_OPS;
    if (bank == VX_DCR_MPM_CLASS_POWER)
      count = sizeof(activity) / sizeof(*activity);
    if (bank == VX_DCR_MPM_CLASS_POWER_MEM)
      count = sizeof(memory) / sizeof(*memory);
    for (size_t i = 0; i < count; ++i) {
      Metric metric = {operations[i], "warp_instructions", "core"};
      if (bank == VX_DCR_MPM_CLASS_POWER)
        metric = activity[i];
      else if (bank == VX_DCR_MPM_CLASS_POWER_MEM)
        metric = memory[i];
      else if (bank == VX_DCR_MPM_CLASS_POWER_LANES)
        metric.unit = "lane_instructions";
      else if (bank == VX_DCR_MPM_CLASS_POWER_ISSUE)
        metric.unit = "issue_cycles";
      fprintf(stream, "%s\"%s\":{\"value\":%" PRIu64 ",\"unit\":\"%s\",\"scope\":\"%s\"}",
              i ? "," : "", metric.name, values[i+1], metric.unit, metric.scope);
    }
    fprintf(stream, "}}\n");
  }
  return ferror(stream) ? -1 : 0;
}
