from __future__ import annotations

import os
import platform
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # Packaged builds include psutil; source checkouts degrade honestly.
    psutil = None  # type: ignore[assignment]


class SystemTelemetry:
    def __init__(self, workspace: Path, desktop_pid: int | None = None) -> None:
        self.workspace = workspace
        self.desktop_pid = desktop_pid
        self.history: deque[dict[str, Any]] = deque(maxlen=60)
        if psutil is not None:
            psutil.cpu_percent(interval=None)

    def sample(self) -> dict[str, Any]:
        disk = shutil.disk_usage(self.workspace)
        system_cpu = psutil.cpu_percent(interval=None) if psutil is not None else None
        memory = psutil.virtual_memory() if psutil is not None else None
        kodex_cpu = 0.0
        kodex_memory = 0
        process_count = 0

        if psutil is not None:
            process_ids = {os.getpid()}
            if self.desktop_pid:
                process_ids.add(self.desktop_pid)
            for process_id in tuple(process_ids):
                try:
                    process_ids.update(child.pid for child in psutil.Process(process_id).children(recursive=True))
                except (psutil.Error, OSError):
                    continue
            for process_id in process_ids:
                try:
                    process = psutil.Process(process_id)
                    kodex_cpu += process.cpu_percent(interval=None)
                    kodex_memory += process.memory_info().rss
                    process_count += 1
                except (psutil.Error, OSError):
                    continue

        sample = {
            "timestamp": time.time(),
            "system": {
                "cpuPercent": system_cpu,
                "memoryUsedBytes": memory.used if memory is not None else None,
                "memoryTotalBytes": memory.total if memory is not None else None,
                "diskUsedBytes": disk.used,
                "diskTotalBytes": disk.total,
                "gpuName": self._gpu_name(),
                "gpuPercent": None,
            },
            "kodex": {
                "cpuPercent": round(kodex_cpu, 2),
                "memoryBytes": kodex_memory,
                "processCount": process_count,
            },
        }
        self.history.append(sample)
        return {**sample, "history": list(self.history)}

    @staticmethod
    def _gpu_name() -> str | None:
        # Utilization is intentionally N/A unless a stable, unprivileged API is present.
        if platform.system() == "Darwin":
            return "Apple GPU" if platform.machine() == "arm64" else None
        return None
