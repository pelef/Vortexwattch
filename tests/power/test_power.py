# SPDX-License-Identifier: Apache-2.0
"""No FPGA, Docker daemon, XRT installation, or network required."""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ci"))
import fpga_power as power
import power_extract as extract


class Sensors:
    def get(self, name):
        if name == "electrical":
            return {"power_consumption_watts": "25.5"}
        return {}


def row(t, p, phase="active"):
    return {"monotonic_s": t, "power_watts": p, "phase": phase}


class PowerTests(unittest.TestCase):
    def test_bdf(self):
        self.assertEqual(power.bdf("0000:AF:00.1"), "0000:af:00.1")
        for value in ("0", "86:00.1", "0000:86:00.0", "0000:86:20.1", "../../etc"):
            with self.assertRaises(ValueError):
                power.bdf(value)

    def test_power_values(self):
        self.assertEqual(power.watts({"power_consumption_watts": "0"}), 0)
        self.assertEqual(power.watts({"electrical": {"power_consumption_watts": "42.5"}}), 42.5)
        for value in (None, True, "N/A", "nan", "inf", "-1"):
            with self.assertRaises((ValueError, TypeError)):
                power.watts({"power_consumption_watts": value})
        with self.assertRaises(ValueError):
            power.watts({})

    def test_idle_check_fails_closed(self):
        power.check_idle_text("outstanding execs: 0\ncontexts: 0\n")
        for text in ("", "contexts: 0", "contexts: 1\noutstanding execs: 0",
                     "contexts: 0\noutstanding execs: 2"):
            with self.assertRaises(RuntimeError):
                power.check_idle_text(text)

    def test_energy_actual_timestamps(self):
        result = power.integrate([row(0, 10), row(0.2, 20), row(0.5, 30)], "active", 1, 5)
        self.assertAlmostEqual(result["board_energy_joules"], 10.5)
        self.assertAlmostEqual(result["covered_seconds"], 0.5)
        self.assertAlmostEqual(result["baseline_subtracted_joules"], 8)

    def test_no_bridging_invalid_large_gaps_or_phases(self):
        rows = [row(0, 10), row(1, None), row(2, 10), row(3, 10), row(4, 10),
                row(100, 10), row(101, 10, "warmup"), row(102, 10)]
        result = power.integrate(rows, "active", 2)
        self.assertEqual(result["covered_seconds"], 2)
        self.assertEqual(result["board_energy_joules"], 20)

    def test_short_window_and_negative_baseline(self):
        self.assertIsNone(power.integrate([row(0, 20), row(1, 20)], "active", 2)["board_energy_joules"])
        result = power.integrate([row(0, 10), row(1, 10), row(2, 10)], "active", 2, 20)
        self.assertEqual(result["baseline_subtracted_joules"], -20)

    def test_sensor_failure_is_not_zero(self):
        provider = mock.Mock()
        provider.get.side_effect = RuntimeError("permission denied")
        result = power.sample(provider, "active")
        self.assertIsNone(result["power_watts"])
        self.assertIn("permission denied", result["error"])
        self.assertGreaterEqual(result["total_query_s"], result["power_query_s"])

    def options(self, output, command=None, duration=0.06):
        return argparse.Namespace(output=str(output), device="0000:86:00.1", interval=0.01,
                                  baseline=0, warmup=0, duration=duration, command=command or [])

    def test_passive_collection_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            with mock.patch.object(power.subprocess, "Popen", side_effect=AssertionError("passive must not launch")):
                self.assertEqual(power.collect(self.options(output), Sensors()), 0)
            metadata = json.loads((output / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "completed")
            self.assertGreater(len((output / "samples.jsonl").read_text().splitlines()), 2)
            with self.assertRaises(FileExistsError):
                power.collect(self.options(output), Sensors())

    def test_failed_command_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "failed"
            options = self.options(output, [sys.executable, "-c", "raise SystemExit(7)"], 5)
            self.assertEqual(power.collect(options, Sensors()), 1)
            metadata = json.loads((output / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "command_failed")
            self.assertEqual(metadata["command_exit_code"], 7)

    def test_timeout_terminates_own_command(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "timeout"
            options = self.options(output, [sys.executable, "-c", "import time; time.sleep(60)"], 0.05)
            self.assertEqual(power.collect(options, Sensors()), 1)
            metadata = json.loads((output / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "timeout")
            self.assertIsNotNone(metadata["command_exit_code"])

    def test_launch_failure_and_interruption_keep_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "missing-command"
            options = self.options(output, [str(Path(temp) / "does-not-exist")])
            self.assertEqual(power.collect(options, Sensors()), 1)
            self.assertEqual(json.loads((output / "metadata.json").read_text())["status"], "error")
            output = Path(temp) / "interrupted"
            with mock.patch.object(power, "sample", side_effect=KeyboardInterrupt):
                self.assertEqual(power.collect(self.options(output), Sensors()), 1)
            self.assertEqual(json.loads((output / "metadata.json").read_text())["status"], "interrupted")


class ExtractTests(unittest.TestCase):
    def record(self, core=0):
        return {"schema": 1, "abi": extract.ABI, "bank": 7, "core": core, "socket": 0,
                "cluster": 0, "counter_bits": 44, "memory_block_bytes": 64,
                "snapshot_cycles": 123, "cores": 2, "threads": 4, "warps": 4, "isa_flags": 0,
                "counters": {"dcache_reads": {"value": 42, "unit": "requests", "scope": "socket"},
                             "local_reads": {"value": 3, "unit": "lane_requests", "scope": "core"}}}

    def lines(self, *records):
        return ["noise\n"] + ["POWER: " + json.dumps(r) for r in records]

    def test_shared_counters_not_added(self):
        records = self.lines(self.record(1), self.record(0))
        all_rows = extract.extract(records, "test")
        self.assertEqual(len(all_rows), 4)
        unique = extract.extract(records, "test", "representative")
        self.assertEqual(len(unique), 3)
        shared = [r for r in unique if r["counter"] == "dcache_reads"]
        self.assertEqual(shared[0]["value"], 42)
        self.assertEqual(shared[0]["core"], 0)

    def test_duplicate_run_rejected(self):
        with self.assertRaises(ValueError):
            extract.extract(self.lines(self.record(), self.record()), "test")

    def test_missing_or_wrong_abi(self):
        with self.assertRaises(ValueError):
            extract.extract(["Test passed!"], "test")
        record = self.record()
        record["abi"] = 0
        with self.assertRaises(ValueError):
            extract.extract(self.lines(record), "test")

    def test_invalid_metadata(self):
        for field, value in (("bank", 2), ("threads", 0), ("counter_bits", 64), ("core", -1)):
            record = self.record()
            record[field] = value
            with self.assertRaises(ValueError):
                extract.extract(self.lines(record), "test")

    def test_invalid_counter(self):
        for field, value in (("value", 1 << 44), ("value", True), ("scope", "gpu"), ("unit", "watts")):
            record = copy.deepcopy(self.record())
            record["counters"]["dcache_reads"][field] = value
            with self.assertRaises(ValueError):
                extract.extract(self.lines(record), "test")


if __name__ == "__main__":
    unittest.main()
