from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:
    resource = None


@dataclass(frozen=True)
class RuntimeAdapter:
    id: str
    available: bool
    executable: str
    isolation: str


def runtime_adapters() -> list[dict[str, Any]]:
    adapters = [
        RuntimeAdapter("local", True, "", "process-group and resource limits"),
        RuntimeAdapter("docker", bool(shutil.which("docker")), shutil.which("docker") or "", "container"),
        RuntimeAdapter("podman", bool(shutil.which("podman")), shutil.which("podman") or "", "container"),
    ]
    if platform.system() == "Darwin":
        adapters.append(
            RuntimeAdapter("sandbox-exec", bool(shutil.which("sandbox-exec")), shutil.which("sandbox-exec") or "", "macOS sandbox")
        )
    return [adapter.__dict__ for adapter in adapters]


def _resource_limits() -> None:
    if resource is None:
        return
    for limit, value in (
        (resource.RLIMIT_CPU, (30, 30)),
        (resource.RLIMIT_NOFILE, (256, 256)),
        (resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024)),
    ):
        try:
            resource.setrlimit(limit, value)
        except (OSError, ValueError):
            continue


def isolated_environment(extra: dict[str, str] | None = None) -> tuple[dict[str, str], tempfile.TemporaryDirectory[str]]:
    temporary = tempfile.TemporaryDirectory(prefix="kodex-runtime-")
    allowed = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "TERM", "SYSTEMROOT", "WINDIR"}
    }
    allowed.update(
        {
            "HOME": temporary.name,
            "TMPDIR": temporary.name,
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    allowed.update(extra or {})
    return allowed, temporary


def run_isolated(
    workspace: Path,
    command: list[str],
    *,
    adapter: str = "local",
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    environment, temporary = isolated_environment(env)
    try:
        actual = list(command)
        if adapter in {"docker", "podman"}:
            executable = shutil.which(adapter)
            if not executable:
                raise ValueError(f"{adapter} is not installed")
            actual = [
                executable,
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--memory",
                "1g",
                "--cpus",
                "1",
                "-v",
                f"{workspace}:/workspace:ro",
                "-w",
                "/workspace",
                "python:3.12-alpine",
                *command,
            ]
        result = subprocess.run(
            actual,
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            start_new_session=True,
            preexec_fn=_resource_limits if resource is not None and adapter == "local" else None,
        )
        return {
            "adapter": adapter,
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout[-100_000:],
            "stderr": result.stderr[-100_000:],
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    finally:
        temporary.cleanup()


def compare_trajectories(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    def metrics(trajectory: dict[str, Any]) -> dict[str, Any]:
        run = trajectory["run"]
        steps = run.get("steps", [])
        return {
            "run_id": run["id"],
            "status": run["status"],
            "steps": len(steps),
            "tool_calls": sum(step["kind"] == "tool_started" for step in steps),
            "failures": sum(step["status"] == "failed" for step in steps),
            "summary": run.get("summary", ""),
        }

    left = metrics(first)
    right = metrics(second)
    return {
        "first": left,
        "second": right,
        "delta": {
            key: right[key] - left[key]
            for key in ("steps", "tool_calls", "failures")
        },
    }


def benchmark_fixtures(workspace: Path) -> list[dict[str, Any]]:
    fixtures = [
        {
            "id": "repository-map",
            "name": "Repository mapping",
            "command": ["python3", "-c", "from pathlib import Path; assert any(Path('.').rglob('*'))"],
        },
        {
            "id": "python-compile",
            "name": "Python source compilation",
            "command": ["python3", "-m", "compileall", "-q", "apps/core/src"],
            "enabled": (workspace / "apps" / "core" / "src").exists(),
        },
    ]
    benchmark_roots = [
        workspace / ".kodex-agent" / "benchmarks",
        workspace / ".codex-agent" / "benchmarks",
    ]
    benchmark_files = {
        path.resolve(): path
        for root in benchmark_roots
        for path in root.glob("*.json")
    }
    for path in sorted(benchmark_files.values()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        command = payload.get("command")
        if isinstance(command, str):
            command = ["bash", "-lc", command]
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            continue
        fixtures.append(
            {
                "id": str(payload.get("id") or path.stem),
                "name": str(payload.get("name") or path.stem),
                "task": str(payload.get("task") or ""),
                "command": command,
                "expected_exit_code": int(payload.get("expected_exit_code", 0)),
                "source": str(path.relative_to(workspace)),
            }
        )
    return [fixture for fixture in fixtures if fixture.get("enabled", True)]


def run_benchmarks(workspace: Path, adapter: str = "local") -> dict[str, Any]:
    results = [
        {**fixture, **run_isolated(workspace, fixture["command"], adapter=adapter)}
        for fixture in benchmark_fixtures(workspace)
    ]
    passed_count = sum(
        result["exit_code"] == result.get("expected_exit_code", 0)
        for result in results
    )
    return {
        "schema_version": "1.0",
        "adapter": adapter,
        "passed": passed_count == len(results),
        "scorecard": {
            "total": len(results),
            "passed": passed_count,
            "pass_rate": round(passed_count / len(results), 3) if results else 1.0,
            "duration_ms": sum(int(result.get("duration_ms", 0)) for result in results),
        },
        "results": results,
    }


def write_workspace_session(workspace: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path = workspace / ".kodex-agent" / "workspace-session.json"
    normalized = {
        "schema_version": "1.0",
        "open_files": [
            str(item)
            for item in payload.get("open_files", [])
            if isinstance(item, str) and (workspace / item).resolve().is_relative_to(workspace)
        ][:50],
        "active_file": str(payload.get("active_file") or ""),
        "unfinished_task_ids": [int(item) for item in payload.get("unfinished_task_ids", [])][:100],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    return normalized


def read_workspace_session(workspace: Path) -> dict[str, Any]:
    path = workspace / ".kodex-agent" / "workspace-session.json"
    legacy_path = workspace / ".codex-agent" / "workspace-session.json"
    if not path.exists() and legacy_path.exists():
        path = legacy_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    existing = [
        item
        for item in payload.get("open_files", [])
        if isinstance(item, str) and (workspace / item).is_file()
    ]
    return {
        "schema_version": "1.0",
        "open_files": existing,
        "active_file": payload.get("active_file") if payload.get("active_file") in existing else (existing[0] if existing else ""),
        "unfinished_task_ids": payload.get("unfinished_task_ids", []),
    }
