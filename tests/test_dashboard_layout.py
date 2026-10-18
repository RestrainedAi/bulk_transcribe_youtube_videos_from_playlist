"""1.2.1: Dashboard fits at 1000x1024, pages scroll, Latest transcripts helpers."""
from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import pytest

import aeonimus_handoff


def test_latest_transcripts_newest_first_and_limited(tmp_path: Path) -> None:
    now = time.time()
    for i in range(7):
        p = tmp_path / f"Song {i} [abcdefghij{i}].txt"
        p.write_text("x", encoding="utf-8")
        os.utime(p, (now - 100 + i, now - 100 + i))
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")
    got = aeonimus_handoff.latest_transcripts(tmp_path, limit=5)
    assert [p.name for p in got] == [f"Song {i} [abcdefghij{i}].txt" for i in (6, 5, 4, 3, 2)]


def test_latest_transcripts_missing_folder(tmp_path: Path) -> None:
    assert aeonimus_handoff.latest_transcripts(tmp_path / "nope") == []


def test_describe_transcript_drops_id(tmp_path: Path) -> None:
    p = tmp_path / "Me at the zoo [jNQXAC9IVRw].txt"
    p.write_text("x", encoding="utf-8")
    text = aeonimus_handoff.describe_transcript(p)
    assert text.startswith("Me at the zoo  -  ")
    assert "jNQXAC9IVRw" not in text


def _make_panel(w: int, h: int):
    tk = pytest.importorskip("tkinter")
    import watcher_control_panel as wcp

    class _Quiet(wcp.ControlPanel):
        # no status polling / auto-handoff during a layout test
        def refresh(self) -> None:  # noqa: D401
            pass

        def refresh_once(self) -> None:
            pass

    try:
        app = _Quiet(socket.socket())
    except tk.TclError as exc:  # no display
        pytest.skip(f"no display: {exc}")
    app.geometry(f"{w}x{h}+0+0")
    app._show_page("dashboard")
    for _ in range(5):
        app.update()
    return app


@pytest.mark.skipif(os.environ.get("TW_SKIP_GUI") == "1", reason="GUI tests disabled")
def test_dashboard_fits_without_scrolling_at_1000x1024() -> None:
    app = _make_panel(1000, 1024)
    try:
        page = app.pages["dashboard"]
        qa = app.quick_actions_frame
        bottom = qa.winfo_rooty() + qa.winfo_height()
        visible_bottom = page.canvas.winfo_rooty() + page.canvas.winfo_height()
        assert bottom <= visible_bottom, (bottom, visible_bottom)
        assert not page.needs_scroll()
    finally:
        app._on_close()


@pytest.mark.skipif(os.environ.get("TW_SKIP_GUI") == "1", reason="GUI tests disabled")
def test_every_page_scrolls_in_a_small_window() -> None:
    app = _make_panel(900, 600)
    try:
        page = app.pages["dashboard"]
        assert page.needs_scroll()
        page.canvas.yview_moveto(1.0)
        app.update()
        assert page.canvas.yview()[0] > 0
        qa = app.quick_actions_frame
        assert qa.winfo_rooty() + qa.winfo_height() <= page.canvas.winfo_rooty() + page.canvas.winfo_height() + 2
        for key, pg in app.pages.items():
            assert hasattr(pg, "inner") and hasattr(pg, "canvas"), key
    finally:
        app._on_close()
