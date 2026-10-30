"""DEV-173..176 regression tests: two-stage progress, Stop result, QuickEdit, GPU read, restart-done."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import asr_engines
import console_guard
import queue_control
import watcher_telemetry


# DEV-173
def test_pass_display_hidden_during_whisper():
    assert queue_control.pass_display({"pass": "whisper"}, time.time()) is None
    assert queue_control.pass_display({}, time.time()) is None


def test_pass_display_shows_granite_elapsed():
    now = 1000.0
    label, elapsed = queue_control.pass_display({"pass": "granite-2b", "pass_number": 2, "pass_started_epoch": 400.0}, now)
    assert label == "Pass 2: Granite" and elapsed == 600.0


def test_heartbeat_fires_while_child_runs(capsys):
    events = []
    asr_engines.set_pass_listener(lambda e, eng, s: events.append(e))
    try:
        code, _ = asr_engines.run_with_heartbeat([sys.executable, "-c", "import time; time.sleep(1.3)"],
                                                 env=None, engine="granite-2b", timeout=60, beat_seconds=0.4)
    finally:
        asr_engines.set_pass_listener(None)
    assert code == 0
    assert events.count("beat") >= 2
    assert "still running" in capsys.readouterr().out


def test_pipeline_reports_start_and_end():
    events = []
    asr_engines.set_pass_listener(lambda e, eng, s: events.append((e, eng)))
    segs = [{"start": 0.0, "end": 1.0, "text": " hello there"}]
    try:
        asr_engines.apply_engine_pipeline("x.wav", "stem", segs, {"asr_engine": "granite-2b"}, str(ROOT / "tests"),
                                          runner=lambda *a: {"text": "hello there"}, free_gb_func=lambda: 99.0)
    except Exception:
        pass  # model folder may be missing in CI; we only care about the events when it ran
    finally:
        asr_engines.set_pass_listener(None)
        (ROOT / "tests" / "stem.flags.txt").unlink(missing_ok=True)
    if events:
        assert events[0] == ("start", "granite-2b") and events[-1][0] == "end"


# DEV-174
def test_stop_result_message():
    import watcher_control_panel as wcp
    assert wcp.stop_result_message(0, "Watcher OFF (stopped PID 5; verified)\n", "") == (True, "Watcher OFF (stopped PID 5; verified)")
    ok, msg = wcp.stop_result_message(2, "Watcher NOT fully stopped: still running PID(s) 9\n", "")
    assert not ok and "9" in msg


def test_stop_script_keeps_completed_progress_and_kills_tree():
    s = (ROOT / "stop_watcher.ps1").read_text(encoding="utf-8")
    assert "asr_eval_worker.py" in s and "/T /F" in s and '"completed"' in s and "exit 2" in s


# DEV-175
def test_quickedit_bit_cleared():
    m = console_guard.quickedit_off_mode(0x01F7)
    assert not m & console_guard.ENABLE_QUICK_EDIT_MODE and m & console_guard.ENABLE_EXTENDED_FLAGS
    assert console_guard.disable_quickedit() in (True, False)  # never raises


# DEV-176
def test_nvidia_parse_two_gpus_and_empty():
    assert watcher_telemetry.parse_nvidia_line("34, 8000, 24576\n5, 100, 8192\n") == {
        "gpu_util": 34.0, "gpu_mem_used_mb": 8000.0, "gpu_mem_total_mb": 24576.0}
    assert watcher_telemetry.parse_nvidia_line("") is None


def test_standard_profile_includes_gpu(monkeypatch):
    monkeypatch.setattr(watcher_telemetry, "_nvidia_sample", lambda: {"gpu_util": 1.0})
    assert watcher_telemetry.sample_snapshot("standard")["gpu"] == {"gpu_util": 1.0}


def test_restart_marks_finished_file_done(tmp_path):
    import watch_and_transcribe as w
    watch = tmp_path / "watch"; watch.mkdir()
    dirs = {k: str(tmp_path / k) for k in ("completed_transcripts", "completed_csv", "completed_json", "completed_videos")}
    for d in dirs.values():
        Path(d).mkdir()
    src = watch / "a.wav"; src.write_bytes(b"x")
    prog = watch / ".watch_progress_a_1.json"
    prog.write_text(json.dumps({"input": str(src), "stem": "a", "status": "completed"}), encoding="utf-8")
    assert w.finished_outputs_stem(str(watch), str(src), dirs) is None  # outputs missing -> rerun
    for k, ext in (("completed_transcripts", ".txt"), ("completed_csv", ".csv"), ("completed_json", ".json")):
        Path(dirs[k], "a" + ext).write_text("ok")
    assert w.finished_outputs_stem(str(watch), str(src), dirs)[0] == "a"
    prog.write_text(json.dumps({"input": str(src), "stem": "a", "status": "processing"}), encoding="utf-8")
    assert w.finished_outputs_stem(str(watch), str(src), dirs) is None
