"""'Possibly missed audio' check for finished transcripts.

Whisper's speech filter can drop sung or noisy stretches, and the next kept line then
gets stretched across the hole. Both show up as either a very long block or a long
gap between blocks (including before the first and after the last block).
"""
from __future__ import annotations

from typing import Iterable

DEFAULT_MAX_BLOCK_S = 30.0
DEFAULT_MAX_GAP_S = 60.0


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def coverage_warnings(segs: Iterable[dict], duration: float | None = None,
                      max_block: float = DEFAULT_MAX_BLOCK_S, max_gap: float = DEFAULT_MAX_GAP_S) -> list[dict]:
    """Return [{kind: 'gap'|'long_block', start, end, text?}] in time order."""
    out: list[dict] = []
    prev_end = 0.0
    items = sorted((s for s in segs if isinstance(s, dict)), key=lambda s: _f(s.get("start")))
    for s in items:
        start, end = _f(s.get("start")), _f(s.get("end"))
        if start - prev_end > max_gap:
            out.append({"kind": "gap", "start": round(prev_end, 2), "end": round(start, 2)})
        if end - start > max_block:
            out.append({"kind": "long_block", "start": round(start, 2), "end": round(end, 2),
                        "text": " ".join(str(s.get("text", "")).split())})
        prev_end = max(prev_end, end)
    d = _f(duration) if duration else 0.0
    if d and d - prev_end > max_gap:
        out.append({"kind": "gap", "start": round(prev_end, 2), "end": round(d, 2)})
    return out


def mmss(t: float) -> str:
    t = int(round(_f(t)))
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def describe(w: dict) -> str:
    return f"Possibly missed audio at {mmss(w.get('start'))}\u2013{mmss(w.get('end'))}"


def summary_lines(warnings: list[dict]) -> list[str]:
    lines = []
    for w in warnings:
        why = "long gap" if w.get("kind") == "gap" else "one line stretched over this span"
        lines.append(f"{describe(w)} ({why})")
    return lines


def mean_no_speech(segs: Iterable[dict]) -> float:
    vals = [_f(s.get("no_speech_prob")) for s in segs if isinstance(s, dict) and s.get("no_speech_prob") is not None]
    return sum(vals) / len(vals) if vals else 0.0


def warnings_from_json_data(data) -> list[dict]:
    """Read stored warnings from a transcript JSON (list of segments, or {segments, coverage_warnings})."""
    if isinstance(data, dict):
        w = data.get("coverage_warnings")
        return w if isinstance(w, list) else []
    if isinstance(data, list):
        for s in data:
            if isinstance(s, dict) and isinstance(s.get("coverage_warnings"), list):
                return s["coverage_warnings"]
    return []
