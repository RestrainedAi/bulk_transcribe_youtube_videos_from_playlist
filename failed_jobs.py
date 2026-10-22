"""Failed-job tracking for "Re-run failed" (v1.3.2).

Failed jobs are derived from watch_events.log: a path whose most recent event is
"failed:" or "stalled:" is a failed job. Nothing is deleted; re-running just asks
the watcher (via queue_control.json "rerun") to queue the file again.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_EVENT = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[watch\] "
    r"(?P<kind>queued|processing|completed|failed|stalled|skipped|requeued|needs_review|resuming interrupted file): "
    r"(?P<rest>.*)$"
)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def failed_from_log(log_path: str | os.PathLike) -> list[dict]:
    """[{path, error, at, kind, failures}] for jobs whose latest event is failed/stalled, oldest first."""
    latest: dict[str, dict] = {}
    counts: dict[str, int] = {}
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        m = _EVENT.match(line.strip())
        if not m:
            continue
        kind, rest = m.group("kind"), m.group("rest")
        if kind in ("failed", "stalled"):
            path, _sep, error = rest.partition(" :: ")
        elif kind in ("completed", "needs_review"):
            path, error = rest.partition(" -> ")[0], ""
        else:
            path, error = rest, ""
        path = path.strip()
        if not path:
            continue
        key = _norm(path)
        if kind in ("failed", "stalled"):
            counts[key] = counts.get(key, 0) + 1
        latest.pop(key, None)  # keep dict order = order of latest event
        latest[key] = {"path": path, "kind": kind, "error": error.strip(), "at": m.group("ts")}
    out = []
    for key, ev in latest.items():
        if ev["kind"] in ("failed", "stalled"):
            out.append({**ev, "failures": counts.get(key, 1)})
    return out


def split_missing(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """(jobs whose source file still exists, jobs whose source file is gone)."""
    present, missing = [], []
    for j in jobs:
        (present if os.path.isfile(j["path"]) else missing).append(j)
    return present, missing


def confirm_text(present: int, missing: int) -> str:
    msg = f"Re-run {present} failed job(s)?\n\nThey go back in the queue with their error cleared. Logs, transcripts and files are kept."
    if missing:
        msg += f"\n\n{missing} failed job(s) will be skipped because the source file is missing."
    return msg


def classify(jobs: list[dict]) -> dict[str, list[dict]]:
    """Split failed jobs into crash / content / other (v1.4.0 status badge)."""
    import data_library
    out: dict[str, list[dict]] = {"crash": [], "content": [], "other": []}
    for j in jobs:
        kind = "crash" if j.get("kind") == "stalled" else data_library.classify_error(j.get("error", ""))
        out[kind].append(j)
    return out
