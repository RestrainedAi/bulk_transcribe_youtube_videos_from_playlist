import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import system_health as sh
import watch_and_transcribe as wt


def test_health_line_flags_low_and_warn():
    disks = [{"drive": "I:\\", "free_gb": 5.0, "total_gb": 100}, {"drive": "E:\\", "free_gb": 15.0, "total_gb": 100},
             {"drive": "C:\\", "free_gb": 50.0, "total_gb": 100}]
    gpu = {"name": "RTX 3090", "util": 40, "mem_used_mb": 12288, "mem_total_mb": 24576, "temp_c": 60}
    line = sh.health_line(gpu, disks, 10)
    assert "GPU 40%" in line and "12.0/24 GB" in line and "I: 5 GB free LOW" in line and "E: 15 GB free warn" in line
    assert "C: 50 GB free" in line and "C: 50 GB free warn" not in line
    assert [d["drive"] for d in sh.low_disk(disks, 10)] == ["I:\\"]
    assert [d["drive"] for d in sh.warn_disk(disks, 10)] == ["E:\\"]
    assert sh.health_line(None, [], 10) == "GPU n/a"


def test_disk_snapshot_dedupes_drives(tmp_path):
    d = sh.disk_snapshot([str(tmp_path), str(tmp_path / "x"), str(tmp_path)])
    assert len(d) == 1 and d[0]["free_gb"] > 0


def test_interrupted_files_from_status(tmp_path):
    watch = tmp_path / "watch"; comp = watch / "Completed"; comp.mkdir(parents=True)
    a = watch / "a.mp4"; b = watch / "b.mp4"; gone = watch / "gone.mp4"; done = comp / "d.mp4"; txt = watch / "n.txt"
    for f in (a, b, done, txt):
        f.write_bytes(b"x")
    st = tmp_path / "status.json"
    st.write_text(json.dumps({"pending_files": [str(a), str(gone), str(txt)],
                              "active_jobs": [{"path": str(b)}, {"path": str(a)}, {"path": str(done)}]}))
    assert wt.interrupted_files_from_status(str(st), str(watch), str(comp)) == [str(a), str(b)]
    assert wt.interrupted_files_from_status(str(tmp_path / "missing.json"), str(watch), str(comp)) == []


def test_wait_for_disk_space_pauses_then_resumes(monkeypatch, tmp_path):
    calls = {"n": 0}
    def fake_snapshot(paths):
        calls["n"] += 1
        return [{"drive": "I:\\", "free_gb": 1.0 if calls["n"] < 3 else 100.0, "total_gb": 200}]
    monkeypatch.setattr(sh, "disk_snapshot", fake_snapshot)
    monkeypatch.setattr(sh, "gpu_snapshot", lambda: None)
    monkeypatch.setattr(wt, "_min_free_gb", lambda root: 10.0)
    logs, phases = [], []
    class S:
        state = {}
        def _append_log(self, m): logs.append(m)
        def set_phase(self, p): phases.append(p)
    wt.wait_for_disk_space(S(), {"x": str(tmp_path)}, str(tmp_path), str(tmp_path), poll_seconds=0)
    assert phases.count("paused_low_disk") == 2
    assert any("PAUSED" in m for m in logs) and any("resuming" in m for m in logs)
    assert S.state["last_error"] is None


def test_new_settings():
    from watcher_settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["resume_on_start"] is True and DEFAULT_SETTINGS["min_free_disk_gb"] == 10.0
