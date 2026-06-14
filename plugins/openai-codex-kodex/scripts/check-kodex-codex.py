#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import urllib.request


def main() -> int:
    login = subprocess.run(
        ["codex", "login", "status"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    print((login.stdout or login.stderr).strip())
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:7799/v1/providers/codex/status",
            timeout=3,
        ) as response:
            print(json.dumps(json.load(response), indent=2))
    except Exception as error:
        print(f"Kodex Core status unavailable: {error}")
    return 0 if login.returncode == 0 else login.returncode


if __name__ == "__main__":
    raise SystemExit(main())
