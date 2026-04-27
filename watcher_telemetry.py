"""Resource sampling for watcher (standard / tuning / debug profiles)."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

import psutil


def _nvidia_sample() -> dict[str, Any] | None:
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            shell=False,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        parts = [p.strip() for p in r.stdout.strip().split(",")]
        if len(parts) >= 3:
            return {
                "gpu_util": float(parts[0]) if parts[0] else None,
                "gpu_mem_used_mb": float(parts[1]) if parts[1] else None,
                "gpu_mem_total_mb": float(parts[2]) if parts[2] else None,
            }
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
        return None
    return None


def sample_snapshot(log_profile: str) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "mem_percent": psutil.virtual_memory().percent,
        "mem_used_mb": round(psutil.virtual_memory().used / (1024 * 1024), 1),
    }
    if log_profile in ("tuning", "debug"):
        nv = _nvidia_sample()
        if nv:
            snap["gpu"] = nv
    if log_profile == "debug":
        snap["python_threads"] = threading.active_count()
    return snap


class TelemetryWriter(threading.Thread):
    """Background thread: append JSON lines while jobs are active."""

    def __init__(
        self,
        resource_jsonl: Path,
        repo_root: Path,
        is_busy: Callable[[], bool],
        interval_seconds: float = 5.0,
    ) -> None:
        super().__init__(daemon=True)
        self.resource_jsonl = resource_jsonl
        self.repo_root = repo_root
        self.is_busy = is_busy
        self.interval_seconds = max(1.0, interval_seconds)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _read_profile(self) -> str:
        try:
            from watcher_settings import load_settings  # noqa: PLC0415

            return str(load_settings(self.repo_root).get("log_profile", "standard"))
        except Exception:
            return "standard"

    def run(self) -> None:
        while True:
            profile = self._read_profile()
            interval = 1.0 if profile == "debug" else self.interval_seconds
            if self._stop.wait(interval):
                break
            if profile == "standard":
                continue
            if not self.is_busy():
                continue
            snap = sample_snapshot(profile)
            line = json.dumps(snap, ensure_ascii=False)
            try:
                with open(self.resource_jsonl, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
            try:
                if (
                    self.resource_jsonl.is_file()
                    and self.resource_jsonl.stat().st_size > 5_000_000
                ):
                    self._trim_jsonl()
            except OSError:
                pass

    def _trim_jsonl(self) -> None:
        try:
            lines = self.resource_jsonl.read_text(encoding="utf-8", errors="replace").splitlines()
            keep = lines[-2000:]
            self.resource_jsonl.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass
