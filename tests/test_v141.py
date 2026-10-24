"""v1.4.1: big run light, needs-review list, collapsible Details."""
from __future__ import annotations

from pathlib import Path

import data_library as dl
from watcher_settings import DEFAULT_SETTINGS

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "watcher_control_panel.py").read_text(encoding="utf-8")


def test_version_is_141() -> None:
    import app_version
    assert tuple(int(x) for x in app_version.APP_VERSION.split(".")) >= (1, 4, 1)


def test_running_light_shows_file_and_n_of_m() -> None:
    L = dl.run_light(running=True, busy=True, current_file=r"C:\in\talk.mp4", done=2, total=8)
    assert L.level == "running"
    assert "3 of 8" in L.title and "talk.mp4" in L.detail


def test_old_failures_never_red_during_run() -> None:
    L = dl.run_light(running=True, busy=True, current_file="a.mp4", done=0, total=1, review_count=5)
    assert L.level == "running"
    L = dl.run_light(running=True, busy=False, review_count=5)
    assert L.level != "error"


def test_idle_review_and_error_levels() -> None:
    assert dl.run_light(running=False).level == "idle"
    assert dl.run_light(running=True).level == "idle"
    r = dl.run_light(running=False, review_count=2)
    assert r.level == "review" and "2 files need review" in r.title
    assert dl.run_light(running=False, review_count=1).title.endswith("1 file needs review")
    e = dl.run_light(running=False, error="The disk is full.")
    assert e.level == "error" and "disk is full" in e.detail and "Start watcher" in e.detail
    assert dl.run_light(running=True, stale=True).level == "error"
    assert dl.run_light(running=True, worker_gone=True, busy=True).level == "error"


def test_plain_words_no_fault() -> None:
    for kw in ({"running": False}, {"running": True, "busy": True}, {"running": False, "review_count": 3},
               {"running": False, "error": "x."}, {"running": True, "stale": True}):
        assert "FAULT" not in dl.run_light(**kw).title.upper()


def test_split_previous_failures() -> None:
    jobs = [{"path": "a", "at": "2026-10-21 23:00:00"}, {"path": "b", "at": "2026-10-22 00:30:00"}]
    prev, cur = dl.split_previous_failures(jobs, "2026-10-22 00:00:00")
    assert [j["path"] for j in prev] == ["a"] and [j["path"] for j in cur] == ["b"]
    assert dl.split_previous_failures(jobs, "")[0] == jobs


def test_files_finished_since(tmp_path) -> None:
    log = tmp_path / "watch_events.log"
    log.write_text(
        "2026-10-21 10:00:00 [watch] completed: old.mp4 -> x\n"
        "2026-10-22 00:10:00 [watch] processing: a.mp4\n"
        "2026-10-22 00:11:00 [watch] completed: a.mp4 -> x\n"
        "2026-10-22 00:12:00 [watch] failed: b.mp4 :: no_speech\n"
        "2026-10-22 00:13:00 [watch] needs_review: c.mp4 -> y\n", encoding="utf-8")
    assert dl.files_finished_since(log, "2026-10-22 00:00:00") == 3
    assert dl.files_finished_since(log, "") == 0
    assert dl.files_finished_since(tmp_path / "missing.log", "2026-10-22 00:00:00") == 0


def test_utc_to_local() -> None:
    assert len(dl.utc_iso_to_local("2026-10-22T05:00:00Z")) == 19
    assert dl.utc_iso_to_local("") == "" and dl.utc_iso_to_local("junk") == ""


def test_details_setting_default_closed() -> None:
    assert DEFAULT_SETTINGS["dashboard_details_open"] is False


def test_panel_wiring() -> None:
    assert "_build_run_light" in PANEL and "_build_review_card" in PANEL
    assert "_toggle_details" in PANEL and "dashboard_details_open" in PANEL
    assert "self._request_rerun(list(self._review_failed))" in PANEL  # reuses re-run-failed logic
    assert '"FAULT"' not in PANEL
    assert "show_banner = False" in PANEL
