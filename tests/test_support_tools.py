import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import support_tools as st
import transcript_review as tr
from watcher_settings import DEFAULT_SETTINGS


def test_every_setting_has_a_friendly_label():
    missing = [k for k in DEFAULT_SETTINGS if k not in st.CATALOG]
    assert missing == []
    assert {st.describe(k)[0] for k in DEFAULT_SETTINGS} <= set(st.SECTION_ORDER)


def test_coerce_and_redact():
    assert st.coerce("12", 10.0) == 12.0 and st.coerce("3", 1) == 3 and st.coerce(1, False) is True and st.coerce("x", "") == "x"
    r = st.redacted_settings({"openai_api_key": "sk-123", "google_api_key": "", "watch_dir": "w"})
    assert r == {"openai_api_key": "***set***", "google_api_key": "", "watch_dir": "w"}


def test_unified_log_merges_by_time(tmp_path, monkeypatch):
    logs = tmp_path / "logs"; logs.mkdir(); w = tmp_path / "w"; w.mkdir()
    monkeypatch.setattr(st, "LOG_DIR", logs); monkeypatch.setattr(st, "UNIFIED_LOG", logs / "unified.log")
    (logs / "launcher.log").write_text("[2026-10-14 10:00:05] Launch started\n", encoding="utf-8")
    (w / "watch_events.log").write_text("2026-10-14 10:00:01 [watch] watcher started\n2026-10-14 10:00:09 [watch] queued: a\n", encoding="utf-8")
    lines = st.build_unified_log(w).read_text(encoding="utf-8").splitlines()
    assert [l.split("] ", 1)[0].strip("[ ") for l in lines] == ["watcher", "launcher", "watcher"]


def test_support_report_hides_keys(tmp_path):
    rep = st.support_report(tmp_path, {"openai_api_key": "sk-secret", "watch_dir": "w"}, {"phase": "watching"}, "GPU 5%")
    assert "sk-secret" not in rep and "***set***" in rep and "GPU 5%" in rep and '"phase": "watching"' in rep


def test_snippet_vlc_exits_and_find_media(tmp_path, monkeypatch):
    assert tr.snippet_command("vlc", "vlc", "a.mp4", 5, 6)[1:3] == ["--no-one-instance", "--play-and-exit"]
    monkeypatch.setattr(tr.transcript_search, "lookup_video", lambda stem: None)
    d = tmp_path / "audio"; d.mkdir(); (d / "clip.m4a").write_bytes(b"1")
    assert tr.find_media("clip", [str(tmp_path / "nope"), str(d)]).name == "clip.m4a"
    assert tr.find_media("other", [str(d)]) is None
