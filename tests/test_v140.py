"""v1.4.0: shared library + migration, needs-review, audio modes, watcher auto-off, status badge."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import data_library as dl
import failed_jobs
import queue_control
from watcher_settings import DEFAULT_SETTINGS, load_settings, save_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def handoff(tmp_path, monkeypatch):
    import aeonimus_handoff as ah
    cfg = tmp_path / "appdata" / "Aeonimus" / "handoff.json"
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv(ah.CONFIG_ENV, str(cfg))
    lib = tmp_path / "Library"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"inbox": str(tmp_path / "inbox"), "library": str(lib), "version": 1}), encoding="utf-8")
    return lib


def _h(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_version_is_140() -> None:
    import app_version
    assert tuple(int(x) for x in app_version.APP_VERSION.split(".")) >= (1, 4, 0)


def test_new_settings_defaults(tmp_path) -> None:
    s = load_settings(tmp_path)
    assert s["keep_watching_after_run"] is False
    assert s["auto_needs_review"] is True
    assert s["shared_library_dir"] == "" and s["needs_review_dir"] == ""
    save_settings(tmp_path, {"keep_watching_after_run": True})
    assert load_settings(tmp_path)["keep_watching_after_run"] is True


def test_library_comes_from_shared_handoff_config(handoff) -> None:
    assert dl.library_root({}) == handoff
    d = dl.library_dirs(handoff)
    assert d["watch_dir"] == handoff / "transcription" / "incoming"
    assert d["needs_review_dir"] == handoff / "transcription" / "needs-review"
    assert dl.needs_review_dir(Path("."), {}) == d["needs_review_dir"]
    # the Comment Workbench's shared folders are untouched by the layout
    assert all("transcription" == p.relative_to(handoff).parts[0] for p in d.values())


def test_library_override_setting_updates_handoff(handoff, tmp_path) -> None:
    import aeonimus_handoff as ah
    other = tmp_path / "Other Library"
    save_settings(tmp_path / "repo", {"shared_library_dir": str(other)})
    assert ah.load_config().library == other


def _legacy_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "watch_incoming" / "sub").mkdir(parents=True)
    (repo / "watch_incoming" / "a.mp4").write_bytes(b"video-a" * 100)
    (repo / "watch_incoming" / "sub" / "b.mp4").write_bytes(b"video-b")
    (repo / "watch_incoming" / ".watcher.lock").write_text("123")
    (repo / "watch_incoming" / "watch_events.log").write_text("log\n")
    (repo / "completed_transcripts").mkdir()
    (repo / "completed_transcripts" / "t [abcdefghijk].txt").write_text("hello", encoding="utf-8")
    (repo / "completed_json").mkdir()
    (repo / "completed_json" / "t [abcdefghijk].json").write_text("{}", encoding="utf-8")
    return repo


def test_migration_dry_run_writes_nothing(handoff, tmp_path) -> None:
    repo = _legacy_repo(tmp_path)
    lines: list[str] = []
    plan = dl.plan_migration(repo, load_settings(repo), handoff)
    stats = dl.run_migration(repo, plan, dry_run=True, log=lines.append)
    assert stats["copied"] == 0
    assert not handoff.exists()
    assert not (repo / "watcher_settings.json").exists()
    assert any("DRY RUN" in ln for ln in lines)
    w = next(i for i in plan.items if i.key == "watch_dir")
    assert w.files == 3  # lock file skipped


def test_migration_apply_copies_with_hashes_and_keeps_originals(handoff, tmp_path) -> None:
    repo = _legacy_repo(tmp_path)
    before = {p: _h(p) for p in repo.rglob("*") if p.is_file()}
    plan = dl.plan_migration(repo, load_settings(repo), handoff)
    stats = dl.run_migration(repo, plan, dry_run=False, log=lambda *_: None)
    assert stats["failed"] == 0 and stats["copied"] == 5
    for p, h in before.items():  # originals untouched
        if p.name != "watcher_settings.json":
            assert p.is_file() and _h(p) == h
    inc = handoff / "transcription" / "incoming"
    assert _h(inc / "a.mp4") == _h(repo / "watch_incoming" / "a.mp4")
    assert (inc / "sub" / "b.mp4").is_file()
    assert not (inc / ".watcher.lock").exists()
    s = load_settings(repo)
    assert s["watch_dir"] == str(inc)
    assert s["completed_transcript_dir"] == str(handoff / "transcription" / "transcripts")
    # running it again is a no-op (identical files skipped)
    again = dl.run_migration(repo, dl.plan_migration(repo, load_settings(repo), handoff), dry_run=False, log=lambda *_: None)
    assert again["copied"] == 0


def test_migration_never_overwrites_a_different_file(handoff, tmp_path) -> None:
    repo = _legacy_repo(tmp_path)
    inc = handoff / "transcription" / "incoming"
    inc.mkdir(parents=True)
    (inc / "a.mp4").write_bytes(b"someone else's file")
    stats = dl.run_migration(repo, dl.plan_migration(repo, load_settings(repo), handoff), dry_run=False, log=lambda *_: None)
    assert stats["renamed"] == 1
    assert (inc / "a.mp4").read_bytes() == b"someone else's file"
    assert (inc / "a (2).mp4").read_bytes() == (repo / "watch_incoming" / "a.mp4").read_bytes()


def test_migration_cli_defaults_to_dry_run(handoff, tmp_path, capsys) -> None:
    import migrate_to_library
    repo = _legacy_repo(tmp_path)
    assert migrate_to_library.main(["--repo", str(repo)]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert not handoff.exists()


def test_verified_move_and_sidecar(tmp_path) -> None:
    src = tmp_path / "in" / "x.mp4"
    src.parent.mkdir()
    src.write_bytes(b"abc" * 1000)
    h = _h(src)
    folder = tmp_path / "needs-review"
    dest = dl.move_to_needs_review(src, "RuntimeError: transcription_returned_no_segments", folder, audio_mode="speech")
    assert not src.exists() and _h(dest) == h
    side = dest.with_name(dest.name + dl.SIDE_CAR_SUFFIX).read_text(encoding="utf-8")
    assert "No speech was found" in side and "transcription_returned_no_segments" in side
    assert dl.review_items(folder) == [dest]


def test_verified_copy_hash_mismatch_keeps_source(tmp_path, monkeypatch) -> None:
    src = tmp_path / "a.bin"
    src.write_bytes(b"1234")
    calls = iter(["good", "bad"])
    monkeypatch.setattr(dl, "sha256", lambda _p: next(calls))
    with pytest.raises(OSError):
        dl.verified_move(src, tmp_path / "out")
    assert src.is_file()
    assert not list((tmp_path / "out").glob("*"))


@pytest.mark.parametrize("err,kind", [
    ("RuntimeError: transcription_returned_no_segments", "content"),
    ("ValueError: No audio stream found", "content"),
    ("transcription_worker_exit_3221226505", "crash"),
    ("transcription_stalled: no progress heartbeat for 900s", "crash"),
    ("RuntimeError: all_whisper_attempts_failed:RuntimeError:CUDA failed", "crash"),
    ("something odd", "other"),
])
def test_classify_error(err, kind) -> None:
    assert dl.classify_error(err) == kind


def test_status_badge_texts_and_clearing() -> None:
    assert dl.status_badge(running=True, busy=False, crash_failures=0, review_count=0).text == "All good"
    assert dl.status_badge(running=True, busy=True, crash_failures=0, review_count=0).text == "Working"
    b = dl.status_badge(running=True, busy=False, crash_failures=0, review_count=2)
    assert b.text == "2 files need review: no speech found" and b.action == "needs_review" and b.tooltip
    c = dl.status_badge(running=True, busy=False, crash_failures=1, review_count=2)
    assert c.text.startswith("Engine crashed") and c.action == "log"  # crashes outrank content issues
    assert dl.status_badge(running=False, busy=False, crash_failures=0, review_count=0).level == "off"
    assert "FAULT" not in " ".join(dl.status_badge(running=r, busy=False, crash_failures=n, review_count=n).text
                                   for r in (True, False) for n in (0, 1, 3))


def test_audio_modes_round_trip(tmp_path) -> None:
    w = str(tmp_path)
    f = str(tmp_path / "Song.MP4")
    assert queue_control.mode_for(w, f) == "speech"
    queue_control.set_mode(w, f, "Speech over music")
    assert queue_control.mode_for(w, f.lower() if __import__("os").name == "nt" else f) == "speech_music"
    queue_control.set_mode(w, f, "music")
    assert queue_control.mode_for(w, f) == "music"
    queue_control.request_rerun(w, [f])  # other keys survive
    assert queue_control.mode_for(w, f) == "music"
    queue_control.forget_mode(w, f)
    assert queue_control.mode_for(w, f) == "speech"
    assert queue_control.normalize_mode("bogus") == "speech"


def test_speech_over_music_decoding_options() -> None:
    import bulk_transcribe_youtube_videos_from_playlist as bt
    k = bt.transcribe_kwargs_for_profile("speech_music", True, None)
    assert k["condition_on_previous_text"] is False
    assert k["vad_filter"] is True and k["vad_parameters"]["threshold"] < 0.5
    assert k["no_speech_threshold"] > 0.6 and k["hallucination_silence_threshold"] is None
    off = bt.transcribe_kwargs_for_profile("speech_music", False, None)
    assert off["vad_filter"] is False and "vad_parameters" not in off
    assert "speech_music" in bt.ALLOWED_DECODE_PROFILES
    labels = [a[4] for a in bt.build_whisper_attempts(True, "large-v3", "medium", "speech_music")]
    assert labels[0] == "gpu_fp16"
    # Speech (default) unchanged
    assert bt.transcribe_kwargs_for_profile("robust", True, "en")["no_speech_threshold"] == 0.6


def test_vocal_separation_is_optional(monkeypatch) -> None:
    import vocal_separation as vs
    monkeypatch.setattr(vs, "available", lambda: False)
    assert vs.separate_vocals("nope.mp4") is None


def test_needs_review_event_removes_job_from_failed_list(tmp_path) -> None:
    log = tmp_path / "watch_events.log"
    p = str(tmp_path / "a.mp4")
    log.write_text(
        f"2026-10-21 20:00:00 [watch] failed: {p} :: RuntimeError: transcription_returned_no_segments\n"
        f"2026-10-21 20:00:01 [watch] needs_review: {p} -> D:\\x\\a.mp4 :: RuntimeError: transcription_returned_no_segments\n",
        encoding="utf-8")
    assert failed_jobs.failed_from_log(log) == []
    groups = failed_jobs.classify([{"path": p, "kind": "failed", "error": "transcription_worker_exit_1"},
                                   {"path": p, "kind": "stalled", "error": "x"},
                                   {"path": p, "kind": "failed", "error": "transcription_returned_no_segments"}])
    assert len(groups["crash"]) == 2 and len(groups["content"]) == 1


class _Status:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def mark_failed(self, src, err):
        self.events.append(("failed", src, err))

    def mark_needs_review(self, src, dest, err):
        self.events.append(("review", src, dest, err))


def test_fail_or_review_moves_content_failures_only(tmp_path, monkeypatch) -> None:
    import watch_and_transcribe as wt
    review = tmp_path / "review"
    monkeypatch.setattr(wt, "load_settings", lambda _r: {**DEFAULT_SETTINGS, "needs_review_dir": str(review)})
    src = tmp_path / "w" / "quiet.mp4"
    src.parent.mkdir()
    src.write_bytes(b"x" * 10)
    st = _Status()
    wt.fail_or_review(str(src), "RuntimeError: transcription_returned_no_segments", st, str(src.parent))
    assert st.events[0][0] == "review" and (review / "quiet.mp4").is_file() and not src.exists()
    crash = tmp_path / "w" / "crash.mp4"
    crash.write_bytes(b"y")
    wt.fail_or_review(str(crash), "transcription_worker_exit_3221226505", st, str(crash.parent))
    assert st.events[1][0] == "failed" and crash.is_file()


class _Handler:
    def __init__(self, finished, idle) -> None:
        self.jobs_finished, self._idle = finished, idle

    def is_idle(self) -> bool:
        return self._idle


@pytest.mark.parametrize("keep,finished,idle,elapsed,want", [
    (False, 1, True, 30, True),
    (True, 1, True, 30, False),
    (False, 0, True, 30, False),   # nothing ran yet: stay on
    (False, 1, False, 30, False),  # still busy
    (False, 1, True, 2, False),    # not idle long enough
])
def test_should_auto_off(tmp_path, monkeypatch, keep, finished, idle, elapsed, want) -> None:
    import watch_and_transcribe as wt
    save_settings(tmp_path, {"keep_watching_after_run": keep})
    assert wt.should_auto_off(_Handler(finished, idle), tmp_path, 100.0, now=100.0 + elapsed) is want


def test_panel_source_has_no_mojibake() -> None:
    for name in ("watcher_control_panel.py", "watch_and_transcribe.py", "youtube_transcript_panel.py"):
        text = (ROOT / name).read_text(encoding="utf-8-sig")
        for bad in ("\u00e2\u20ac", "\u00c2\u00b7", "\u00c3"):
            assert bad not in text, (name, bad)
    assert "LOAD:" not in (ROOT / "watcher_control_panel.py").read_text(encoding="utf-8-sig")


def test_audio_modes_doc_has_license_notes() -> None:
    doc = (ROOT / "docs" / "AUDIO_MODES.md").read_text(encoding="utf-8")
    assert "MIT" in doc and "Demucs" in doc and "GPL" in doc
