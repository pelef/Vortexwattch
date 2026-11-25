#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only XRT board-power sampling, optionally around an explicit command.

No programming, reset, clock changes, sudo, or privileged device access. See
docs/power_modeling.md for sampling limits and FPGA/ASIC calibration differences.
"""

import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import time


def bdf(value):
    value = value.lower()
    if not re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[01][0-9a-f]\.1", value):
        raise ValueError("use an explicit PCI user-function BDF, e.g. 0000:86:00.1")
    return value


def watts(electrical):
    # pyxrt returns the sensor object, whereas xbutil wraps it in 'electrical'.
    electrical = electrical.get("electrical", electrical)
    value = electrical.get("power_consumption_watts")
    if value is None or isinstance(value, bool):
        raise ValueError("board power unavailable")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid board power")
    return result


def check_idle_text(text):
    """Fail closed: this is a point-in-time check, NOT a device reservation."""
    for field in ("outstanding execs", "contexts"):
        match = re.search(r"^\s*" + field + r":\s*(\d+)\s*$", text, re.MULTILINE)
        if not match or int(match[1]) != 0:
            raise RuntimeError("U50 not demonstrably idle: " + field)


class XrtSensors:
    def __init__(self, address):
        import pyxrt  # optional dependency, only needed on the FPGA host
        self.info = pyxrt.xrt_info_device
        self.device = pyxrt.device(address)
        actual = bdf(self.device.get_info(self.info.bdf))
        if actual != address:
            raise RuntimeError("XRT opened a different PCI device: " + actual)

    def get(self, name):
        return json.loads(self.device.get_info(getattr(self.info, name)))


def sample(provider, phase):
    start = time.monotonic()
    wall = time.time()
    row = {"phase": phase, "power_watts": None, "error": ""}
    try:
        row["electrical"] = provider.get("electrical")
        row["power_watts"] = watts(row["electrical"])
    except Exception as error:
        row["error"] = str(error)
    end = time.monotonic()
    row.update(monotonic_s=(start + end) / 2, wall_time_s=wall + (end-start)/2,
               power_query_s=end-start)
    try:
        row["thermal"] = provider.get("thermal")
    except Exception as error:
        row["thermal_error"] = str(error)
    row["total_query_s"] = time.monotonic() - start
    return row


def integrate(rows, phase, max_gap, baseline=None):
    """Trapezoids only between adjacent valid samples in the SAME phase.

    Never bridge failed reads, large gaps, or phase boundaries; never extrapolate
    to the beginning/end of the command. Baseline subtraction may be negative.
    """
    energy, covered, pairs = 0.0, 0.0, 0
    valid = [r for r in rows if r["phase"] == phase and r["power_watts"] is not None]
    for left, right in zip(rows, rows[1:]):
        if left["phase"] != phase or right["phase"] != phase:
            continue
        if left["power_watts"] is None or right["power_watts"] is None:
            continue
        dt = right["monotonic_s"] - left["monotonic_s"]
        if not 0 < dt <= max_gap:
            continue
        energy += (left["power_watts"] + right["power_watts"]) / 2 * dt
        covered += dt
        pairs += 1
    # At least 3 valid readings and 2 intervals; even this is not sufficient
    # to resolve kernels shorter than the sensor's independent update period.
    usable = len(valid) >= 3 and pairs >= 2
    return {"valid_samples": len(valid), "covered_seconds": covered, "intervals": pairs,
            "board_energy_joules": energy if usable else None,
            "mean_watts": energy / covered if usable else None,
            "baseline_subtracted_joules": energy - baseline * covered
            if usable and baseline is not None else None}


def stop_child(child):
    """Only terminate the process group created by THIS invocation."""
    if child is None or child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=5)
    except ProcessLookupError:
        child.wait()


def collect(options, provider):
    output = Path(options.output)
    output.mkdir(parents=True, exist_ok=False)
    command = options.command
    rows, child = [], None
    metadata = {"schema": 1, "device": options.device, "command": command,
                "requested_interval_s": options.interval, "baseline_s": options.baseline,
                "warmup_s": options.warmup, "duration_limit_s": options.duration,
                "start_wall_time_s": time.time(), "status": "running",
                "environment": {k: os.environ.get(k) for k in
                                ("VORTEX_DRIVER", "VORTEX_PROFILING", "XRT_DEVICE_INDEX", "XRT_XCLBIN_PATH")},
                "scope": "XRT board telemetry; command window, not kernel-only or ASIC power"}
    for name in ("platform", "pcie_info", "dynamic_regions"):
        try:
            metadata[name] = provider.get(name)
        except Exception as error:
            metadata[name + "_error"] = str(error)
    fields = ("monotonic_s", "wall_time_s", "phase", "power_watts", "power_query_s", "total_query_s", "error")
    command_start = None
    try:
        with (output / "samples.jsonl").open("x") as raw, \
                (output / "samples.csv").open("x", newline="") as csvfile, \
                (output / "workload.log").open("x") as log:
            writer = csv.DictWriter(csvfile, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()

            def record(phase):
                row = sample(provider, phase)
                if phase != "baseline":
                    row["phase"] = "warmup" if row["monotonic_s"]-command_start < options.warmup else "active"
                    if child is not None and child.poll() is not None:
                        row["phase"] = "completion_boundary"
                rows.append(row)
                raw.write(json.dumps(row, allow_nan=False) + "\n")
                raw.flush()
                writer.writerow(row)
                csvfile.flush()

            deadline = time.monotonic() + options.baseline
            while time.monotonic() < deadline:
                before = time.monotonic()
                record("baseline")
                time.sleep(max(0, min(options.interval - (time.monotonic()-before), deadline-time.monotonic())))

            command_start = time.monotonic()
            metadata["command_start_monotonic_s"] = command_start
            if command:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True)
                metadata["child_pid"] = child.pid
            deadline = command_start + options.duration
            while time.monotonic() < deadline:
                if child is not None and child.poll() is not None:
                    break
                before = time.monotonic()
                phase = "warmup" if before-command_start < options.warmup else "active"
                record(phase)
                # A query that straddles command completion cannot be assigned
                # wholly to its active interval; retain it, but exclude it.
                if child is not None and child.poll() is not None:
                    break
                time.sleep(max(0, min(options.interval - (time.monotonic()-before), deadline-time.monotonic())))
            if child is not None and child.poll() is None:
                metadata["status"] = "timeout"
                stop_child(child)
            else:
                metadata["status"] = "completed" if child is None or child.returncode == 0 else "command_failed"
    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
    except Exception as error:
        metadata.update(status="error", error=str(error))
    finally:
        stop_child(child)
        metadata["end_monotonic_s"] = time.monotonic()
        metadata["command_exit_code"] = None if child is None else child.returncode
        baseline_stats = integrate(rows, "baseline", 2.5 * options.interval)
        baseline = baseline_stats["mean_watts"]
        active = integrate(rows, "active", 2.5 * options.interval, baseline)
        gaps = [b["monotonic_s"] - a["monotonic_s"] for a, b in zip(rows, rows[1:])]
        summary = {"baseline": baseline_stats, "active": active,
                   "actual_median_interval_s": statistics.median(gaps) if gaps else None,
                   "failed_samples": sum(r["power_watts"] is None for r in rows),
                   "completion_boundary_samples": sum(r["phase"] == "completion_boundary" for r in rows),
                   "warning": "Energy covers valid adjacent samples only, not the entire command. "
                              "Polling cadence does not establish the sensor update rate."}
        if active["board_energy_joules"] is None and metadata["status"] == "completed":
            metadata["status"] = "insufficient_samples"
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
        (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return 0 if metadata["status"] == "completed" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="PCI BDF of the user function, not device index 0")
    parser.add_argument("--output", required=True, help="new directory; never overwrites a previous run")
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--duration", type=float, default=30, help="maximum command/observation seconds")
    parser.add_argument("--baseline", type=float, default=0, help="pre-command idle-baseline seconds")
    parser.add_argument("--warmup", type=float, default=0, help="initial command seconds excluded from active integration")
    parser.add_argument("--exclusive", action="store_true", help="confirm exclusive reservation; required to run a command/baseline")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="optional: -- executable arguments (no implicit shell)")
    options = parser.parse_args(argv)
    if options.command[:1] == ["--"]:
        options.command = options.command[1:]
    try:
        options.device = bdf(options.device)
        if not all(math.isfinite(x) for x in (options.interval, options.duration, options.baseline, options.warmup)):
            raise ValueError("time options must be finite")
        if options.interval < 0.1 or options.duration <= 0 or options.baseline < 0 or not 0 <= options.warmup < options.duration:
            raise ValueError("interval >= 0.1, duration > warmup >= 0, baseline >= 0 required")
        if options.command or options.baseline:
            if not options.exclusive:
                raise ValueError("reserve the FPGA with its users first, then supply --exclusive")
            check_idle_text((Path("/sys/bus/pci/devices") / options.device / "kdsstat").read_text())
        provider = XrtSensors(options.device)
        return collect(options, provider)
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print("power collection failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
