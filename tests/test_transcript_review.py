import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transcript_review as tr
import transcript_search as ts
import watch_and_transcribe as wt


def _setup(tmp_path):
    dirs = {k: str(tmp_path / k) for k in ("completed_json", "completed_transcripts", "completed_csv")}
    for d in dirs.values():
        os.makedirs(d)
    segs = [
        {"start": 0.0, "end": 2.0, "text": " Hello there.", "avg_logprob": -0.2},
        {"start": 2.0, "end": 4.5, "text": " We'll be right back.", "avg_logprob": -1.4, "no_speech_prob": 0.7},
        {"start": 4.5, "end": 6.0, "text": " Bye now.", "avg_logprob": -0.1, "flags": "low model agreement 0.4"},
    ]
    (tmp_path / "completed_json" / "v.json").write_text(json.dumps(segs), encoding="utf-8")
    (tmp_path / "completed_json" / "fingerprints.json").write_text("{}", encoding="utf-8")
    (tmp_path / "completed_csv" / "v.csv").write_text("start,end,text\n", encoding="utf-8")
    return dirs


def test_list_and_flags(tmp_path):
    dirs = _setup(tmp_path)
    lst = tr.list_transcripts(dirs["completed_json"])
    assert [t["stem"] for t in lst] == ["v"] and lst[0]["flagged"] == 2
    items = tr.flagged_items(os.path.join(dirs["completed_json"], "v.json"))
    assert [i["index"] for i in items] == [1, 2]
    assert "low avg_logprob" in items[0]["reason"] and items[1]["reason"] == "low model agreement 0.4"


def test_save_keeps_original_and_updates_outputs(tmp_path, monkeypatch):
    dirs = _setup(tmp_path)
    db = tmp_path / "idx.sqlite"
    monkeypatch.setattr(ts, "DB_PATH", db)
    real_index = ts.index_transcript
    monkeypatch.setattr(ts, "index_transcript", lambda jp, **kw: real_index(jp, txt_path=kw.get("txt_path"), db_path=db))
    tr.save_correction("v", dirs, 1, "  We will see you   tomorrow. ")
    segs = json.loads((tmp_path / "completed_json" / "v.json").read_text(encoding="utf-8"))
    assert segs[1]["text"] == " We will see you tomorrow." and segs[1]["original_text"] == " We'll be right back."
    assert segs[1]["reviewed"] is True
    orig = json.loads((tmp_path / "completed_json" / "v.original.json").read_text(encoding="utf-8"))
    assert orig[1]["text"] == " We'll be right back."
    txt = (tmp_path / "completed_transcripts" / "v.txt").read_text(encoding="utf-8")
    assert "We will see you tomorrow." in txt and "right back" not in txt
    assert "tomorrow" in (tmp_path / "completed_transcripts" / "v.srt").read_text(encoding="utf-8")
    assert "tomorrow" in (tmp_path / "completed_transcripts" / "v.vtt").read_text(encoding="utf-8")
    assert "original_text" in (tmp_path / "completed_csv" / "v.csv").read_text(encoding="utf-8")
    assert [h["stem"] for h in ts.search("tomorrow", db_path=db)] == ["v"]
    assert ts.search("right back", db_path=db) == []
    # second edit keeps the FIRST original
    tr.save_correction("v", dirs, 1, "See you tomorrow.")
    segs = json.loads((tmp_path / "completed_json" / "v.json").read_text(encoding="utf-8"))
    assert segs[1]["original_text"] == " We'll be right back."
    tr.revert("v", dirs, 1)
    segs = json.loads((tmp_path / "completed_json" / "v.json").read_text(encoding="utf-8"))
    assert segs[1]["text"] == " We'll be right back." and "original_text" not in segs[1]
    assert tr.list_transcripts(dirs["completed_json"])[0]["stem"] == "v"  # .original.json not listed


def test_mark_ok_and_empty_rejected(tmp_path, monkeypatch):
    dirs = _setup(tmp_path)
    monkeypatch.setattr(ts, "index_transcript", lambda *a, **k: 0)
    tr.mark_reviewed("v", dirs, 2)
    assert tr.list_transcripts(dirs["completed_json"])[0]["reviewed"] == 1
    import pytest
    with pytest.raises(ValueError):
        tr.save_correction("v", dirs, 1, "   ")


def test_snippet_command():
    c = tr.snippet_command("vlc", "vlc.exe", "a.mp4", 10.0, 12.5)
    assert c == ["vlc.exe", "--no-one-instance", "--play-and-exit", "--start-time=9.00", "--stop-time=13.50", "a.mp4"]
    assert tr.snippet_command("mpv", "mpv", "a.mp4", 0.5, 1)[1] == "--start=0.00"


def test_backfill_ignores_registry_and_originals():
    assert ts.is_transcript_json("x.json") and not ts.is_transcript_json("fingerprints.json")
    assert not ts.is_transcript_json("x.original.json")


def test_skip_requested_consumes(tmp_path):
    import queue_control as qc
    w = str(tmp_path); a = os.path.join(w, "a.mp4")
    assert wt.skip_requested(w, a) is False
    qc.request_skip(w, a)
    assert wt.skip_requested(w, a) is True and wt.skip_requested(w, a) is False
