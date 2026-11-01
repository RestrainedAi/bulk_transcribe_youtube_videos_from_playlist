"""1.5.2: optional per-job --output-dir on the worker."""
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app_version  # noqa: E402
import watch_transcribe_worker as w  # noqa: E402

bt = w.bt
SETTINGS = ROOT / "watcher_settings.json"


def _digest(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def test_version_is_152():
    assert app_version.APP_VERSION == "1.5.2"


def test_parser_output_dir_optional():
    a = w.build_parser().parse_args(["--input", "x.wav", "--stem", "x", "--progress", "p.json"])
    assert a.output_dir is None
    a = w.build_parser().parse_args(
        ["--input", "x.wav", "--stem", "x", "--progress", "p.json", "--output-dir", "out"]
    )
    assert a.output_dir == "out"


def test_no_output_dir_keeps_defaults(monkeypatch):
    before = (bt.COMPLETED_TRANSCRIPTS_DIR, bt.COMPLETED_CSV_DIR, bt.COMPLETED_JSON_DIR)
    assert w.apply_output_dir(None) is None
    assert w.apply_output_dir("") is None
    assert (bt.COMPLETED_TRANSCRIPTS_DIR, bt.COMPLETED_CSV_DIR, bt.COMPLETED_JSON_DIR) == before


def test_output_dir_created_and_applied(monkeypatch, tmp_path):
    for n in ("COMPLETED_TRANSCRIPTS_DIR", "COMPLETED_CSV_DIR", "COMPLETED_JSON_DIR"):
        monkeypatch.setattr(bt, n, getattr(bt, n))
    target = tmp_path / "a" / "b"
    assert not target.exists()
    out = w.apply_output_dir(str(target))
    assert target.is_dir() and out == str(target.resolve())
    assert bt.COMPLETED_TRANSCRIPTS_DIR == bt.COMPLETED_CSV_DIR == bt.COMPLETED_JSON_DIR == out


def test_main_writes_to_output_dir_and_leaves_settings(monkeypatch, tmp_path):
    for n in ("COMPLETED_TRANSCRIPTS_DIR", "COMPLETED_CSV_DIR", "COMPLETED_JSON_DIR"):
        monkeypatch.setattr(bt, n, getattr(bt, n))
    default_json = bt.COMPLETED_JSON_DIR
    settings_before = _digest(SETTINGS)
    src = tmp_path / "clip.wav"
    src.write_bytes(b"\0" * 64)
    out = tmp_path / "job_out"

    async def fake_compute(audio_path, stem, size_mb, progress_callback=None):
        for d, ext in ((bt.COMPLETED_TRANSCRIPTS_DIR, "txt"), (bt.COMPLETED_CSV_DIR, "csv"), (bt.COMPLETED_JSON_DIR, "json")):
            Path(d, f"{stem}.{ext}").write_text("hi", encoding="utf-8")
        return "hi", [{"start": 0, "end": 1, "text": "hi"}], []

    monkeypatch.setattr(bt, "compute_transcript_with_whisper_from_audio_func", fake_compute)
    monkeypatch.setattr(w.console_guard, "disable_quickedit", lambda: None)
    monkeypatch.setattr(w.asr_engines, "set_pass_listener", lambda f: None)
    monkeypatch.setenv("AUDIO_MODE", "speech")
    prog = tmp_path / "p.json"
    monkeypatch.setattr(sys, "argv", ["w", "--input", str(src), "--stem", "t152", "--progress", str(prog), "--output-dir", str(out)])
    assert w.main() == 0
    for ext in ("txt", "csv", "json"):
        assert (out / f"t152.{ext}").is_file()
    assert not Path(default_json, "t152.json").exists()
    import json
    assert json.loads(prog.read_text(encoding="utf-8"))["output_dir"] == str(out.resolve())
    assert _digest(SETTINGS) == settings_before
