import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aeonimus_handoff as ah  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv(ah.CONFIG_ENV, str(tmp_path / "appdata" / "Aeonimus" / "handoff.json"))
    cfg = ah.load_config()
    ah.ensure_folders(cfg)
    return cfg


def _write(p: Path, text: str = "https://www.youtube.com/watch?v=jNQXAC9IVRw\n", age: float = 60.0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def test_config_created_with_defaults(env, tmp_path):
    path = ah.config_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert Path(data["inbox"]) == tmp_path / "home" / "Downloads" / "Aeonimus"
    assert Path(data["library"]) == tmp_path / "home" / "Documents" / "Aeonimus Library"
    for sub in ah.SUBFOLDERS:
        assert (env.inbox / sub).is_dir() and (env.library / sub).is_dir()


def test_save_config_keeps_unknown_keys_and_is_shared(env, tmp_path):
    path = ah.config_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["commentWorkbench"] = {"x": 1}
    path.write_text(json.dumps(data), encoding="utf-8")
    ah.save_config(library=tmp_path / "NewLib")
    again = json.loads(path.read_text(encoding="utf-8"))
    assert again["commentWorkbench"] == {"x": 1}
    assert ah.load_config().library == tmp_path / "NewLib"


def test_existing_config_is_read_not_overwritten(env, tmp_path):
    path = ah.config_path()
    original = '{\n    "inbox":  "X:\\\\Inbox",\n    "library":  "D:\\\\Aeonimus Library",\n    "version":  1\n}'
    path.write_text("\ufeff" + original, encoding="utf-8")  # PowerShell-style BOM
    before = path.read_bytes()
    cfg = ah.load_config()
    assert str(cfg.library).endswith("Aeonimus Library") and "Inbox" in str(cfg.inbox)
    assert path.read_bytes() == before, "an existing config is never rewritten on read"
    ah.save_config(library=tmp_path / "L2")
    assert json.loads(path.read_text(encoding="utf-8-sig"))["version"] == 1


def test_broken_config_falls_back_to_defaults(env):
    ah.config_path().write_text("{not json", encoding="utf-8")
    cfg = ah.load_config()
    assert cfg.library.name == "Aeonimus Library"


def test_inbox_files_are_moved_not_copied_and_never_overwrite(env):
    _write(env.library / "link-lists" / "chan_videos_2026-10-17.csv", "old")
    src = _write(env.inbox / "link-lists" / "chan_videos_2026-10-17.csv", "new")
    _write(env.inbox / "link-lists" / "notes.pdf")
    moved = ah.move_inbox_link_lists(env)
    assert not src.exists()
    assert [p.name for p in moved] == ["chan_videos_2026-10-17 (2).csv"]
    assert (env.library / "link-lists" / "chan_videos_2026-10-17.csv").read_text() == "old"
    assert (env.library / "link-lists" / "chan_videos_2026-10-17 (2).csv").read_text() == "new"
    assert (env.inbox / "link-lists" / "notes.pdf").exists(), "only link lists move"


def test_files_still_being_written_are_left_for_next_time(env):
    fresh = _write(env.inbox / "link-lists" / "fresh.csv", age=0)
    assert ah.move_inbox_link_lists(env, min_age_s=30) == []
    assert fresh.exists()


def test_pick_newest_not_imported_then_offer_newest(env, tmp_path):
    ledger = ah.ImportLedger(tmp_path / "ledger.json")
    old = _write(env.library / "link-lists" / "a.csv", age=300)
    new = _write(env.inbox / "link-lists" / "b.csv", age=100)
    pick = ah.pick_link_list(env, ledger)
    assert pick.is_new and pick.path.name == "b.csv" and pick.path.parent == env.library / "link-lists"
    ledger.mark(pick.path)
    pick2 = ah.pick_link_list(env, ah.ImportLedger(tmp_path / "ledger.json"))  # remembered across runs
    assert pick2.is_new and pick2.path == old
    ledger.mark(old)
    pick3 = ah.pick_link_list(env, ledger)
    assert not pick3.is_new and pick3.path.name == "b.csv"
    assert not new.exists()


def test_changed_file_counts_as_new_again(env, tmp_path):
    ledger = ah.ImportLedger(tmp_path / "ledger.json")
    p = _write(env.library / "link-lists" / "a.csv", age=300)
    ledger.mark(p)
    assert ah.pick_link_list(env, ledger).is_new is False
    _write(p, "changed", age=10)
    assert ah.pick_link_list(env, ledger).is_new is True


def test_no_link_lists(env, tmp_path):
    pick = ah.pick_link_list(env, ah.ImportLedger(tmp_path / "l.json"))
    assert pick.path is None and not pick.is_new


def test_ledger_lives_in_app_data_not_shared_folders(env):
    ledger = ah.ImportLedger()
    assert env.library not in ledger.path.parents and env.inbox not in ledger.path.parents


def test_publish_transcript(env, tmp_path):
    out = tmp_path / "out"
    txt = _write(out / "Me at the zoo [jNQXAC9IVRw]_1.txt", "hello")
    js = _write(out / "Me at the zoo [jNQXAC9IVRw]_1.json", "{}")
    _write(out / "no id here.txt")
    written = ah.publish_transcript(txt, js, env)
    names = sorted(p.name for p in written)
    assert names == ["Me at the zoo [jNQXAC9IVRw].json", "Me at the zoo [jNQXAC9IVRw].txt"]
    assert ah.publish_transcript(out / "no id here.txt", out / "missing.json", env) == []
    assert not list((env.library / "transcripts").glob("*.part"))


def test_move_library(env, tmp_path):
    _write(env.library / "transcripts" / "x [jNQXAC9IVRw].txt", "a")
    _write(env.library / "link-lists" / "a.csv", "b")
    new = tmp_path / "Elsewhere"
    _write(new / "link-lists" / "a.csv", "keep")
    assert ah.library_has_files(env.library)
    moved, failed = ah.move_library(env.library, new)
    assert (moved, failed) == (2, 0)
    assert (new / "link-lists" / "a.csv").read_text() == "keep"
    assert (new / "link-lists" / "a (2).csv").read_text() == "b"
    assert (new / "transcripts" / "x [jNQXAC9IVRw].txt").exists()
    assert not ah.library_has_files(env.library)
    assert ah.move_library(new, new) == (0, 0)


def test_unique_destination(tmp_path):
    (tmp_path / "a.csv").write_text("1")
    (tmp_path / "a (2).csv").write_text("2")
    assert ah.unique_destination(tmp_path, "a.csv").name == "a (3).csv"
    assert ah.unique_destination(tmp_path, "b.csv").name == "b.csv"
