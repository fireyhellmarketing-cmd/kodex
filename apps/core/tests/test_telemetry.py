import os
import tempfile
import unittest
from pathlib import Path

from codex_core.telemetry import SystemTelemetry


class SystemTelemetryTests(unittest.TestCase):
    def test_sample_reports_plausible_bounded_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            telemetry = SystemTelemetry(Path(temporary), os.getpid())
            sample = telemetry.sample()

        self.assertGreater(sample["timestamp"], 0)
        self.assertGreater(sample["system"]["diskTotalBytes"], 0)
        self.assertGreaterEqual(sample["system"]["diskUsedBytes"], 0)
        self.assertLessEqual(sample["system"]["diskUsedBytes"], sample["system"]["diskTotalBytes"])
        if sample["system"]["cpuPercent"] is not None:
            self.assertGreaterEqual(sample["system"]["cpuPercent"], 0)
            self.assertLessEqual(sample["system"]["cpuPercent"], 100)
        self.assertGreaterEqual(sample["kodex"]["memoryBytes"], 0)
        self.assertGreaterEqual(sample["kodex"]["processCount"], 0)
        self.assertIsNone(sample["system"]["gpuPercent"])
        self.assertEqual(len(sample["history"]), 1)


if __name__ == "__main__":
    unittest.main()
