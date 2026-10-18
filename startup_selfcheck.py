"""Startup self-check for the watcher control panel.

Exit code 0 = all good. Non-zero = problem; details printed and written to logs/selfcheck.log.
"""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

REQUIRED_PACKAGES = ["psutil", "faster_whisper", "watchdog", "tqdm"]


def run_checks() -> list[str]:
    problems: list[str] = []
    if sys.version_info[:2] != (3, 12):
        problems.append(f"Python 3.12 expected, found {sys.version.split()[0]} at {sys.executable}")
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        root.destroy()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"tkinter/Tcl is not usable: {exc}")
    if shutil.which("ffmpeg") is None:
        problems.append("ffmpeg was not found on PATH (install it, e.g. 'winget install Gyan.FFmpeg').")
    for name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Required package '{name}' failed to import: {exc}")
    return problems


def main() -> int:
    problems = run_checks()
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    text = "OK" if not problems else "\n".join(f"- {p}" for p in problems)
    (log_dir / "selfcheck.log").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
