# Power-modeling activity counters and FPGA telemetry

This extension to Vortex **v2.3** provides synthesizable, opt-in activity counters,
machine-readable runtime exports, and a read-only XRT board-power recorder.
It is measurement infrastructure, **not a calibrated power predictor**. Existing
bitstreams do not contain the new counters: rebuild the RTL with `POWER_ENABLE`.

## Research basis and limits

The design follows the event-accounting approach of
[AccelWattch (Kandiah et al., MICRO 2021)](https://paragon.cs.northwestern.edu/papers/2021-MICRO-AccelWattch-Kandiah.pdf),
especially its component/activity separation and operation-specific modeling.
It also follows the native-event-unit and component-scope distinctions in the
user-supplied manuscript **An Event-Driven Framework for Component-Level Power
Modeling in AMD GPUs**, particularly Sections 3–4 and Tables 4–5. The latter was
supplied as `A_Chiplet_Aware_Power_Modeling_Framework_for_AMD_GPUs-3.pdf`; its
title page lists anonymous authors. The private PDF is not redistributed here.

| Modeling feature | Vortex measurement | Important distinction |
| --- | --- | --- |
| Component activation | Core busy cycles, resident warp/thread cycle sums | Residency/busy are proxies, not physical power-gating state |
| Integer/FP execution | Accepted warp instructions and active-lane instructions, by operation | A lane FMA is one instruction, not two FLOPs |
| Operation active cycles | Cycles accepting at least one packet of that category | These are **issue cycles**, not cycles an iterative FU stays busy |
| Register activity | Decode-used operand lane-words and writeback lane-words | Logical operand demand / writeback traffic, not physical RF bank accesses |
| Cache and memory activity | Existing cache request/miss, local-memory and external-memory counters | Keep hierarchy scope and request/block units; do not infer HBM wire traffic |
| Total measured power | Timestamped XRT board watts and thermal readings | FPGA board/shell/HBM power, not an NVIDIA/AMD ASIC component measurement |

No NVIDIA/AMD energy coefficients, scaling factors, chiplet topology, or claimed
prediction accuracy are imported. Vortex SFU means CSR/warp control here, not
NVIDIA transcendental math. FP16, texture/raster, explicit clock/power gating,
RF bank reads, per-unit execution occupancy, and datatype-specific tensor
operation counts are not implemented by this extension. The generic tensor
instruction category is available when the existing TCU is enabled. Existing
profiling classes 1 and 2 remain available for pipeline stalls and memory latency.

## Enable and run locally

Use an out-of-tree **v2.3** build with its matching toolchain/dependencies already
available. Re-run `configure` after adding this extension so the new Makefiles
and scripts are copied to the build tree. From that configured build directory:

```bash
source ci/toolchain_env.sh
make -j2 -C hw
make -j2 -C kernel
make -j2 -C runtime/stub
make -j1 -C runtime/rtlsim THREADS=2 CONFIGS="-DPOWER_ENABLE"
make -j2 -C tests/regression/sgemm

# Five deterministic replays; one bank is saved by the kernel exit path per run.
mkdir -p power-results
for bank in 3 4 5 6 7; do
    VORTEX_PROFILING="$bank" make -C tests/regression/sgemm \
        run-rtlsim OPTS=-n16 > "power-results/sgemm-bank-$bank.log" 2>&1 || break
done
python3 ci/power_extract.py power-results/sgemm-bank-{3,4,5,6,7}.log \
    > power-results/counters.csv
```

Alternatively, `ci/blackbox.sh --driver=rtlsim --app=sgemm --perf=3` enables the
feature automatically. `CONFIGS` controls hardware configuration: keep the
runtime, simulator/bitstream and kernel configuration consistent. SimX does not
implement these RTL events and is explicitly rejected for power profiling.
Without `POWER_ENABLE`, the new counter/snapshot hardware is not instantiated;
`POWER_ENABLE` implies `PERF_ENABLE`.

For an FPGA build, add `CONFIGS="-DPOWER_ENABLE"` to the normal v2.3 XRT hardware
build, retaining the platform, core configuration and resource limits you already
validated. Build in a **new output directory**: the upstream FPGA flow does not
reliably invalidate every generated artifact after changing configuration flags.
Use matching Vitis/Vivado/platform tools. This change does not require a shell
flash or a server reboot. Rebuild the host runtime as well as the `.xclbin`.
The host XRT backend is built with `make -C runtime/xrt TARGET=hw` (without
`TARGET=hw`, that v2.3 Makefile defaults to the XRT simulator).
No new FPGA bitstream or hardware timing/resource result is included here.

## Counter bank ABI (version 1)

Select `VX_DCR_BASE_MPM_CLASS` before launching the kernel, normally via
`VORTEX_PROFILING`. Use a separate process for each bank; the v2.3 environment
selection is cached. `vx_dump_perf()`/device close export the chosen bank, or call
`vx_dump_power(device, stream)` explicitly after completion. Check its return
value. Output is one `POWER: {JSON}` line per core; missing ABI support produces
an error at `vx_ready_wait()` as well as export/close, not fabricated zero
measurements. Applications must check runtime return values.

| Class | Export | Counter unit |
| --- | --- | --- |
| 3 | Core activity, logical register traffic, instruction/load/store requests | Explicit per-field units |
| 4 | Operation mix | Warp instructions, counted on accepted `sop` |
| 5 | Operation mix | Active-lane instructions, summed across accepted SIMD packets |
| 6 | Operation mix | Per-category issue cycles, OR across all issue ports |
| 7 | Memory hierarchy activity | Requests, lane requests, or external memory blocks |

The 23 categories, in hardware order, are integer ALU, integer multiply, integer
divide/remainder, branch/jump, FP32 add/subtract, multiply, FMA, divide, sqrt,
miscellaneous; the same six FP64 categories; load, store, fence, CSR, warp
control, tensor, other. FP miscellaneous includes conversions, comparisons and
sign/min/max operations; its format follows the decoded FPU format, not the
datatype of both source and destination. Issued instructions are not retired
instructions. Backpressured dispatches do not count. Multiple same-category
issues in one cycle increase instruction/lane counts separately but issue
cycles only once. The overall issue-cycle counter is an OR over all categories,
so it need not equal their sum.

Class 3 also exports:

- `busy_cycles`: cycles the core's existing busy signal is asserted.
- `warp_cycles`, `resident_thread_cycles`: sums of active warps and their active
  thread masks per cycle; stalled/resident warps are included.
- `decoded_warps`, `logical_read_words`: accepted decode instructions and sum of
  used source operands times active lanes; this is not a read-port probe.
- `writeback_words`: active lanes on valid register writeback packets. A word
  is the Vortex register datapath word (XLEN), not necessarily 32 bits.
- `issued_warps`, `issued_lanes`: totals of the operation categories.
- `ifetch_requests`, `load_requests`, `store_requests`: existing pipeline events.

All counters are unsigned **44-bit modulo counters**, reset by their existing
core/cache/system reset domains. There is no overflow flag; bound each run so
none wraps. Thread/warp integrals can wrap much sooner than cycle counts.
Snapshot metadata includes core/socket/cluster IDs, counter width, thread/warp
counts, ISA/cache enable flags, and external block size. Disabled features can
have zero counts; interpret `isa_flags` using `VX_config.vh`, not as proof of
inactivity. Cache events retain the existing v2.3 counter implementation and
pipeline delays; inspect them before interpreting hits or deriving bytes.

Reading CSR **0xB03** in classes 3–7 returns **0x56585001** and captures the
selected bank atomically **for that core** on that clock edge. CSRs 0xB04–0xB1F
then read the frozen 28-word snapshot. RV32 high halves are at address +0x80;
the ABI high half is zero. Layout: word 0 snapshot cycles, words 1–23 metrics,
word 24 socket ID, 25 cluster ID, 26 memory block bytes, 27 counter width.
Unused metric slots are zero. The existing `kernel/src/vx_perf.c` reads 0xB03
before these slots and writes the usual shadow buffer; no new kernel ABI is
needed. Normal cycle/retired CSRs retain their old meanings.

The window starts at reset and ends at the snapshot. It includes runtime startup
and some exit/profiling instructions, not just the application's inner loop.
The snapshot itself does not freeze event accumulation. Snapshot readout has
one shared register bank per core: serialize readers across that core's warps,
as the normal kernel exit path does. Capture occurs on an accepted CSR read,
not on a backpressured request. There is no global inter-core barrier.
Later core snapshots can therefore see different
shared-cache/memory totals. Bank replays must use the same inputs/configuration;
non-deterministic runs cannot be combined into a claimed single execution.

## Shared counters and CSV extraction

I/D caches are **socket** scoped; L2 is **cluster** scoped; L3 and the Vortex
external memory interface are **device** scoped. Local memory is core scoped.
The runtime exports these scopes on every metric. **Do not sum duplicate shared
measurements across cores.** `ci/power_extract.py --shared representative` keeps
the lowest-core snapshot for each shared scope; it never sums them. This is a
representative observation, not a globally synchronized total. The default
`--shared all` retains all snapshots for analysis. Repeated dumps of the same
bank/core in one log are rejected; save separate runs in separate files.

`memory_reads/writes * memory_block_bytes` describes full-block request capacity
at Vortex's external port, not useful application bytes or actual PCIe/HBM
traffic; write masks, coalescing, caches and shell traffic matter. No implicit
byte/transaction conversions or automatic ASIC power estimation are performed.

## FPGA power recording (later, on the FPGA host)

`ci/fpga_power.py` needs Python 3 and the matching **XRT `pyxrt`** binding. Source
XRT's setup script in the container first. It opens only the explicit PCI user
function, checks that XRT reports the same BDF, and queries electrical/thermal
information. It never calls FPGA programming/reset/clock-control APIs or sudo.
Keep device passthrough restricted to the approved U50 render device; do not use
`--privileged` or mount every FPGA device. Reserve the card with its users before
launching any application that will program it.

Passive observation (no application launch, no claimed idle baseline):

```bash
python3 ci/fpga_power.py --device 0000:86:00.1 \
    --output power-results/observation-001 --duration 30 --interval 0.1
```

For an explicit foreground workload command, add `--exclusive`. The recorder
then requires the device's read-only `kdsstat` to show zero outstanding commands
and contexts before opening it. If that report is missing/unreadable, it fails
closed. This check **is not a reservation** and cannot prevent a new user racing
the launch. The sampler's `--device` selects telemetry only: **your workload must
independently select the same U50**, e.g. with the verified numeric
`XRT_DEVICE_INDEX` used by the existing v2.3 runtime. Do not assume index 0.

```bash
# From the application's build directory, with XRT environment, runtime library
# path and the new XRT_XCLBIN_PATH already configured. Set U50_XRT_INDEX only
# after checking the index-to-BDF mapping in this exact container.
python3 /path/to/vortex/ci/fpga_power.py --device 0000:86:00.1 \
    --output /path/to/power-results/gemm-001 --exclusive \
    --baseline 5 --warmup 2 --duration 120 --interval 0.1 -- \
    env XRT_DEVICE_INDEX="${U50_XRT_INDEX:?Verify the U50 index first}" \
    VORTEX_DRIVER=xrt VORTEX_PROFILING=3 ./sgemm -n256
```

The command may itself load an `.xclbin`; its effects are not made read-only by
this wrapper. The recorded command window includes host setup/transfers/loading,
not just kernel execution. Warmup exclusion uses elapsed seconds, not kernel
markers. Choose a sufficiently long workload, or add an application-level
repeat/ROI mechanism before attempting kernel-only calibration. Do not background
or daemonize the command. Duration is a command timeout (terminated in its own
process group), not a way to force the workload to run longer. A hung XRT sensor
query can delay the timeout; no driver reset is attempted.

For comparable idle subtraction, establish an idle baseline with the **same
instrumented accelerator image**, clock settings and thermal conditions first.
An idle shell or another loaded accelerator is not an interchangeable baseline.
Retain the source commit, hardware configuration, application input/seed and
bitstream SHA-256 with the run; startup `dynamic_regions` metadata is recorded
but does not replace those experiment records.

Output is a **new directory** with `samples.csv`, raw `samples.jsonl`,
`workload.log`, `metadata.json` (including platform/clock/PCIe information), and
`summary.json`. Each power sample records monotonic midpoint and wall timestamps,
query duration, and a phase: baseline/warmup/active/completion boundary. Thermal
readings retain XRT's labels, units and presence flags in raw JSON. Sensor clocks
are captured at startup, not assumed constant; control/record changes separately.

Integration uses actual timestamps and only adjacent valid samples in the same
phase. It does not bridge failed queries or gaps above 2.5 requested intervals,
extrapolate to command endpoints, or substitute zero for unavailable sensors.
At least three valid samples and two intervals are required for an energy
estimate. The summary reports **covered seconds**, not full-command energy.
Command failure, timeout, interruption, and insufficient samples return nonzero.
An idle-subtracted result may be negative and is not clamped or labeled pure
dynamic power. Missing/failed samples remain visible even if coverage is usable.

Polling at 10 Hz does not prove a 10 Hz independent sensor update rate or resolve
short kernels. Check telemetry cadence/quantization, thermal drift and coverage;
collect repeat runs. Fit FPGA-specific coefficients on matched activity/energy
windows, keep calibration and held-out workloads separate, and retain baseline
and frequency/temperature information. The counters themselves add FPGA area,
routing, switching and readout overhead; measure this overhead with a comparable
non-instrumented build before making power claims.

## Tests

From the configured build tree with Verilator 5.028 available:

```bash
make -C hw/unittest/power_counters run
make -C hw/unittest/power_counters run XLEN=64
make -C tests/power run
python3 -m unittest discover -s /path/to/vortex/tests/power -v
python3 /path/to/vortex/tests/power/check_runs.py power-results/sgemm-bank-{3,4,5,6,7}.log
# For the supplied regression SGEMM at -n16: exactly 16^3 FP32 lane FMAs.
python3 /path/to/vortex/tests/power/check_runs.py --expect-fma-lanes 4096 \
    power-results/sgemm-bank-{3,4,5,6,7}.log
```

The RTL unit test exercises operation classification, masked lanes, SIMD SOP,
two issue ports, idle/backpressure, register events, counter wrap/reset, frozen
snapshots, metadata and RV32 high-half CSR reads. Cross-bank checks compare
deterministic RTL workload replays, not FPGA executions. Python tests use fake
telemetry and local child processes; they never access a card. Hardware sensor
availability, synthesis utilization/timing and FPGA calibration still require
separate validation on the target system.

### Local validation, 2026-11-25

Validated against v2.3 (`d425a56`) in the existing v2.3 software container,
using Verilator 5.028, GCC 11.4 and Python 3.10. Source was mounted read-only;
builds were out of tree, with a 2-CPU/4-GiB container limit, no network and no
FPGA device passthrough. The following checks passed:

- RV32 and RV64 counter/snapshot/CSR unit tests, including two issue ports and
  partial SIMD masks; integer-only and TCU-enabled classifier variants.
- The same unit test using the existing Xilinx synthesis-path population-count
  implementation (simulated by Verilator, **not** Vivado synthesis).
- Demo and `sgemm -n16` RTL runs across all five banks on 1 core, 4 warps,
  4 threads. Cross-bank totals match; GEMM reports exactly 4,096 FP32 lane FMAs.
- Four-core GEMM (2 clusters × 2 cores, 4 threads, 4 warps, issue width 2,
  L2/L3 enabled), all five banks and the same total of 4,096 FP32 lane FMAs.
- Default feature-disabled RTL GEMM; a power-profiling request on that build
  is rejected as missing the hardware ABI. No power-counter/snapshot objects
  appear in its generated simulator.
- Ordinary SimX demo and explicit rejection of SimX power profiling.
- XRT host-backend compilation, mock C++ runtime export/error tests, and
  16 Python extraction/telemetry tests. No physical XRT device was opened.

A wider configuration (2 clusters × 2 cores, 8 threads, SIMD width 4, issue
width 2, L2/L3 enabled) hits the existing `VX_lsu_slice.sv` **broken sop request**
assertion in GEMM. It reproduces with `POWER_ENABLE` disabled. That separate
configuration issue was not changed; partial-packet counter behavior is covered
by the isolated RTL unit test, but this workload/configuration is not qualified.
