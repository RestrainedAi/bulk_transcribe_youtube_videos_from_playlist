"""v1.5.0: optional speaker labels."""
from __future__ import annotations

import json
from pathlib import Path

import speaker_labels as sl
import transcript_search as ts
from watcher_settings import DEFAULT_SETTINGS

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_150_or_later() -> None:
    import app_version
    assert tuple(int(x) for x in app_version.APP_VERSION.split(".")) >= (1, 5, 0)


def test_off_by_default_and_env_override(monkeypatch) -> None:
    assert DEFAULT_SETTINGS["label_speakers"] is False
    monkeypatch.delenv("LABEL_SPEAKERS", raising=False)
    assert not sl.enabled({})
    assert sl.enabled({"label_speakers": True})
    monkeypatch.setenv("LABEL_SPEAKERS", "0")
    assert not sl.enabled({"label_speakers": True})


def test_numbered_by_first_appearance_and_split_at_change() -> None:
    segs = [
        {"start": 0.0, "end": 2.0, "text": " Hello there.", "_words": [(0.0, 0.8, " Hello"), (0.8, 2.0, " there.")]},
        {"start": 2.0, "end": 6.0, "text": " Yes I know. Do you?",
         "_words": [(2.0, 2.4, " Yes"), (2.4, 2.8, " I"), (2.8, 3.2, " know."), (4.0, 4.6, " Do"), (4.6, 5.8, " you?")]},
    ]
    turns = [(0.0, 3.3, 7), (3.9, 6.0, 3)]
    out = sl.assign_speakers(segs, turns)
    assert [s["speaker"] for s in out] == ["Speaker 1", "Speaker 1", "Speaker 2"]
    assert out[1]["text"].strip() == "Yes I know." and out[2]["text"].strip() == "Do you?"
    assert out[2]["start"] == 4.0 and out[2]["split_by_speaker"]
    assert all("_words" not in s for s in out)


def test_one_word_blip_is_not_a_turn() -> None:
    segs = [{"start": 0.0, "end": 3.0, "text": " a b c", "_words": [(0.0, 1.0, " a"), (1.0, 1.2, " b"), (1.2, 3.0, " c")]}]
    out = sl.assign_speakers(segs, [(0.0, 1.0, 0), (1.0, 1.2, 1), (1.2, 3.0, 0)])
    assert len(out) == 1 and out[0]["speaker"] == "Speaker 1"


def test_overlap_without_word_timings() -> None:
    segs = [{"start": 0.0, "end": 4.0, "text": " x"}, {"start": 5.0, "end": 9.0, "text": " y"}]
    out = sl.assign_speakers(segs, [(0.0, 4.5, 1), (4.5, 9.0, 0)])
    assert [s["speaker"] for s in out] == ["Speaker 1", "Speaker 2"]


def test_tiny_clusters_folded_into_neighbour() -> None:
    turns = [(0, 30, 0), (30, 30.4, 5), (31, 60, 1)]
    merged = sl.merge_minor_speakers(turns)
    assert {t[2] for t in merged} == {0, 1}


def test_outputs_txt_srt_show_labels() -> None:
    segs = [{"start": 0, "end": 1, "text": " Hi.", "speaker": "Speaker 1"},
            {"start": 1, "end": 2, "text": " How are you?", "speaker": "Speaker 1"},
            {"start": 2, "end": 3, "text": " Fine.", "speaker": "Speaker 2"}]
    assert sl.render_txt(segs) == "Speaker 1: Hi. How are you?\n\nSpeaker 2: Fine."
    assert "Speaker 2: Fine." in ts.to_srt(segs)
    assert "Speaker" not in ts.to_srt([{"start": 0, "end": 1, "text": "plain"}])


def test_failure_never_loses_transcript() -> None:
    segs = [{"start": 0, "end": 1, "text": " a", "_words": [(0, 1, " a")]}]
    def boom(*a, **k):
        raise RuntimeError("model broke")
    out, note = sl.label_segments("x.wav", segs, {}, diarizer=boom)
    assert out == [{"start": 0, "end": 1, "text": " a"}] and "skipped" in note


def test_missing_models_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIARIZATION_MODELS_DIR", raising=False)
    r = sl.check_ready({"diarization_models_dir": str(tmp_path)})
    assert r and ("missing" in r or "sherpa-onnx" in r)


def test_speakers_file_keeps_typed_names_and_rename(tmp_path) -> None:
    j, t, c = tmp_path / "json", tmp_path / "txt", tmp_path / "csv"
    for d in (j, t, c):
        d.mkdir()
    segs = [{"start": 0, "end": 1, "text": " Hi.", "speaker": "Speaker 1"},
            {"start": 1, "end": 2, "text": " Yo.", "speaker": "Speaker 2"}]
    (j / "s.json").write_text(json.dumps(segs), encoding="utf-8")
    p = sl.write_speakers_file(j, "s", segs)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["names"]["Speaker 1"] = "Thomas"
    p.write_text(json.dumps(data), encoding="utf-8")
    sl.write_speakers_file(j, "s", segs)  # a re-run must keep the typed name
    assert json.loads(p.read_text(encoding="utf-8"))["names"]["Speaker 1"] == "Thomas"
    import transcript_search
    orig = transcript_search.index_transcript
    transcript_search.index_transcript = lambda *a, **k: 0
    try:
        assert sl.rename("s", j, t, c) == 1
    finally:
        transcript_search.index_transcript = orig
    assert (t / "s.txt").read_text(encoding="utf-8").startswith("Thomas: Hi.")
    assert "Thomas: Hi." in (t / "s.srt").read_text(encoding="utf-8")
    assert "Thomas" in (c / "s.csv").read_text(encoding="utf-8")
    assert (j / "s.before-rename.json").is_file()


def test_checkbox_and_settings_wired() -> None:
    panel = (ROOT / "watcher_control_panel.py").read_text(encoding="utf-8")
    assert '"Label speakers"' in panel and "set_label_speakers" in panel
    worker = (ROOT / "watch_and_transcribe.py").read_text(encoding="utf-8")
    assert 'env["LABEL_SPEAKERS"]' in worker


def test_docs_list_licenses() -> None:
    doc = (ROOT / "docs" / "SPEAKER_LABELS.md").read_text(encoding="utf-8")
    for needle in ("Apache-2.0", "MIT", "CC-BY-4.0", "GPL"):
        assert needle in doc
