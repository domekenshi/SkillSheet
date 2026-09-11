#!/usr/bin/env python3
"""Skill Gap PlannerをmacOSログイン中に常駐させるLaunchAgentを登録する。"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "local.skill-gap-planner"
URL = "http://127.0.0.1:8766"


def build_config(python: Path, server: Path, root: Path, log: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(server)],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "NO_BROWSER": "1",
            "SKILL_GAP_PORT": "8766",
            "PYTHONUNBUFFERED": "1",
            "PATH": f"{python.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def main() -> None:
    here = Path(__file__).resolve().parent
    root = here.parents[1]
    server = here / "server.py"
    python = Path(sys.executable).resolve()
    if not server.is_file():
        raise SystemExit(f"サーバーが見つかりません: {server}")

    domain = f"gui/{os.getuid()}"
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    logs_dir = Path.home() / "Library" / "Logs"
    agents_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    target = agents_dir / f"{LABEL}.plist"
    log = logs_dir / "skill-gap-planner.log"
    target.write_bytes(plistlib.dumps(build_config(python, server, root, log)))
    subprocess.run(["plutil", "-lint", str(target)], check=True)

    exists = subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], capture_output=True)
    if exists.returncode == 0:
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    print(f"常駐設定を登録しました: {URL}")
    print(f"ログ: {log}")


if __name__ == "__main__":
    main()
