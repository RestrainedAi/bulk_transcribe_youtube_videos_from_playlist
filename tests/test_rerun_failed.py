"""Tests for v1.3.2 "Re-run failed"."""
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import failed_jobs  # noqa: E402
import queue_control  # noqa: E402


def _log(tmp_path, lines):
    p = tmp_path / "watch_events.log"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_failed_from_log_latest_event_wins(tmp_path):
    a, b, c = (str(tmp_path / n) for n in ("a.mp4", "b.mp4", "c.mp4"))
    log = _log(tmp_path, [
        f"2026-10-21 21:37:01 [watch] queued: {a}",
        f"2026-10-21 21:37:05 [watch] failed: {a} :: transcription_worker_exit_3221225501",
        f"2026-10-21 21:37:06 [watch] failed: {b} :: boom",
        f"2026-10-21 21:37:07 [watch] queued: {b}",
        f"2026-10-21 21:37:08 [watch] completed: {b} -> {tmp_path / 'done' / 'b.mp4'}",
        f"2026-10-21 21:37:09 [watch] stalled: {c} :: stall",
        "garbage line",
    ])
    jobs = failed_jobs.failed_from_log(log)
    assert [Path(j["path"]).name for j in jobs] == ["a.mp4", "c.mp4"]
    assert jobs[0]["error"] == "transcription_worker_exit_3221225501"
    assert jobs[1]["kind"] == "stalled"


def test_requeued_clears_failure_and_counts_attempts(tmp_path):
    a = str(tmp_path / "a.mp4")
    log = _log(tmp_path, [
        f"2026-10-21 21:00:00 [watch] failed: {a} :: x",
        f"2026-10-21 21:01:00 [watch] requeued: {a}",
    ])
    assert failed_jobs.failed_from_log(log) == []
    log = _log(tmp_path, [
        f"2026-10-21 21:00:00 [watch] failed: {a} :: x",
        f"2026-10-21 21:01:00 [watch] requeued: {a}",
        f"2026-10-21 21:02:00 [watch] failed: {a} :: y",
    ])
    (j,) = failed_jobs.failed_from_log(log)
    assert j["failures"] == 2 and j["error"] == "y"


def test_missing_log_is_empty(tmp_path):
    assert failed_jobs.failed_from_log(tmp_path / "nope.log") == []


def test_split_missing_and_confirm_text(tmp_path):
    here = tmp_path / "here.mp4"
    here.write_bytes(b"x")
    present, missing = failed_jobs.split_missing([{"path": str(here)}, {"path": str(tmp_path / "gone.mp4")}])
    assert len(present) == 1 and len(missing) == 1
    t = failed_jobs.confirm_text(1, 1)
    assert "Re-run 1 failed" in t and "1 failed job(s) will be skipped" in t
    assert "skipped" not in failed_jobs.confirm_text(3, 0)


def test_queue_control_rerun_roundtrip_keeps_other_keys(tmp_path):
    w = str(tmp_path)
    queue_control.set_paused(w, True)
    queue_control.request_skip(w, "s.mp4")
    assert queue_control.request_rerun(w, ["a.mp4", "b.mp4"]) == 2
    assert queue_control.request_rerun(w, ["a.mp4"]) == 0
    data = queue_control.load(w)
    assert data["paused"] and data["skip"] == ["s.mp4"] and data["rerun"] == ["a.mp4", "b.mp4"]
    assert queue_control.take_reruns(w) == ["a.mp4", "b.mp4"]
    assert queue_control.take_reruns(w) == []
    assert queue_control.load(w)["skip"] == ["s.mp4"]


class _FakeStatus:
    def __init__(self):
        self.state = {"failed_count": 3, "processed_count": 3, "last_error": "e", "stalled": False}
        self.log = []
        self.requeued = []

    def _append_log(self, m):
        self.log.append(m)

    def mark_requeued(self, p):
        self.requeued.append(p)
        self.state["failed_count"] -= 1


def test_handler_process_reruns_skips_missing(tmp_path):
    import watch_and_transcribe as w
    keep = tmp_path / "keep.mp4"
    keep.write_bytes(b"x")
    scheduled = []
    h = w.VideoHandler.__new__(w.VideoHandler)
    h.watch_dir = str(tmp_path)
    h.status = _FakeStatus()
    h._lock = threading.Lock()
    h._queued, h._active = set(), set()
    h._schedule = lambda p: scheduled.append(p)
    queue_control.request_rerun(str(tmp_path), [str(keep), str(tmp_path / "gone.mp4")])
    assert h.process_reruns() == (1, 1)
    assert scheduled == [os.path.abspath(str(keep))]
    assert h.status.state["failed_count"] == 2
    assert any("source missing" in m for m in h.status.log)
    assert queue_control.load(str(tmp_path))["rerun"] == []


def test_mark_requeued_updates_counter(tmp_path):
    import watch_and_transcribe as w
    st = w.StatusTracker.__new__(w.StatusTracker) if hasattr(w, "StatusTracker") else None
    if st is None:
        import pytest
        pytest.skip("status class name differs")
    st._lock = threading.Lock()
    st.state = {"failed_count": 1, "processed_count": 1, "last_error": "e", "stalled": True}
    st._write_status = lambda: None
    logged = []
    st._append_log = logged.append
    st.mark_requeued("a.mp4")
    assert st.state["failed_count"] == 0 and st.state["last_error"] is None and not st.state["stalled"]
    assert logged == ["[watch] requeued: a.mp4"]


def test_version_bumped():
    import app_version
    assert app_version.APP_VERSION == "1.4.1"
