"""GPU and disk health for the watcher: a one-line summary plus a low-disk guard."""
from __future__ import annotations

import os
import shutil
import subprocess

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def gpu_snapshot() -> dict | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW,
        ).stdout.strip().splitlines()
        name, util, used, total, temp = [x.strip() for x in out[0].split(",")]
        return {"name": name, "util": int(util), "mem_used_mb": int(used), "mem_total_mb": int(total), "temp_c": int(temp)}
    except Exception:
        return None


def drive_of(path: str) -> str:
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    return (drive.upper() + "\\") if drive else os.path.abspath(os.sep)


def disk_snapshot(paths) -> list[dict]:
    seen: dict[str, dict] = {}
    for p in paths:
        d = drive_of(p)
        if d in seen:
            continue
        try:
            u = shutil.disk_usage(d)
            seen[d] = {"drive": d, "free_gb": round(u.free / 1024**3, 1), "total_gb": round(u.total / 1024**3, 1)}
        except OSError:
            pass
    return list(seen.values())


def low_disk(disks: list[dict], min_free_gb: float) -> list[dict]:
    return [d for d in disks if d["free_gb"] < min_free_gb]


def warn_disk(disks: list[dict], min_free_gb: float) -> list[dict]:
    """Drives that are above the pause threshold but within 2x of it."""
    return [d for d in disks if min_free_gb <= d["free_gb"] < 2 * min_free_gb]


def health_line(gpu: dict | None, disks: list[dict], min_free_gb: float) -> str:
    if gpu:
        g = (f"GPU {gpu['util']}% | VRAM {gpu['mem_used_mb'] / 1024:.1f}/{gpu['mem_total_mb'] / 1024:.0f} GB"
             f" | {gpu['temp_c']}C")
    else:
        g = "GPU n/a"
    parts = []
    for d in disks:
        flag = " LOW" if d["free_gb"] < min_free_gb else (" warn" if d["free_gb"] < 2 * min_free_gb else "")
        parts.append(f"{d['drive'][:2]} {d['free_gb']:.0f} GB free{flag}")
    return g + (" | " + ", ".join(parts) if parts else "")
