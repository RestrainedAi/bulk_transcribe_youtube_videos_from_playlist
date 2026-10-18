"""Queue control shared by the watcher and the control panel.

The panel writes <watch_dir>/queue_control.json; the watcher reads it before starting each file.
  paused: bool       -> no new file starts (the running one finishes)
  order:  [paths]    -> preferred order; files not listed keep arrival order after the listed ones
  skip:   [paths]    -> remove these from the queue (the files stay where they are)
"""
from __future__ import annotations

import json
import os
import threading

CONTROL_NAME = "queue_control.json"
_lock = threading.Lock()


def control_path(watch_dir: str) -> str:
    return os.path.join(watch_dir, CONTROL_NAME)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def load(watch_dir: str) -> dict:
    try:
        with open(control_path(watch_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    return {
        "paused": bool(data.get("paused", False)),
        "order": [str(p) for p in data.get("order") or [] if p],
        "skip": [str(p) for p in data.get("skip") or [] if p],
    }


def save(watch_dir: str, data: dict) -> None:
    with _lock:
        path = control_path(watch_dir)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"paused": bool(data.get("paused")), "order": list(data.get("order") or []),
                       "skip": list(data.get("skip") or [])}, f, indent=1)
        os.replace(tmp, path)


def ordered(pending: list[str], order: list[str]) -> list[str]:
    """pending is in arrival order; listed paths go first in the listed order."""
    rank = {_norm(p): i for i, p in enumerate(order)}
    arrival = {p: i for i, p in enumerate(pending)}
    return sorted(pending, key=lambda p: (rank.get(_norm(p), len(rank)), arrival[p]))


def move(watch_dir: str, pending: list[str], path: str, delta: int) -> list[str]:
    """Move path up (delta<0) or down within the current effective order and persist it."""
    data = load(watch_dir)
    cur = ordered(pending, data["order"])
    keys = [_norm(p) for p in cur]
    if _norm(path) not in keys:
        return cur
    i = keys.index(_norm(path))
    j = max(0, min(len(cur) - 1, i + delta))
    cur.insert(j, cur.pop(i))
    data["order"] = cur
    save(watch_dir, data)
    return cur


def set_paused(watch_dir: str, paused: bool) -> None:
    data = load(watch_dir)
    data["paused"] = bool(paused)
    save(watch_dir, data)


def request_skip(watch_dir: str, path: str) -> None:
    data = load(watch_dir)
    if _norm(path) not in {_norm(p) for p in data["skip"]}:
        data["skip"].append(path)
    save(watch_dir, data)


def forget(watch_dir: str, path: str) -> None:
    """Drop a finished file from the saved order so the control file doesn't grow forever."""
    data = load(watch_dir)
    keep = [p for p in data["order"] if _norm(p) != _norm(path)]
    if len(keep) != len(data["order"]):
        data["order"] = keep
        save(watch_dir, data)


def take_skips(watch_dir: str, pending: list[str]) -> list[str]:
    """Return pending paths the user asked to skip, and clear those requests."""
    data = load(watch_dir)
    if not data["skip"]:
        return []
    want = {_norm(p) for p in data["skip"]}
    hits = [p for p in pending if _norm(p) in want]
    pend = {_norm(p) for p in pending}
    data["skip"] = [p for p in data["skip"] if _norm(p) not in pend]
    data["order"] = [p for p in data["order"] if _norm(p) not in {_norm(h) for h in hits}]
    save(watch_dir, data)
    return hits


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def job_progress(job: dict, now: float) -> tuple[float | None, float | None]:
    """(fraction 0..1, eta seconds) from a status active_jobs entry."""
    dur = float(job.get("duration") or 0)
    seg = float(job.get("last_segment_end") or 0)
    if dur <= 0:
        return None, None
    frac = max(0.0, min(1.0, seg / dur))
    started = float(job.get("started_at_epoch") or 0)
    elapsed = now - started if started else 0
    if seg <= 0 or elapsed <= 0:
        return frac, None
    return frac, max(0.0, (dur - seg) * elapsed / seg)
