#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cross-bank checks for deterministic RTL runs: check_runs.py run-bank-{3,4,5,6,7}.log"""
import argparse
import json
from pathlib import Path
import sys


def check(paths, expected_fma=None):
    banks = {}
    for path in paths:
        text = Path(path).read_text()
        if "PASSED!" not in text:
            raise ValueError(str(path) + ": workload did not report success")
        for line in text.splitlines():
            if line.startswith("POWER: "):
                record = json.loads(line[7:])
                key = record["bank"], record["core"]
                if key in banks:
                    raise ValueError("duplicate bank/core")
                banks[key] = record
    cores = {core for bank, core in banks if bank == 3}
    if not cores or set(banks) != {(bank, core) for bank in range(3, 8) for core in cores}:
        raise ValueError("need all five banks on every core")
    for core in sorted(cores):
        activity = banks[3, core]
        if cores != set(range(activity["cores"])):
            raise ValueError("missing core snapshots")
        for bank in range(3, 8):
            record = banks[bank, core]
            for field in ("abi", "core", "cores", "socket", "cluster", "snapshot_cycles", "threads", "warps"):
                if record[field] != activity[field]:
                    raise ValueError("non-identical deterministic replay: " + field)
        warp = banks[4, core]["counters"]
        lane = banks[5, core]["counters"]
        issue = banks[6, core]["counters"]
        for name in warp:
            w, l, c = (x[name]["value"] for x in (warp, lane, issue))
            if not w <= l <= w * activity["threads"]:
                raise ValueError("invalid lane/warp ratio for " + name)
            if c > activity["snapshot_cycles"]:
                raise ValueError("issue cycles exceed elapsed cycles")
        for bank, name in ((4, "issued_warps"), (5, "issued_lanes")):
            if sum(m["value"] for m in banks[bank, core]["counters"].values()) != activity["counters"][name]["value"]:
                raise ValueError("activity total != sum of operation categories")
    if expected_fma is not None:
        actual = sum(banks[5, core]["counters"]["fp32_fma"]["value"] for core in cores)
        if actual != expected_fma:
            raise ValueError("FP32 lane FMA count: expected %d, got %d" % (expected_fma, actual))
    print("Cross-bank accounting passed for", len(cores), "core(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--expect-fma-lanes", type=int)
    options = parser.parse_args()
    try:
        check(options.logs, options.expect_fma_lanes)
    except (ValueError, OSError, KeyError) as error:
        sys.exit(str(error))
