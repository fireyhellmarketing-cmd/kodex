import tempfile
import unittest
from pathlib import Path

from codex_core.excellence import (
    compare_trajectories,
    read_workspace_session,
    run_benchmarks,
    runtime_adapters,
    write_workspace_session,
)


class ExcellenceTests(unittest.TestCase):
    def test_workspace_session_restores_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            (workspace / "one.py").write_text("print('one')\n", encoding="utf-8")
            write_workspace_session(
                workspace,
                {
                    "open_files": ["one.py", "missing.py"],
                    "active_file": "one.py",
                    "unfinished_task_ids": [2, 4],
                },
            )
            session = read_workspace_session(workspace)
            self.assertEqual(session["open_files"], ["one.py"])
            self.assertEqual(session["active_file"], "one.py")
            self.assertEqual(session["unfinished_task_ids"], [2, 4])

    def test_local_benchmark_and_runtime_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            (workspace / "README.md").write_text("test\n", encoding="utf-8")
            adapters = {item["id"]: item for item in runtime_adapters()}
            self.assertTrue(adapters["local"]["available"])
            report = run_benchmarks(workspace)
            self.assertTrue(report["passed"])

    def test_trajectory_comparison_reports_deltas(self):
        first = {
            "run": {
                "id": 1,
                "status": "failed",
                "summary": "first",
                "steps": [
                    {"kind": "tool_started", "status": "failed"},
                ],
            }
        }
        second = {
            "run": {
                "id": 2,
                "status": "completed",
                "summary": "second",
                "steps": [
                    {"kind": "tool_started", "status": "completed"},
                    {"kind": "review", "status": "completed"},
                ],
            }
        }
        comparison = compare_trajectories(first, second)
        self.assertEqual(comparison["delta"]["steps"], 1)
        self.assertEqual(comparison["delta"]["failures"], -1)


if __name__ == "__main__":
    unittest.main()
