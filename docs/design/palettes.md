# Transcription Workbench: palette options (4 user-selectable skins)

Figma file: https://www.figma.com/design/aYWsiZla4Zl2DkBEKTXY2B, page **Palette options** (`26:344`).

All four skins are complete **modes of the "Workbench tokens" collection**. The original mode (`3:0`, previously named "Dark") is renamed **"Harbor (Current)"** and its values are unchanged; it is still the collection default, so every existing frame looks exactly as before. Spacing/radius values are identical in all modes.

| Skin | Mode name / id | Mood | Page column | Dashboard copy | Screenshot |
|---|---|---|---|---|---|
| Current · **Harbor (Current)** | `Harbor (Current)` / `3:0` | The current Workbench blue: cool slate surfaces with a sky-blue accent. | `26:345` | `26:768` | `palette-current.png` |
| Palette 1 · **Midnight** | `Midnight` / `25:0` | A refined Harbor: deep navy-ink surfaces, a calmer azure accent and crisper borders. | `26:430` | `26:1005` | `palette-1.png` |
| Palette 2 · **Graphite** | `Graphite` / `25:1` | Warm neutral graphite with a sea-glass teal accent; quiet, editorial and easy on long sessions. | `26:515` | `26:1242` | `palette-2.png` |
| Palette 3 · **Aurora** | `Aurora` / `25:2` | The bold brand skin: near-black plum surfaces with an electric violet accent that reads as Aeonimus. | `26:600` | `26:1479` | `palette-3.png` |

Overview (all four side by side): `overview.png`. Settings with the new Appearance group: `settings-appearance-1000x1024.png` (node `26:1716`, Theme dropdown open) and `settings-appearance-1280x720.png` (node `26:2024`).

Each Dashboard copy is a clone of `7:2` with the skin mode set explicitly on its column. In the copies only, the hard-coded icon colours were re-bound to the matching tokens, so icons follow the skin as well. The originals were not touched.

## 1. Hex values per skin

| Token | Harbor (Current) | Midnight | Graphite | Aurora |
|---|---|---|---|---|
| `color/bg-deep` | `#0c0e0f` | `#080b11` | `#0e0d0c` | `#0b0a10` |
| `color/bg` | `#121415` | `#0d1118` | `#141312` | `#110f17` |
| `color/surface` | `#1e2021` | `#151b25` | `#1d1c1a` | `#1a1722` |
| `color/surface-2` | `#1a1c1d` | `#111620` | `#191817` | `#16131d` |
| `color/surface-raised` | `#282a2b` | `#1d2431` | `#272523` | `#231f2d` |
| `color/input` | `#333536` | `#232c3b` | `#2f2d2a` | `#2c2738` |
| `color/nav-selected` | `#07152a` | `#13254a` | `#0f2a27` | `#2a1d55` |
| `color/border` | `#3e4851` | `#3a4658` | `#48443f` | `#4a4260` |
| `color/accent` | `#8dcdff` | `#7ab8ff` | `#5fd3bc` | `#b69cff` |
| `color/primary` | `#004b70` | `#1a4280` | `#0d544b` | `#3d2a8e` |
| `color/success` | `#3ce36a` | `#4ade80` | `#7dd87a` | `#3ddc97` |
| `color/success-surface` | `#0f2a19` | `#0e2a1c` | `#15261a` | `#0f2a20` |
| `color/danger` | `#93000a` | `#992019` | `#952e1c` | `#9a1e33` |
| `color/danger-text` | `#ffb4ab` | `#ffb4ab` | `#ffb8a8` | `#ffb3c1` |
| `color/danger-surface` | `#2a1717` | `#2d1518` | `#2b1814` | `#2d1520` |
| `color/warning` | `#ffb84d` | `#fbbf24` | `#f2b45a` | `#ffc04d` |
| `color/warning-surface` | `#2b2112` | `#2a2110` | `#2a2114` | `#2c2213` |
| `color/text` | `#e2e2e3` | `#e6ebf2` | `#ece8e3` | `#ece9f5` |
| `color/text-muted` | `#bdc8d3` | `#a9b4c4` | `#bdb6ad` | `#b8b1cc` |
| `color/text-disabled` | `#6b7480` | `#5f6b7d` | `#6e6861` | `#665f7a` |

What changed from Harbor, in short:
- **Midnight:** blue-ink neutrals (#0d1118 bg, #151b25 card), a softer azure accent #7ab8ff, a deeper primary #1a4280 and a brighter nav highlight #13254a. Success #4ade80, warning #fbbf24, danger #992019.
- **Graphite:** warm graphite neutrals (#141312 bg, #1d1c1a card) with warm-grey text #ece8e3 / #bdb6ad and a sea-glass teal accent #5fd3bc on a #0d544b primary. Status colours are softened: sage success #7dd87a, honey warning #f2b45a, brick danger #952e1c.
- **Aurora:** plum-black neutrals (#110f17 bg, #1a1722 card), an electric violet accent #b69cff, a #3d2a8e primary and a #2a1d55 nav highlight. Mint success #3ddc97, gold warning #ffc04d, crimson danger #9a1e33.

## 2. WCAG contrast (sRGB relative luminance; ratios rounded to 2 places)

Targets: body text 4.5:1. Large text and UI graphics 3:1. All chip/label text in the app is 11–12 px, so every text pair is held to 4.5:1. The last two rows are recorded only. Disabled controls are exempt from WCAG, and card borders are decorative: cards and inputs are identified by their fill and label, and focus uses the accent ring, which does pass 3:1.

| Pair (fg on bg) | Target | Harbor (Current) | Midnight | Graphite | Aurora |
|---|---|---|---|---|---|
| Body text on background (`text` on `bg`) | 4.5:1 | 14.27 ✅ | 15.79 ✅ | 15.22 ✅ | 15.88 ✅ |
| Body text on card (`text` on `surface`) | 4.5:1 | 12.64 ✅ | 14.42 ✅ | 13.96 ✅ | 14.75 ✅ |
| Body text on secondary button (`text` on `surface-raised`) | 4.5:1 | 11.14 ✅ | 13.0 ✅ | 12.52 ✅ | 13.44 ✅ |
| Text in input (`text` on `input`) | 4.5:1 | 9.52 ✅ | 11.73 ✅ | 11.26 ✅ | 12.06 ✅ |
| Muted text on background (`text-muted` on `bg`) | 4.5:1 | 10.88 ✅ | 9.02 ✅ | 9.24 ✅ | 9.23 ✅ |
| Muted text on card (`text-muted` on `surface`) | 4.5:1 | 9.63 ✅ | 8.24 ✅ | 8.48 ✅ | 8.57 ✅ |
| Muted text on sidebar/table (`text-muted` on `surface-2`) | 4.5:1 | 10.07 ✅ | 8.64 ✅ | 8.83 ✅ | 8.91 ✅ |
| Accent text on card (`accent` on `surface`) | 4.5:1 | 9.59 ✅ | 8.33 ✅ | 9.35 ✅ | 7.72 ✅ |
| Selected nav label (`accent` on `nav-selected`) | 4.5:1 | 10.71 ✅ | 7.28 ✅ | 8.35 ✅ | 6.54 ✅ |
| Primary button label (`accent` on `primary`) | 4.5:1 | 5.49 ✅ | 4.74 ✅ | 4.83 ✅ | 4.79 ✅ |
| Success text on card (`success` on `surface`) | 4.5:1 | 9.65 ✅ | 9.92 ✅ | 9.71 ✅ | 9.99 ✅ |
| Success chip label (`success` on `success-surface`) | 4.5:1 | 9.07 ✅ | 8.82 ✅ | 9.04 ✅ | 8.66 ✅ |
| Danger button label (`danger-text` on `danger`) | 4.5:1 | 5.51 ✅ | 4.8 ✅ | 4.72 ✅ | 4.78 ✅ |
| Danger chip label (`danger-text` on `danger-surface`) | 4.5:1 | 10.03 ✅ | 10.04 ✅ | 10.19 ✅ | 10.06 ✅ |
| Warning strip / chip label (`warning` on `warning-surface`) | 4.5:1 | 9.19 ✅ | 9.51 ✅ | 8.64 ✅ | 9.59 ✅ |
| Warning text on card (`warning` on `surface`) | 4.5:1 | 9.52 ✅ | 10.35 ✅ | 9.29 ✅ | 10.84 ✅ |
| UI: focus ring / checkbox / selected border vs card (`accent` on `surface`) | 3.0:1 | 9.59 ✅ | 8.33 ✅ | 9.35 ✅ | 7.72 ✅ |
| UI: progress fill vs track (`accent` on `surface-2`) | 3.0:1 | 10.03 ✅ | 8.73 ✅ | 9.74 ✅ | 8.02 ✅ |
| Large: page title on background (`text` on `bg`) | 3.0:1 | 14.27 ✅ | 15.79 ✅ | 15.22 ✅ | 15.88 ✅ |
| Disabled label (WCAG-exempt, recorded only) (`text-disabled` on `surface-2`) | record | 3.61 | 3.35 | 3.22 | 3.04 |
| Card/input border vs card (decorative; recorded only) (`border` on `surface`) | record | 1.75 | 1.81 | 1.76 | 1.88 |

**Result:** every required pair passes in all four skins. To get there, the primary and danger button fills in the three new skins were darkened until their labels reached at least 4.7:1. Harbor already passed. The disabled label sits at 3.0–3.6:1, which is above 3:1 even though it is exempt. Borders are about 1.8:1 everywhere, as in today's design. If you ever need visible input boundaries at 3:1, add a `color/border-strong` token for inputs only rather than brightening every card.

## 3. Appearance setting (Settings)

- New **Appearance** card (Settings copies on the Palette options page; the original Settings frames `18:2` / `18:310` are untouched): at 1000 it sits under Advanced in the right column; compact puts it at the top of the third column so it is visible without scrolling.
- **Theme** dropdown listing the 4 skins (Harbor (default), Midnight, Graphite, Aurora). Each option shows a 4-colour mini swatch (bg, surface, accent, primary) with a check on the selected one.
- **Swatch preview** under the field. It shows the chosen skin's bg, surface, border, accent, primary, success, warning and danger: 20 px swatches at 1000 and 12 px in compact.
- Saved through the existing sticky Save bar. Changing the theme marks the field dirty (amber dot) and adds "Theme" to "Unsaved changes (4)". Discard reverts it.
- Help: "Applies to the whole app when you click Save changes. Default: Harbor."

## 4. Tk implementation note

**One dict per skin, keyed by token name** (the hex tables above, verbatim):

```python
# theme.py
SKINS = {
    "harbor":   {"bg-deep": "#0c0e0f", "bg": "#121415", ...},   # default
    "midnight": {"bg-deep": "#080b11", "bg": "#0d1118", ...},
    "graphite": {"bg-deep": "#0e0d0c", "bg": "#141312", ...},
    "aurora":   {"bg-deep": "#0b0a10", "bg": "#110f17", ...},
}
SKIN_LABELS = {"harbor": "Harbor", "midnight": "Midnight", "graphite": "Graphite", "aurora": "Aurora"}
DEFAULT_SKIN = "harbor"
T = dict(SKINS[DEFAULT_SKIN])   # the live token dict every widget reads from
```

- **Persist it in the config** as `"ui_theme": "harbor"` in `watcher_settings.json`, next to the other settings. The Theme combobox writes it only when Save changes is clicked. Load it at startup before any widget is created. An unknown value falls back to `harbor`.
- **Never hard-code hex in widgets.** Read `T["accent"]` and so on. Register each classic Tk widget with the token names it uses, e.g. `themed(widget, bg="surface", fg="text", highlightbackground="border")`, which stores the mapping on the widget (`widget._tokens = {...}`). Icons should be drawn from token colours too (or keep one PNG set per skin).
- **Re-apply on change (live, recommended):**
  1. `T.clear(); T.update(SKINS[name])`
  2. Restyle ttk: rebuild every `ttk.Style().configure(...)` / `.map(...)` call from `T` (put them in one `apply_ttk_styles(style, T)` function that startup also uses). This covers TButton/Accent/Danger/Warning, Treeview + tags (`sel`, `warn`), TCombobox, TSpinbox, TCheckbutton, TScrollbar and the segmented Toolbutton.
  3. Walk the widget tree from the root (`for w in walk(root)`, using `winfo_children()` recursively). For each widget with `_tokens`, call `w.configure(**{opt: T[tok] for opt, tok in w._tokens.items()})`. Also re-apply `tag_configure` on Text and Treeview widgets, and redraw Canvas items (chart, progress bars, dots).
  4. Call `root.update_idletasks()`. With a few hundred widgets this takes well under a second.
- **Fallback, apply on restart:** if a screen can't be restyled live (for example images baked at startup), save the value and show "Theme changes apply after restart" next to the dropdown, then build everything from `T` on the next launch. The design works either way; live is nicer and the walk above is cheap.

## 5. Notes

- The Figma file now has two pages: Page 1 (all original frames, unchanged) and **Palette options**.
- The Settings copies keep the content of the current Settings frames (version label, overrides) apart from the new Appearance group. Forge's settled decisions are recorded in `screens_redesign_spec.md` (spec notes only, as asked). Frames were not changed.