#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Convert Vortex POWER JSON lines to scoped long-form CSV; never sum shared counters."""

import argparse
import csv
import json
from pathlib import Path
import sys

ABI = 0x56585001
FIELDS = ("run", "bank", "core", "socket", "cluster", "scope", "scope_id", "counter", "value",
          "unit", "snapshot_cycles", "counter_bits", "cores", "threads", "warps", "isa_flags", "memory_block_bytes")


def integer(record, key, minimum=0, maximum=None):
    value = record.get(key)
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError("invalid integer field: " + key)
    return value


def extract(lines, run, shared="all"):
    records, seen = [], set()
    for line in lines:
        if not line.startswith("POWER: "):
            continue
        record = json.loads(line[len("POWER: "):])
        if not isinstance(record, dict):
            raise ValueError("power record must be an object")
        if record.get("schema") != 1 or record.get("abi") != ABI:
            raise ValueError("unsupported power counter ABI/schema")
        bank = integer(record, "bank", 3, 7)
        core = integer(record, "core")
        if (bank, core) in seen:
            raise ValueError("multiple dumps of the same bank/core: keep each run in a separate log")
        seen.add((bank, core))
        bits = integer(record, "counter_bits", 44, 44)
        for name in ("socket", "cluster", "snapshot_cycles", "isa_flags"):
            integer(record, name)
        for name in ("cores", "threads", "warps", "memory_block_bytes"):
            integer(record, name, 1)
        if core >= record["cores"]:
            raise ValueError("core ID outside device core count")
        if record["snapshot_cycles"] >= 1 << bits:
            raise ValueError("snapshot cycles exceed counter width")
        if not isinstance(record.get("counters"), dict) or not record["counters"]:
            raise ValueError("missing counter data")
        records.append(record)
    if not records:
        raise ValueError("no POWER records; enable POWER_ENABLE and select a power bank")
    rows, owners = [], set()
    for record in sorted(records, key=lambda r: (r["bank"], r["core"])):
        for name, metric in record["counters"].items():
            if not isinstance(metric, dict):
                raise ValueError("counter metric must be an object")
            scope = metric.get("scope")
            if scope not in ("core", "socket", "cluster", "device"):
                raise ValueError("unknown counter scope")
            scope_id = 0 if scope == "device" else record[scope]
            key = (record["bank"], scope, scope_id, name)
            value = integer(metric, "value", 0, (1 << record["counter_bits"]) - 1)
            unit = metric.get("unit")
            if unit not in ("cycles", "warp_cycles", "thread_cycles", "warp_instructions",
                            "lane_instructions", "issue_cycles", "lane_words", "requests", "lane_requests", "blocks"):
                raise ValueError("unknown counter unit")
            if shared == "representative" and key in owners:
                continue
            owners.add(key)
            row = {key: record[key] for key in FIELDS if key in record}
            row.update(run=run, scope=scope, scope_id=scope_id, counter=name, value=value, unit=unit)
            rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--shared", choices=("all", "representative"), default="all",
                        help="representative keeps the lowest-core snapshot per shared scope; never sums")
    options = parser.parse_args(argv)
    try:
        rows = []
        for path in options.logs:
            with path.open() as source:
                rows.extend(extract(source, str(path), options.shared))
        writer = csv.DictWriter(sys.stdout, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("counter extraction failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
