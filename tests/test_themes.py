"""1.3.1: four user-selectable skins (Harbor default, Midnight, Graphite, Aurora)."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

import themes
from watcher_settings import load_settings, save_settings

GUI = pytest.mark.skipif(os.environ.get("TW_SKIP_GUI") == "1", reason="GUI tests disabled")
ROOT = Path(__file__).resolve().parents[1]


def test_every_skin_defines_all_20_tokens() -> None:
    assert len(themes.TOKEN_ORDER) == 20
    assert set(themes.SKIN_NAMES) == set(themes.SKINS)
    for name, t in themes.SKINS.items():
        assert tuple(t) == themes.TOKEN_ORDER, name
        for v in t.values():
            assert v.startswith("#") and len(v) == 7, (name, v)
        assert set(themes.panel_constants(name)) == set(themes.panel_constants("Harbor"))


def test_skins_match_figma_palettes_json() -> None:
    p = ROOT / "docs" / "design" / "palettes.json"
    if not p.is_file():
        pytest.skip("palettes.json not present")
    data = json.loads(p.read_text(encoding="utf-8"))["palettes"]
    for name in themes.SKIN_NAMES:
        assert themes.SKINS[name] == data[name]["c"], name


@pytest.mark.parametrize("value", [None, "", "Neon", 42, "  ", "harbour"])
def test_unknown_or_missing_theme_falls_back_to_harbor(value) -> None:
    assert themes.normalize(value) == "Harbor"
    assert themes.panel_constants(value) == themes.panel_constants("Harbor")


def test_names_are_case_insensitive() -> None:
    assert themes.normalize("aurora") == "Aurora"
    assert themes.normalize(" MIDNIGHT ") == "Midnight"


def test_text_contrast_meets_4_5() -> None:
    pairs = [("text", "bg"), ("text", "surface"), ("text", "surface-raised"), ("text", "input"),
             ("text-muted", "bg"), ("text-muted", "surface"), ("text-muted", "surface-2"), ("accent", "surface"),
             ("accent", "nav-selected"), ("accent", "primary"), ("success", "success-surface"),
             ("danger-text", "danger"), ("danger-text", "danger-surface"), ("warning", "warning-surface")]
    for name, t in themes.SKINS.items():
        for fg, bg in pairs:
            assert themes.contrast(t[fg], t[bg]) >= 4.5, (name, fg, bg)


def test_theme_setting_round_trips(tmp_path) -> None:
    assert load_settings(tmp_path)["theme"] == "Harbor"
    save_settings(tmp_path, {"theme": "Graphite"})
    assert json.loads((tmp_path / "watcher_settings.json").read_text(encoding="utf-8"))["theme"] == "Graphite"
    assert load_settings(tmp_path)["theme"] == "Graphite"
    save_settings(tmp_path, {"theme": "Bogus"})
    assert load_settings(tmp_path)["theme"] == "Harbor"


def test_color_map_covers_every_constant() -> None:
    for a in themes.SKIN_NAMES:
        for b in themes.SKIN_NAMES:
            m = themes.color_map(a, b)
            for k, v in themes.panel_constants(a).items():
                assert m[v.lower()] == themes.panel_constants(b)[k], (a, b, k)


@GUI
def test_live_switch_changes_colors_without_crashing() -> None:
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
    try:
        app.geometry("1000x1024+0+0")
        # Start from the really active skin (module colours follow the saved theme, e.g. Graphite).
        for _ in range(3):
            app.update()
        for name in ("Aurora", "Midnight", "Graphite", "Harbor"):
            failed = app.apply_skin(name)
            app.update()
            want = themes.SKINS[name]
            assert failed == 0, name
            assert app._skin == name
            assert wcp.D_BG == want["bg"] and wcp.D_ACCENT == want["accent"]
            assert str(app.settings_save_bar.cget("bg")).lower() == want["surface"]
            assert str(wcp.ttk.Style(app).lookup("Dash.Primary.TButton", "background")).lower() == want["primary"]
    finally:
        app._on_close()
