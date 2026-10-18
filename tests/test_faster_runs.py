import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import file_fingerprint as ff
import bulk_transcribe_youtube_videos_from_playlist as bt


def _write(p, data):
    with open(p, "wb") as f:
        f.write(data)


def test_fingerprint_stable_and_rename_proof(tmp_path):
    a = tmp_path / "a.mp4"; b = tmp_path / "renamed.mp4"
    data = os.urandom(1000) * 20000  # ~20 MB, uses sampling path
    _write(a, data); _write(b, data)
    assert ff.fingerprint(str(a)) == ff.fingerprint(str(b))


def test_fingerprint_detects_change(tmp_path):
    a = tmp_path / "a.mp4"; b = tmp_path / "b.mp4"
    _write(a, b"x" * 5000); _write(b, b"x" * 4999 + b"y")
    assert ff.fingerprint(str(a)) != ff.fingerprint(str(b))


def test_registry_find_needs_existing_json(tmp_path):
    j = tmp_path / "json"
    ff.record(str(j), "fp1", "stem1")
    assert ff.find_done(str(j), "fp1") is None  # transcript gone -> not a duplicate
    (j / "stem1.json").write_text("[]")
    assert ff.find_done(str(j), "fp1") == "stem1"
    assert ff.find_done(str(j), "other") is None


def test_corrupt_registry_is_ignored(tmp_path):
    (tmp_path / ff.REGISTRY_NAME).write_text("{not json")
    assert ff.load_registry(str(tmp_path)) == {}


def test_move_to_skipped_avoids_overwrite(tmp_path):
    for _ in range(2):
        src = tmp_path / "w" / "v.mp4"; src.parent.mkdir(exist_ok=True); src.write_bytes(b"1")
        ff.move_to_skipped(str(src), str(tmp_path))
    assert len(os.listdir(tmp_path / ff.SKIPPED_DIR_NAME)) == 2


def test_backfill(tmp_path):
    v = tmp_path / "videos"; j = tmp_path / "json"; v.mkdir(); j.mkdir()
    (v / "s1.mp4").write_bytes(b"abc"); (j / "s1.json").write_text("[]")
    (v / "s2.mp4").write_bytes(b"def")  # no json -> not recorded
    assert ff.backfill(str(v), str(j)) == 1
    assert ff.find_done(str(j), ff.fingerprint(str(v / "s1.mp4"))) == "s1"
    assert ff.backfill(str(v), str(j)) == 0


def test_batched_kwargs_drop_prompt_and_silence_threshold():
    kw = bt.transcribe_kwargs_for_profile("robust", True, None)
    out = bt.batched_transcribe_kwargs(kw)
    assert "initial_prompt" not in out and "hallucination_silence_threshold" not in out
    assert out["vad_filter"] is True and out["condition_on_previous_text"] is False


def test_batched_mode_off_by_default(monkeypatch):
    monkeypatch.delenv("WHISPER_BATCHED", raising=False)
    monkeypatch.setitem(bt._SETTINGS, "whisper_batched", False)
    assert bt.resolve_batched_mode()[0] is False
    monkeypatch.setenv("WHISPER_BATCHED", "1"); monkeypatch.setenv("WHISPER_BATCH_SIZE", "99")
    assert bt.resolve_batched_mode() == (True, 32)


def test_model_cache_reuses(monkeypatch):
    made = []
    monkeypatch.setattr(bt, "WhisperModel", lambda *a, **k: made.append(a) or object())
    bt.release_whisper_models()
    m1 = bt.get_whisper_model("tiny", "cpu", "int8"); m2 = bt.get_whisper_model("tiny", "cpu", "int8")
    assert m1 is m2 and len(made) == 1
    bt.release_whisper_models()
    assert bt._WHISPER_MODEL_CACHE == {}


def test_new_settings_defaults():
    from watcher_settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["whisper_batched"] is False
    assert DEFAULT_SETTINGS["skip_duplicates"] is True
    assert DEFAULT_SETTINGS["whisper_gpu_model"] == "large-v3"
