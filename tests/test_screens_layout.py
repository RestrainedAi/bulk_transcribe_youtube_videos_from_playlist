"""1.3.0: every page matches the redesign and fits at 1000x1024 and 1280x720 with nothing clipped."""
from __future__ import annotations

import os
import socket

import pytest

PAGES = ("queue", "monitor", "logs", "live_transcribe", "handoff", "search", "review", "settings")
GUI = pytest.mark.skipif(os.environ.get("TW_SKIP_GUI") == "1", reason="GUI tests disabled")


def _panel(w: int, h: int):
    tk = pytest.importorskip("tkinter")
    import watcher_control_panel as wcp

    class _Quiet(wcp.ControlPanel):
        def refresh(self) -> None:
            pass

        def refresh_once(self) -> None:
            pass

    try:
        app = _Quiet(socket.socket())
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    app.geometry(f"{w}x{h}+0+0")
    for _ in range(5):
        app.update()
    return app


def _walk(w):
    for c in w.winfo_children():
        yield c
        yield from _walk(c)


def _clipped(app, key: str) -> list[str]:
    page = app.pages[key]
    cv = page.canvas
    right = cv.winfo_rootx() + cv.winfo_width()
    bottom = cv.winfo_rooty() + cv.winfo_height()
    scroll = page.needs_scroll()
    out = []
    for w in _walk(page.inner):
        if not w.winfo_ismapped():
            continue
        cls = w.winfo_class()
        if w.winfo_rootx() + w.winfo_width() > right + 1:
            out.append(f"{cls} past right edge")
        if not scroll and w.winfo_rooty() + w.winfo_height() > bottom + 1:
            out.append(f"{cls} past bottom")
        if cls in ("TButton", "TCheckbutton") and w.winfo_width() < w.winfo_reqwidth() - 2:
            out.append(f"{cls} {w.cget('text')!r} squeezed")
        if cls == "Treeview":
            tot = sum(int(w.column(c, "width")) for c in w["columns"])
            if tot > w.winfo_width() + 2:
                out.append(f"Treeview columns {tot} > {w.winfo_width()}")
    return out


@GUI
@pytest.mark.parametrize("size", [(1000, 1024), (1280, 720)])
def test_every_page_fits_without_clipping(size) -> None:
    app = _panel(*size)
    try:
        assert app._dash_compact is (size == (1280, 720))
        for key in PAGES:
            app._show_page(key)
            for _ in range(6):
                app.update()
            assert _clipped(app, key) == [], (key, size, _clipped(app, key))
            if key != "settings":
                assert not app.pages[key].needs_scroll(), (key, size)
    finally:
        app._on_close()


@GUI
@pytest.mark.parametrize("size", [(1000, 1024), (1280, 720)])
def test_settings_scrolls_with_a_sticky_save_bar(size) -> None:
    app = _panel(*size)
    try:
        app._show_page("settings")
        app.update()
        page = app.pages["settings"]
        bar = app.settings_save_bar
        assert bar.master is page and bar.winfo_ismapped()
        top = bar.winfo_rooty()
        page.canvas.yview_moveto(1.0)
        app.update()
        assert bar.winfo_rooty() == top
        assert bar.winfo_rooty() + bar.winfo_height() <= app.winfo_rooty() + app.winfo_height()
    finally:
        app._on_close()


@GUI
def test_queue_controls_and_table_columns() -> None:
    import watcher_control_panel as wcp
    app = _panel(1000, 1024)
    try:
        app._show_page("queue")
        app.update()
        assert app.q_pause_btn.winfo_width() >= app.q_pause_btn.winfo_reqwidth() - 2
        assert list(app.queue_tree["columns"]) == ["file", "state", "engine", "progress", "eta"]
        assert int(app.queue_tree.column("file", "width")) == 332
        assert app.queue_tree.cget("style") == "Screen.Treeview"
        # Colours depend on the saved skin (1.4.0): compare against whatever skin is active.
        import themes
        want = themes.panel_constants(app._skin)
        for k in ("D_WARNING", "D_WARNING_SURFACE", "D_TEXT_DISABLED"):
            assert getattr(wcp, k) == want[k], k
    finally:
        app._on_close()


@GUI
def test_compact_column_widths() -> None:
    app = _panel(1280, 720)
    try:
        assert int(app.queue_tree.column("file", "width")) == 244
        assert int(app.search_tree.column("video", "width")) == 280
        assert int(app.review_tree.column("state", "width")) == 112
    finally:
        app._on_close()


@GUI
def test_settings_dirty_state_and_validation() -> None:
    app = _panel(1000, 1024)
    try:
        sb = app._sb
        assert "All changes saved" in sb["label"].cget("text")
        old = app.set_hb.get()
        app.set_hb.set(old + 1)
        app.update()
        assert "Unsaved changes (1)" in sb["label"].cget("text")
        assert "Heartbeat" in sb["detail"].cget("text")
        app.set_cov_gap.set("abc")
        vals, bad = app._settings_values()
        assert vals is None and bad == ["Coverage max gap"]
        app._settings_discard()
        app.update()
        assert "All changes saved" in sb["label"].cget("text")
        assert app.set_cov_gap.get() in ("60", "60.0") or float(app.set_cov_gap.get()) > 0
    finally:
        app._on_close()


@GUI
def test_handoff_flags_are_read_only_real_settings() -> None:
    app = _panel(1000, 1024)
    try:
        chips = app._handoff_flag_chips
        assert "NOTIFY_MOBILE_APP" not in chips and "APPEND_TIMESTAMPS" not in chips
        assert "STRIP_SPEAKER_IDS" not in chips
        app._settings["handoff_try_api_first"] = True
        app._refresh_handoff_flags()
        assert chips["LLM_API_FIRST"]._chip_parts[2].cget("text").endswith("ON")
        app._settings["handoff_try_api_first"] = False
        app._refresh_handoff_flags()
        assert chips["LLM_API_FIRST"]._chip_parts[2].cget("text").endswith("OFF")
        for c in chips.values():
            assert not c.bind("<Button-1>")
    finally:
        app._on_close()


@GUI
def test_review_missed_audio_strip_and_editor_states() -> None:
    app = _panel(1000, 1024)
    try:
        app._review_set_warning([{"kind": "gap", "start": 56, "end": 545}])
        app.update()
        assert app._r["warn"].winfo_manager() == "pack"
        assert app.review_warn_title.get() == "Possibly missed audio at 0:56\u20139:05"
        app._review_set_warning([])
        app.update()
        assert app._r["warn"].winfo_manager() == ""
        app._review_editor_state(None)
        assert all(b.instate(["disabled"]) for b in app.review_btns.values())
        app._review_editor_state({"kind": "gap", "start": 1, "end": 2})
        assert app.review_btns["play"].instate(["!disabled"]) and app.review_btns["save"].instate(["disabled"])
    finally:
        app._on_close()


@GUI
def test_search_buttons_disabled_without_selection() -> None:
    app = _panel(1000, 1024)
    try:
        assert app.search_play_btn.instate(["disabled"]) and app.search_open_btn.instate(["disabled"])
    finally:
        app._on_close()


def test_version_label_reads_app_version() -> None:
    import app_version
    assert app_version.APP_VERSION == "1.4.1"
