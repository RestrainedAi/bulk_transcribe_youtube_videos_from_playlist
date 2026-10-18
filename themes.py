"""Transcription Workbench skins (v1.3.1).

One token dict per skin, from the Figma 'Workbench tokens' modes (Palette options page, node 26:344).
Harbor is the original look and the default. The control panel reads its colour constants from
``panel_constants()`` so every widget, style and icon follows the chosen skin.
"""

from __future__ import annotations

TOKEN_ORDER: tuple[str, ...] = (
    "bg-deep", "bg", "surface", "surface-2", "surface-raised", "input", "nav-selected", "border",
    "accent", "primary", "success", "success-surface", "danger", "danger-text", "danger-surface",
    "warning", "warning-surface", "text", "text-muted", "text-disabled",
)

SKINS: dict[str, dict[str, str]] = {
    "Harbor": {
        "bg-deep": "#0c0e0f", "bg": "#121415", "surface": "#1e2021", "surface-2": "#1a1c1d",
        "surface-raised": "#282a2b", "input": "#333536", "nav-selected": "#07152a", "border": "#3e4851",
        "accent": "#8dcdff", "primary": "#004b70", "success": "#3ce36a", "success-surface": "#0f2a19",
        "danger": "#93000a", "danger-text": "#ffb4ab", "danger-surface": "#2a1717", "warning": "#ffb84d",
        "warning-surface": "#2b2112", "text": "#e2e2e3", "text-muted": "#bdc8d3", "text-disabled": "#6b7480",
    },
    "Midnight": {
        "bg-deep": "#080b11", "bg": "#0d1118", "surface": "#151b25", "surface-2": "#111620",
        "surface-raised": "#1d2431", "input": "#232c3b", "nav-selected": "#13254a", "border": "#3a4658",
        "accent": "#7ab8ff", "primary": "#1a4280", "success": "#4ade80", "success-surface": "#0e2a1c",
        "danger": "#992019", "danger-text": "#ffb4ab", "danger-surface": "#2d1518", "warning": "#fbbf24",
        "warning-surface": "#2a2110", "text": "#e6ebf2", "text-muted": "#a9b4c4", "text-disabled": "#5f6b7d",
    },
    "Graphite": {
        "bg-deep": "#0e0d0c", "bg": "#141312", "surface": "#1d1c1a", "surface-2": "#191817",
        "surface-raised": "#272523", "input": "#2f2d2a", "nav-selected": "#0f2a27", "border": "#48443f",
        "accent": "#5fd3bc", "primary": "#0d544b", "success": "#7dd87a", "success-surface": "#15261a",
        "danger": "#952e1c", "danger-text": "#ffb8a8", "danger-surface": "#2b1814", "warning": "#f2b45a",
        "warning-surface": "#2a2114", "text": "#ece8e3", "text-muted": "#bdb6ad", "text-disabled": "#6e6861",
    },
    "Aurora": {
        "bg-deep": "#0b0a10", "bg": "#110f17", "surface": "#1a1722", "surface-2": "#16131d",
        "surface-raised": "#231f2d", "input": "#2c2738", "nav-selected": "#2a1d55", "border": "#4a4260",
        "accent": "#b69cff", "primary": "#3d2a8e", "success": "#3ddc97", "success-surface": "#0f2a20",
        "danger": "#9a1e33", "danger-text": "#ffb3c1", "danger-surface": "#2d1520", "warning": "#ffc04d",
        "warning-surface": "#2c2213", "text": "#ece9f5", "text-muted": "#b8b1cc",
        "text-disabled": "#665f7a",
    },
}

SKIN_NAMES: tuple[str, ...] = ("Harbor", "Midnight", "Graphite", "Aurora")
DEFAULT_SKIN = "Harbor"


def normalize(name: object) -> str:
    """Return a known skin name (case-insensitive); anything else falls back to Harbor."""
    if isinstance(name, str):
        for n in SKIN_NAMES:
            if n.lower() == name.strip().lower():
                return n
    return DEFAULT_SKIN


def tokens(name: object) -> dict[str, str]:
    return dict(SKINS[normalize(name)])


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    """Blend colour a toward b by t (0..1)."""
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def contrast(fg: str, bg: str) -> float:
    def lum(h: str) -> float:
        out = []
        for c in _rgb(h):
            c = c / 255
            out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    a, b = lum(fg), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# Harbor's extra shades are the exact v1.3.0 values; the other skins derive theirs from tokens.
_HARBOR_EXTRA = {
    "ACCENT_GOLD": "#afcbd8", "OUTLINE": "#88929c", "PRIMARY_FIXED": "#cae6ff", "INVERSE_PRIMARY": "#006493",
    "SECONDARY_CONTAINER": "#334d58", "ON_ERROR_CONTAINER": "#ffdad6", "PRIMARY_HOVER": "#005a86",
    "DANGER_HOVER": "#ad0010", "WARNING_HOVER": "#3a2c16", "DANGER_ACTIVE": "#b3261e", "CARD_HILITE": "#1e2a38",
    "ROW_ERROR": "#5c1010", "ROW_WARN": "#5c4a10",
}


def _extras(t: dict[str, str], name: str) -> dict[str, str]:
    if name == "Harbor":
        return dict(_HARBOR_EXTRA)
    w = "#ffffff"
    return {
        "ACCENT_GOLD": mix(t["text-muted"], t["accent"], 0.25),
        "OUTLINE": mix(t["border"], t["text-muted"], 0.5),
        "PRIMARY_FIXED": mix(t["accent"], w, 0.55),
        "INVERSE_PRIMARY": mix(t["primary"], t["accent"], 0.25),
        "SECONDARY_CONTAINER": mix(t["surface-raised"], t["accent"], 0.18),
        "ON_ERROR_CONTAINER": mix(t["danger-text"], w, 0.5),
        "PRIMARY_HOVER": mix(t["primary"], t["accent"], 0.14),
        "DANGER_HOVER": mix(t["danger"], w, 0.08),
        "WARNING_HOVER": mix(t["warning-surface"], t["warning"], 0.06),
        "DANGER_ACTIVE": mix(t["danger"], t["danger-text"], 0.18),
        "CARD_HILITE": mix(t["surface"], t["accent"], 0.08),
        "ROW_ERROR": mix(t["danger-surface"], t["danger"], 0.6),
        "ROW_WARN": mix(t["warning-surface"], t["warning"], 0.22),
    }


def panel_constants(name: object) -> dict[str, str]:
    """Every colour constant the control panel uses, for the given skin."""
    n = normalize(name)
    t = SKINS[n]
    c = {
        # legacy names (pre-1.2.1 pages)
        "BG_MAIN": t["bg"], "BG_SIDE": t["surface-2"], "BG_CARD": t["surface"], "BG_HEAD": t["bg-deep"],
        "BG_SOFT": t["surface-2"], "TOPBAR_BG": t["bg-deep"], "SIDEBAR_BG": t["surface-2"],
        "SURFACE_INK": t["bg-deep"], "FIELD_BG": t["input"], "ACCENT": t["accent"], "ACCENT_GREEN": t["success"],
        "ACCENT_MINT": t["success"], "ACCENT_RED": t["danger-text"], "FG": t["text"], "FG_DIM": t["text-muted"],
        "BG_LOG": t["bg-deep"], "WARN_BG": t["danger"], "CARD_BORDER": t["border"], "OUTLINE_SOFT": t["border"],
        "BG_HIGH": t["surface-raised"], "ON_PRIMARY_FIXED_VARIANT": t["primary"],
        # Workbench tokens
        "D_BG_DEEP": t["bg-deep"], "D_BG": t["bg"], "D_SURFACE": t["surface"], "D_SURFACE_2": t["surface-2"],
        "D_RAISED": t["surface-raised"], "D_INPUT": t["input"], "D_NAV_SEL": t["nav-selected"],
        "D_BORDER": t["border"], "D_ACCENT": t["accent"], "D_PRIMARY": t["primary"], "D_SUCCESS": t["success"],
        "D_SUCCESS_SURFACE": t["success-surface"], "D_DANGER": t["danger"], "D_DANGER_TEXT": t["danger-text"],
        "D_DANGER_SURFACE": t["danger-surface"], "D_TEXT": t["text"], "D_MUTED": t["text-muted"],
        "D_WARNING": t["warning"], "D_WARNING_SURFACE": t["warning-surface"], "D_TEXT_DISABLED": t["text-disabled"],
    }
    c.update(_extras(t, n))
    return c


def color_map(old: object, new: object) -> dict[str, str]:
    """Map every hex used by skin ``old`` to the matching hex in skin ``new`` (lower-case keys)."""
    a, b = panel_constants(old), panel_constants(new)
    out: dict[str, str] = {}
    for k, v in a.items():
        out.setdefault(v.lower(), b[k])
    return out
