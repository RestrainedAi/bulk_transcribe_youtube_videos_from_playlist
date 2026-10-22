import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import queue_control as qc
import watch_and_transcribe as wt


def test_ordered_and_move(tmp_path):
    w = str(tmp_path)
    a, b, c = (os.path.join(w, n) for n in ("a.mp4", "b.mp4", "c.mp4"))
    assert qc.ordered([a, b, c], []) == [a, b, c]
    assert qc.move(w, [a, b, c], c, -1) == [a, c, b]
    assert qc.move(w, [a, b, c], c, -5) == [c, a, b]
    assert qc.ordered([a, b, c], qc.load(w)["order"]) == [c, a, b]
    d = os.path.join(w, "d.mp4")  # new arrivals go after the ordered ones
    assert qc.ordered([a, b, c, d], qc.load(w)["order"]) == [c, a, b, d]


def test_pause_and_skip(tmp_path):
    w = str(tmp_path); a = os.path.join(w, "a.mp4"); b = os.path.join(w, "b.mp4")
    qc.set_paused(w, True); assert qc.load(w)["paused"] is True
    qc.request_skip(w, b); qc.request_skip(w, b)
    assert qc.load(w)["skip"] == [b]
    assert qc.take_skips(w, [a, b]) == [b]
    assert qc.load(w)["skip"] == [] and qc.load(w)["paused"] is True


def test_corrupt_control_file(tmp_path):
    (tmp_path / qc.CONTROL_NAME).write_text("{oops")
    assert qc.load(str(tmp_path)) == {"paused": False, "order": [], "skip": [], "rerun": []}


def test_progress_and_eta():
    now = 1000.0
    frac, eta = qc.job_progress({"duration": 100, "last_segment_end": 25, "started_at_epoch": 990}, now)
    assert frac == 0.25 and abs(eta - 30) < 1e-6
    assert qc.job_progress({"last_segment_end": 5}, now) == (None, None)
    assert qc.format_eta(None) == "-" and qc.format_eta(75) == "1:15" and qc.format_eta(3725) == "1:02:05"


def test_describe_engine():
    assert wt.describe_engine({"asr_engine": "whisper", "whisper_gpu_model": "large-v3"}) == "whisper large-v3"
    assert wt.describe_engine({"asr_engine": "whisper", "whisper_batched": True, "asr_crosscheck": True}) == "whisper large-v3 fast +check"
    assert wt.describe_engine({"asr_engine": "voxtral-mini-3b"}) == "voxtral-mini-3b"
    assert wt.describe_engine({}, force_cpu=True) == "whisper medium (CPU)"


def test_catch_up_new_files(tmp_path):
    watch = tmp_path / "w"; comp = watch / "Completed"; comp.mkdir(parents=True)
    old = watch / "old.mp4"; old.write_bytes(b"1"); os.utime(old, (1, 1))
    new = watch / "new.mp4"; new.write_bytes(b"1"); (comp / "done.mp4").write_bytes(b"1"); (watch / "x.txt").write_bytes(b"1")
    got = []
    class H:
        def _schedule(self, p): got.append(os.path.basename(p))
    since = time.time() - 60
    n = wt.catch_up_new_files(H(), str(watch), str(comp), since)
    assert "new.mp4" in got and "done.mp4" not in got and "x.txt" not in got and n == len(got)


def test_forget(tmp_path):
    w = str(tmp_path); a = os.path.join(w, "a.mp4"); b = os.path.join(w, "b.mp4")
    qc.move(w, [a, b], b, -1); qc.forget(w, b)
    assert qc.load(w)["order"] == [a]
