"""Desktop control panel for the transcription folder watcher (dark dashboard shell)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

import psutil

import threading

import themes
import transcript_search
import youtube_intake
import youtube_captions
import coverage_check
import aeonimus_handoff
from app_version import APP_NAME, APP_VERSION
import queue_control
import transcript_review
import support_tools
from output_paths import (
    resolved_output_dirs,
    resolved_completed_csv_dir,
    resolved_completed_json_dir,
    resolved_completed_transcript_dir,
)
from post_transcript_handoff import (
    build_handoff_bundle,
    handoff_llm_browser,
    handoff_notebooklm,
    handoff_open_folder,
    handoff_try_llm_api,
    write_api_handoff_artifact,
)
from safe_io import atomic_write_json
from watcher_telemetry import sample_snapshot
from watcher_settings import (
    load_settings,
    resolved_completed_dir,
    resolved_watch_dir,
    save_settings,
    settings_path,
)

ROOT = Path(__file__).resolve().parent
ASSETS_DIR = ROOT / "assets"
ICON_PNG = ASSETS_DIR / "watcher_desktop_icon.png"
START_SCRIPT = ROOT / "start_watcher.ps1"
STOP_SCRIPT = ROOT / "stop_watcher.ps1"
SINGLE_INSTANCE_PORT = 45219

# Kinetic Console palette — matched to .archive/assets/kinetic_console_reference/code_DesignGuide.html
# Colours come from the selected skin (themes.py); Harbor is the default.
_C = themes.panel_constants(load_settings(ROOT).get("theme"))
PRIMARY_HOVER = _C["PRIMARY_HOVER"]
DANGER_HOVER = _C["DANGER_HOVER"]
WARNING_HOVER = _C["WARNING_HOVER"]
DANGER_ACTIVE = _C["DANGER_ACTIVE"]
CARD_HILITE = _C["CARD_HILITE"]
ROW_ERROR = _C["ROW_ERROR"]
ROW_WARN = _C["ROW_WARN"]
BG_MAIN = _C["BG_MAIN"]
BG_SIDE = _C["BG_SIDE"]
BG_CARD = _C["BG_CARD"]
BG_HEAD = _C["BG_HEAD"]
BG_SOFT = _C["BG_SOFT"]
TOPBAR_BG = _C["TOPBAR_BG"]
SIDEBAR_BG = _C["SIDEBAR_BG"]
SURFACE_INK = _C["SURFACE_INK"]
FIELD_BG = _C["FIELD_BG"]

ACCENT = _C["ACCENT"]
ACCENT_GREEN = _C["ACCENT_GREEN"]
ACCENT_MINT = _C["ACCENT_MINT"]
ACCENT_RED = _C["ACCENT_RED"]
ACCENT_GOLD = _C["ACCENT_GOLD"]

FG = _C["FG"]
FG_DIM = _C["FG_DIM"]

BG_LOG = _C["BG_LOG"]
WARN_BG = _C["WARN_BG"]
CARD_BORDER = _C["CARD_BORDER"]
OUTLINE_SOFT = _C["OUTLINE_SOFT"]
BG_HIGH = _C["BG_HIGH"]
OUTLINE = _C["OUTLINE"]
PRIMARY_FIXED = _C["PRIMARY_FIXED"]
ON_PRIMARY_FIXED_VARIANT = _C["ON_PRIMARY_FIXED_VARIANT"]
INVERSE_PRIMARY = _C["INVERSE_PRIMARY"]
SECONDARY_CONTAINER = _C["SECONDARY_CONTAINER"]
ON_ERROR_CONTAINER = _C["ON_ERROR_CONTAINER"]

FONT_HEADLINE = "Bahnschrift"
FONT_BODY = "Segoe UI Variable"
FONT_BODY_FALLBACK = "Segoe UI"
FONT_MONO = "Cascadia Mono"

# Dashboard redesign tokens (Figma "Workbench tokens", Dark) - px sizes via negative Tk font sizes
D_BG_DEEP = _C["D_BG_DEEP"]
D_BG = _C["D_BG"]
D_SURFACE = _C["D_SURFACE"]
D_SURFACE_2 = _C["D_SURFACE_2"]
D_RAISED = _C["D_RAISED"]
D_INPUT = _C["D_INPUT"]
D_NAV_SEL = _C["D_NAV_SEL"]
D_BORDER = _C["D_BORDER"]
D_ACCENT = _C["D_ACCENT"]
D_PRIMARY = _C["D_PRIMARY"]
D_SUCCESS = _C["D_SUCCESS"]
D_SUCCESS_SURFACE = _C["D_SUCCESS_SURFACE"]
D_DANGER = _C["D_DANGER"]
D_DANGER_TEXT = _C["D_DANGER_TEXT"]
D_DANGER_SURFACE = _C["D_DANGER_SURFACE"]
D_TEXT = _C["D_TEXT"]
D_MUTED = _C["D_MUTED"]
# 1.3.0 token additions (Figma VariableID:8:2-8:4)
D_WARNING = _C["D_WARNING"]
D_WARNING_SURFACE = _C["D_WARNING_SURFACE"]
D_TEXT_DISABLED = _C["D_TEXT_DISABLED"]
F_APP = ("Segoe UI", -16, "bold")
F_PAGE = ("Segoe UI Semibold", -20)
F_SECTION = ("Segoe UI Semibold", -14)
F_BODY = ("Segoe UI", -12)
F_NAV = ("Segoe UI Semibold", -12)
F_LABEL = ("Segoe UI Semibold", -11)
F_VALUE_LG = ("Consolas", -20, "bold")
F_VALUE = ("Consolas", -12)
F_MONO_LABEL = ("Consolas", -11)

NAV_KEYS = ("dashboard", "queue", "monitor", "logs", "live_transcribe", "handoff", "search", "review", "settings")
MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
    ".m4v",
    ".mpeg",
    ".mpg",
    ".wmv",
    ".flv",
    ".mp3",
    ".m4a",
    ".wma",
    ".flac",
    ".ogg",
    ".wav",
}
IGNORE_SUFFIXES = (".tmp", ".temp", ".part", ".crdownload", ".download", ".partial")


def acquire_single_instance_socket() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def process_alive(pid: int) -> bool:
    return bool(pid) and psutil.pid_exists(int(pid))


def open_path(path: Path) -> None:
    if path.is_dir():
        os.startfile(path)  # noqa: S606
    elif path.is_file():
        os.startfile(path)  # noqa: S606


def format_watcher_error(error: object) -> str:
    text = str(error or "").strip()
    if not text:
        return "—"
    lowered = text.lower()
    if "transcription_returned_no_segments" in lowered:
        return "Whisper returned no transcript segments for this file."
    if "transcript_outputs_missing" in lowered:
        return "Transcription finished without writing transcript/CSV/JSON outputs."
    if "transcription_worker_exit_3221226505" in lowered:
        return "Transcription worker crashed during GPU/CUDA processing."
    if "transcription_worker_exit_3221225477" in lowered:
        return "Transcription worker crashed because a required runtime/DLL dependency was missing."
    if text.startswith("RuntimeError: "):
        return text[len("RuntimeError: ") :]
    return text


def age_label(iso_utc: object) -> str:
    text = str(iso_utc or "").strip()
    if not text or text == "—":
        return "—"
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return text
    delta = datetime.utcnow() - dt
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"


class MiniBar(tk.Canvas):
    """8 px counter bar (track surface-2 + 1 px border, accent fill). Accepts bar["maximum"]/bar["value"]."""

    def __init__(self, master: tk.Misc, *, fill: str, bg: str) -> None:
        super().__init__(master, width=40, height=8, bg=bg, highlightthickness=0, bd=0)
        self._fill = fill
        self._vals = {"maximum": 1.0, "value": 0.0}
        self.bind("<Configure>", lambda _e: self._redraw(), add="+")

    def __setitem__(self, key, value):  # noqa: ANN001
        if key in ("maximum", "value"):
            try:
                self._vals[key] = float(value)
            except (TypeError, ValueError):
                self._vals[key] = 0.0
            self._redraw()
        else:
            super().__setitem__(key, value)

    def __getitem__(self, key):  # noqa: ANN001
        if key in ("maximum", "value"):
            return self._vals[key]
        return super().__getitem__(key)

    def _redraw(self) -> None:
        self.delete("all")
        w = max(2, self.winfo_width())
        self.create_rectangle(0, 0, w - 1, 7, fill=D_SURFACE_2, outline=D_BORDER)
        mx = self._vals["maximum"] or 1.0
        frac = max(0.0, min(1.0, self._vals["value"] / mx))
        if frac > 0:
            self.create_rectangle(1, 1, max(2, int((w - 1) * frac)), 6, fill=self._fill, outline="")


class ScrollablePage(tk.Frame):
    """A page that scrolls vertically when its content is taller than the window.

    Content goes in ``.inner``. When everything fits, the inner frame is stretched to the
    window height so pages with expanding text areas still fill the space.
    """

    SELF_SCROLLING = {"Text", "Listbox", "Treeview", "Canvas", "TCombobox", "Spinbox", "TSpinbox"}

    def __init__(self, master: tk.Misc, *, bg: str) -> None:
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.inner = tk.Frame(self.canvas, bg=bg, padx=16, pady=16)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.vbar.grid_remove()
        self._bar_shown = False
        self.inner.bind("<Configure>", self._sync, add="+")
        self.canvas.bind("<Configure>", self._sync, add="+")

    def _sync(self, _event: tk.Event | None = None) -> None:
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        h = max(ch, self.inner.winfo_reqheight())
        self.canvas.itemconfigure(self._win, width=cw, height=h)
        self.canvas.configure(scrollregion=(0, 0, cw, h))
        need = self.inner.winfo_reqheight() > ch + 1
        if need != self._bar_shown:
            self._bar_shown = need
            if need:
                self.vbar.grid()
            else:
                self.vbar.grid_remove()
                self.canvas.yview_moveto(0)

    def needs_scroll(self) -> bool:
        return self.inner.winfo_reqheight() > self.canvas.winfo_height() + 1

    def on_mousewheel(self, event: tk.Event) -> None:
        w = event.widget
        if not isinstance(w, tk.Misc) or not str(w).startswith(str(self)):
            return
        try:
            if w is not self.canvas and w.winfo_class() in self.SELF_SCROLLING:
                return
        except tk.TclError:
            return
        if not self.needs_scroll():
            return
        steps = int(-event.delta / 120) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(steps, "units")


class ControlPanel(tk.Tk):
    def __init__(self, lock_sock: socket.socket) -> None:
        super().__init__()
        self.lock_sock = lock_sock
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1350, max(900, sw - 40))}x{min(1350, max(600, sh - 80))}")
        self.minsize(900, 600)
        self.configure(bg=BG_MAIN)

        self._settings = load_settings(ROOT)
        self._current_nav = "dashboard"
        self._nav_buttons: dict[str, tk.Label] = {}

        self.summary_var = tk.StringVar(value="Starting…")
        self.phase_var = tk.StringVar(value="—")
        self.health_var = tk.StringVar(value="—")
        self.detail_var = tk.StringVar(value="")
        self.node_status_var = tk.StringVar(value="SYSTEM: —")
        self.file_var = tk.StringVar(value="—")
        self.heartbeat_var = tk.StringVar(value="—")
        self.progress_var = tk.StringVar(value="—")
        self.segment_var = tk.StringVar(value="—")
        self.pid_var = tk.StringVar(value="—")
        self.worker_pid_var = tk.StringVar(value="—")
        self.error_var = tk.StringVar(value="—")
        self.active_jobs_var = tk.StringVar(value="—")
        self.queue_var = tk.IntVar(value=0)
        self.completed_var = tk.IntVar(value=0)
        self.failed_var = tk.IntVar(value=0)
        self.processed_var = tk.IntVar(value=0)
        self.auto_refresh_var = tk.BooleanVar(value=True)
        self.queue_meta_var = tk.StringVar(value="")
        self.monitor_hint_var = tk.StringVar(value="")
        self.log_status_var = tk.StringVar(value="Live updates on")
        self.logs_hint_var = tk.StringVar(value="")
        self.live_transcribe_hint_var = tk.StringVar(value="")
        self.log_filter_var = tk.StringVar(value="")
        self.queue_active_var = tk.StringVar(value="0")
        self.queue_depth_var = tk.StringVar(value="0")
        self.queue_throughput_var = tk.StringVar(value="0.00 items/min")
        self.queue_health_var = tk.StringVar(value="STANDBY")
        self.monitor_cpu_var = tk.StringVar(value="--%")
        self.monitor_mem_var = tk.StringVar(value="--%")
        self.monitor_gpu_var = tk.StringVar(value="--")
        self.monitor_disk_var = tk.StringVar(value="watch_resource.jsonl")
        self.handoff_destination_var = tk.StringVar(value="NotebookLM")
        self.handoff_format_var = tk.StringVar(value="Raw text")
        self._refresh_job: str | None = None
        self.youtube = youtube_intake.YouTubeDownloader(log_path=ROOT / "logs" / "youtube_intake.log")
        self.youtube.captions_fn = self._youtube_captions
        self.easy_status_var = tk.StringVar(
            value="One click: picks up the newest link list from the URL Lister, downloads the audio and starts transcribing."
        )
        self._easy_status_labels: list[tk.Label] = []
        try:
            _cfg = aeonimus_handoff.load_config()
        except Exception:  # noqa: BLE001
            _cfg = aeonimus_handoff.HandoffConfig(aeonimus_handoff.default_inbox(), aeonimus_handoff.default_library())
        self.set_library_dir = tk.StringVar(value=str(_cfg.library))
        self.handoff_inbox_var = tk.StringVar(value=str(_cfg.inbox))

        self.set_hb = tk.DoubleVar(value=float(self._settings["heartbeat_seconds"]))
        self.set_stall = tk.DoubleVar(value=float(self._settings["stall_timeout_seconds"]))
        self.set_jobs = tk.IntVar(value=int(self._settings["max_concurrent_jobs"]))
        self.set_profile = tk.StringVar(value=str(self._settings.get("log_profile", "standard")))
        self.set_scan = tk.BooleanVar(value=bool(self._settings.get("start_scan_existing", True)))
        self.set_watch_dir = tk.StringVar(value=str(self._settings.get("watch_dir", "watch_incoming")))
        self.set_completed_dir = tk.StringVar(
            value=str(self._settings.get("completed_video_dir") or self._settings.get("completed_dir", ""))
        )
        self.set_handoff_api = tk.BooleanVar(value=bool(self._settings.get("handoff_try_api_first", False)))
        self.set_whisper_gpu = tk.StringVar(value=str(self._settings.get("whisper_gpu_model", "large-v3")))
        self.set_whisper_cpu = tk.StringVar(value=str(self._settings.get("whisper_cpu_model", "medium")))
        self.set_whisper_profile = tk.StringVar(value=self._profile_label(self._settings.get("whisper_decode_profile", "auto")))
        self.set_prefer_captions = tk.BooleanVar(value=bool(self._settings.get("prefer_youtube_captions", True)))
        self.set_whisper_language = tk.StringVar(value=str(self._settings.get("whisper_language", "auto")))
        _engine = str(self._settings.get("asr_engine", "whisper"))
        self.set_asr_engine = tk.StringVar(value=_engine if _engine in ("whisper", "voxtral-mini-3b", "granite-2b") else "whisper")
        self.set_asr_crosscheck = tk.BooleanVar(value=bool(self._settings.get("asr_crosscheck", False)))
        self.set_whisper_batched = tk.BooleanVar(value=bool(self._settings.get("whisper_batched", False)))
        self.set_skip_duplicates = tk.BooleanVar(value=bool(self._settings.get("skip_duplicates", True)))
        self.set_completed_audio_dir = tk.StringVar(value=str(self._settings.get("completed_audio_dir", "completed_audio")))
        self._skin = themes.normalize(self._settings.get("theme"))
        self.set_theme = tk.StringVar(value=self._skin)

        self._handoff_initialized = False
        self._last_handoff_completed_path: str | None = None

        self._apply_theme()
        self._set_window_icon()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh()

    def _on_close(self) -> None:
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
        try:
            self.lock_sock.close()
        except OSError:
            pass
        self.destroy()

    def _set_window_icon(self) -> None:
        if not ICON_PNG.is_file():
            return
        try:
            self._icon_img = tk.PhotoImage(file=str(ICON_PNG))  # noqa: SLF001
            self.iconphoto(True, self._icon_img)
        except tk.TclError:
            pass

    def _apply_theme(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG_MAIN, foreground=FG, fieldbackground=FIELD_BG)
        style.configure("TFrame", background=BG_MAIN)
        style.configure("Card.TFrame", background=BG_CARD)
        style.configure("TLabel", background=BG_MAIN, foreground=FG)
        style.configure("Card.TLabel", background=BG_CARD, foreground=FG)
        style.configure("Dim.TLabel", background=BG_MAIN, foreground=FG_DIM)
        style.configure(
            "TButton",
            background=BG_HIGH,
            foreground=FG,
            bordercolor=CARD_BORDER,
            lightcolor=BG_HIGH,
            darkcolor=BG_HIGH,
            padding=(12, 8),
        )
        style.map(
            "TButton",
            background=[("active", SECONDARY_CONTAINER), ("pressed", SECONDARY_CONTAINER)],
            foreground=[("active", FG)],
        )
        style.configure(
            "Accent.TButton",
            background=ON_PRIMARY_FIXED_VARIANT,
            foreground=PRIMARY_FIXED,
            bordercolor=ACCENT,
            lightcolor=ON_PRIMARY_FIXED_VARIANT,
            darkcolor=ON_PRIMARY_FIXED_VARIANT,
        )
        style.map(
            "Accent.TButton",
            background=[("active", INVERSE_PRIMARY), ("pressed", INVERSE_PRIMARY)],
            foreground=[("active", PRIMARY_FIXED)],
        )
        style.configure(
            "Danger.TButton",
            background=WARN_BG,
            foreground=ON_ERROR_CONTAINER,
            bordercolor=ACCENT_RED,
            lightcolor=WARN_BG,
            darkcolor=WARN_BG,
        )
        style.map(
            "Danger.TButton",
            background=[("active", DANGER_ACTIVE), ("pressed", DANGER_ACTIVE)],
            foreground=[("active", ON_ERROR_CONTAINER)],
        )
        style.configure("TCheckbutton", background=BG_CARD, foreground=FG)
        style.map("TCheckbutton", background=[("active", BG_CARD)], foreground=[("active", FG)])
        style.configure(
            "TCombobox",
            fieldbackground=FIELD_BG,
            background=FIELD_BG,
            foreground=FG,
            arrowcolor=ACCENT,
            bordercolor=CARD_BORDER,
            lightcolor=FIELD_BG,
            darkcolor=FIELD_BG,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", FIELD_BG), ("!readonly", FIELD_BG)],
            foreground=[("readonly", FG)],
            background=[("active", BG_HIGH)],
        )
        style.configure(
            "TEntry",
            fieldbackground=FIELD_BG,
            foreground=FG,
            bordercolor=CARD_BORDER,
            lightcolor=FIELD_BG,
            darkcolor=FIELD_BG,
            insertcolor=FG,
        )
        style.configure(
            "TSpinbox",
            fieldbackground=FIELD_BG,
            foreground=FG,
            background=FIELD_BG,
            bordercolor=CARD_BORDER,
            arrowcolor=ACCENT,
            insertcolor=FG,
        )
        style.configure(
            "Vertical.TScrollbar",
            background=BG_HIGH,
            troughcolor=BG_HEAD,
            bordercolor=CARD_BORDER,
            arrowcolor=ACCENT,
            darkcolor=BG_HIGH,
            lightcolor=BG_HIGH,
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=BG_SOFT,
            background=ACCENT,
            bordercolor=CARD_BORDER,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=18,
        )
        style.configure(
            "Treeview",
            background=BG_LOG,
            foreground=FG,
            fieldbackground=BG_LOG,
            rowheight=28,
            bordercolor=CARD_BORDER,
        )
        style.configure(
            "Treeview.Heading",
            background=BG_HEAD,
            foreground=ACCENT,
            relief="flat",
            bordercolor=CARD_BORDER,
        )
        style.map(
            "Treeview",
            background=[("selected", SECONDARY_CONTAINER)],
            foreground=[("selected", FG)],
        )

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=BG_MAIN)
        root.pack(fill="both", expand=True)

        head = tk.Frame(root, bg=D_BG_DEEP, height=48)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text=APP_NAME, font=F_APP, fg=D_ACCENT, bg=D_BG_DEEP).pack(side="left", padx=(16, 12))
        tk.Label(head, text="Pipeline", font=F_NAV, fg=D_MUTED, bg=D_BG_DEEP).pack(side="left")
        self._hdr_status_lbl = tk.Label(head, textvariable=self.node_status_var, font=F_MONO_LABEL, fg=D_SUCCESS, bg=D_BG_DEEP)
        self._hdr_status_lbl.pack(side="right", padx=(0, 16))
        self._hdr_dot = tk.Canvas(head, width=8, height=8, bg=D_BG_DEEP, highlightthickness=0, bd=0)
        self._hdr_dot_id = self._hdr_dot.create_oval(0, 0, 8, 8, fill=D_SUCCESS, outline="")
        self._hdr_dot.pack(side="right", padx=(0, 8))

        body = tk.Frame(root, bg=D_BG)
        body.pack(fill="both", expand=True)

        side = tk.Frame(body, bg=D_SURFACE_2, width=176)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        tk.Label(side, text="VERSION", font=F_LABEL, fg=D_MUTED, bg=D_SURFACE_2).pack(anchor="w", padx=20, pady=(16, 0))
        tk.Label(side, text=f"v{APP_VERSION}", font=F_VALUE, fg=D_ACCENT, bg=D_SURFACE_2).pack(anchor="w", padx=20, pady=(4, 16))

        nav_items = [
            ("Dashboard", "dashboard"),
            ("Queue", "queue"),
            ("Monitor", "monitor"),
            ("Logs", "logs"),
            ("Live Transcribe", "live_transcribe"),
            ("Handoff", "handoff"),
            ("Search", "search"),
            ("Review", "review"),
            ("Settings", "settings"),
        ]
        for text, key in nav_items:
            lbl = tk.Label(side, text=text.upper(), font=F_NAV, fg=D_MUTED, bg=D_SURFACE_2, anchor="w",
                           cursor="hand2", padx=12, pady=7)
            lbl.pack(fill="x", padx=8, pady=(0, 4))
            lbl.bind("<Button-1>", lambda _e, k=key: self._show_page(k))
            self._nav_buttons[key] = lbl

        tk.Label(side, text="", bg=D_SURFACE_2).pack(fill="both", expand=True)
        doc = tk.Label(side, text="DOCUMENTATION", font=F_NAV, fg=D_MUTED, bg=D_SURFACE_2, anchor="w",
                       cursor="hand2", padx=12, pady=7)
        doc.pack(fill="x", padx=8, pady=(0, 16))
        doc.bind("<Button-1>", lambda _e: open_path(ROOT / "README.md"))

        self.content_host = tk.Frame(body, bg=D_BG)
        self.content_host.pack(side="right", fill="both", expand=True)

        self.pages: dict[str, ScrollablePage] = {}
        for key in NAV_KEYS:
            f = ScrollablePage(self.content_host, bg=BG_MAIN)
            f.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = f
        # One mouse-wheel handler for the main window: scrolls the visible page
        # unless the pointer is over a widget that scrolls itself (text, lists, tables).
        self.bind("<MouseWheel>", self._route_mousewheel, add="+")
        self.content_host.grid_rowconfigure(0, weight=1)
        self.content_host.grid_columnconfigure(0, weight=1)

        self._page_layouts: list = []
        self._build_page_dashboard(self.pages["dashboard"].inner)
        self._apply_screen_styles()
        self._build_page_queue(self.pages["queue"].inner)
        self._build_page_monitor(self.pages["monitor"].inner)
        self._build_page_logs(self.pages["logs"].inner)
        self._build_page_live_transcribe(self.pages["live_transcribe"].inner)
        self._build_page_handoff(self.pages["handoff"].inner)
        self._build_page_search(self.pages["search"].inner)
        self._build_page_review(self.pages["review"].inner)
        self._build_page_settings(self.pages["settings"].inner)

        self._show_page("dashboard")

    def _route_mousewheel(self, event: tk.Event) -> None:
        page = self.pages.get(self._current_nav)
        if page is not None:
            page.on_mousewheel(event)

    def _autowrap(self, label: tk.Label, pad: int = 8) -> tk.Label:
        """Wrap a label to its real width so long text never forces the window wider."""
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(200, e.width - pad)), add="+")
        return label

    def _highlight_nav(self, key: str) -> None:
        for k, lbl in self._nav_buttons.items():
            if k == key:
                lbl.configure(bg=D_NAV_SEL, fg=D_ACCENT, highlightbackground=D_ACCENT, highlightthickness=1, pady=6)
            else:
                lbl.configure(bg=D_SURFACE_2, fg=D_MUTED, highlightthickness=0, pady=7)
        self._current_nav = key

    def _show_page(self, key: str) -> None:
        if key not in self.pages:
            return
        for f in self.pages.values():
            f.grid_remove()
        self.pages[key].grid()
        self._highlight_nav(key)
        self.refresh_once()

    def _card(self, parent: tk.Misc, *, expand: bool = False, autopack: bool = True, **pack_kw) -> tk.Frame:
        f = tk.Frame(parent, bg=BG_CARD, highlightthickness=1, highlightbackground=CARD_BORDER)
        if autopack:
            f.pack(fill="both", expand=expand, **pack_kw)
        return f

    def _headline(self, parent: tk.Misc, text: str, *, size: int = 14, fg: str = FG) -> tk.Label:
        lbl = tk.Label(parent, text=text, fg=fg, bg=parent.cget("bg"), font=(FONT_HEADLINE, size, "bold"))
        return lbl

    def _body_label(
        self,
        parent: tk.Misc,
        text: str = "",
        *,
        fg: str = FG_DIM,
        size: int = 10,
        mono: bool = False,
        bold: bool = False,
        textvariable: tk.Variable | None = None,
        anchor: str = "w",
        justify: str = "left",
        wraplength: int | None = None,
    ) -> tk.Label:
        family = FONT_MONO if mono else FONT_BODY
        weight = "bold" if bold else "normal"
        lbl = tk.Label(
            parent,
            text=text,
            textvariable=textvariable,
            fg=fg,
            bg=parent.cget("bg"),
            font=(family, size, weight),
            anchor=anchor,
            justify=justify,
        )
        if wraplength is not None:
            lbl.configure(wraplength=wraplength)
        return lbl

    def _section_title(
        self,
        parent: tk.Misc,
        eyebrow: str,
        title: str,
        subtitle: str,
    ) -> None:
        self._body_label(parent, eyebrow, fg=ACCENT, mono=True, size=10, bold=True).pack(anchor="w")
        self._headline(parent, title, size=20).pack(anchor="w", pady=(8, 0))
        self._body_label(parent, subtitle, fg=FG_DIM, size=10).pack(anchor="w", pady=(6, 0))

    def _metric_tile(
        self,
        parent: tk.Misc,
        title: str,
        var: tk.Variable,
        *,
        color: str = ACCENT,
    ) -> tk.Frame:
        tile = tk.Frame(parent, bg=BG_SOFT, highlightthickness=1, highlightbackground=CARD_BORDER)
        tk.Label(tile, text=title.upper(), fg=FG_DIM, bg=BG_SOFT, font=(FONT_HEADLINE, 8, "bold")).pack(
            anchor="w", padx=12, pady=(10, 2)
        )
        tk.Label(tile, textvariable=var, fg=color, bg=BG_SOFT, font=(FONT_MONO, 18, "bold")).pack(
            anchor="w", padx=12, pady=(0, 10)
        )
        return tile

    def _stat_chip(self, parent: tk.Misc, title: str, var: tk.Variable, *, color: str = ACCENT) -> tk.Frame:
        """Compact dashboard stat: label on the left, number on the right, one short row."""
        chip = tk.Frame(parent, bg=BG_SOFT, highlightthickness=1, highlightbackground=CARD_BORDER)
        tk.Label(chip, text=title.upper(), fg=FG_DIM, bg=BG_SOFT, font=(FONT_HEADLINE, 8, "bold")).pack(
            side="left", padx=(10, 6), pady=5
        )
        tk.Label(chip, textvariable=var, fg=color, bg=BG_SOFT, font=(FONT_MONO, 13, "bold")).pack(
            side="right", padx=(6, 10), pady=3
        )
        return chip

    @staticmethod
    def _attach_scrollbars(text_widget: tk.Text, parent: tk.Misc) -> None:
        ybar = ttk.Scrollbar(parent, orient="vertical", command=text_widget.yview)
        xbar = ttk.Scrollbar(parent, orient="horizontal", command=text_widget.xview)
        text_widget.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        text_widget.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)

    # ---- Dashboard (Figma redesign, spec sections 4-7) ----
    def _apply_dash_styles(self) -> None:
        st = ttk.Style(self)
        base = dict(font=F_BODY, padding=(12, 6), relief="flat", borderwidth=1, focusthickness=0)
        st.configure("Dash.Primary.TButton", background=D_PRIMARY, foreground=D_ACCENT, bordercolor=D_ACCENT,
                     lightcolor=D_PRIMARY, darkcolor=D_PRIMARY, focuscolor=D_PRIMARY, **base)
        st.map("Dash.Primary.TButton", background=[("disabled", D_SURFACE_2), ("active", PRIMARY_HOVER)],
               foreground=[("disabled", D_MUTED)])
        st.configure("Dash.Secondary.TButton", background=D_RAISED, foreground=D_TEXT, bordercolor=D_BORDER,
                     lightcolor=D_RAISED, darkcolor=D_RAISED, focuscolor=D_RAISED, **base)
        st.map("Dash.Secondary.TButton", background=[("disabled", D_SURFACE_2), ("active", D_INPUT)],
               foreground=[("disabled", D_MUTED)])
        st.configure("Dash.Danger.TButton", background=D_DANGER, foreground=D_DANGER_TEXT, bordercolor=D_DANGER,
                     lightcolor=D_DANGER, darkcolor=D_DANGER, focuscolor=D_DANGER, **base)
        st.map("Dash.Danger.TButton", background=[("disabled", D_SURFACE_2), ("active", DANGER_HOVER)],
               foreground=[("disabled", D_MUTED)])
        small = dict(base, padding=(12, 4))
        st.configure("Dash.Small.TButton", background=D_RAISED, foreground=D_TEXT, bordercolor=D_BORDER,
                     lightcolor=D_RAISED, darkcolor=D_RAISED, focuscolor=D_RAISED, **small)
        st.map("Dash.Small.TButton", background=[("active", D_INPUT)])
        st.configure("Latest.Treeview", background=D_SURFACE, fieldbackground=D_SURFACE, foreground=D_TEXT,
                     rowheight=24, font=F_BODY, borderwidth=0, indent=0)
        st.map("Latest.Treeview", background=[("selected", D_RAISED)], foreground=[("selected", D_TEXT)])
        st.layout("Latest.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def _dcard(self, parent: tk.Misc, *, border: str = D_BORDER) -> tuple[tk.Frame, tk.Frame]:
        card = tk.Frame(parent, bg=D_SURFACE, highlightthickness=1, highlightbackground=border)
        inner = tk.Frame(card, bg=D_SURFACE)
        inner.pack(fill="both", expand=True, padx=12, pady=12)
        return card, inner

    def _dlabel(self, parent: tk.Misc, text: str = "", *, font=F_BODY, fg: str = D_TEXT,  # noqa: ANN001
                textvariable: tk.Variable | None = None, clip: bool = False) -> tk.Label:
        lbl = tk.Label(parent, text=text, textvariable=textvariable, font=font, fg=fg, bg=parent.cget("bg"), anchor="w",
                       justify="left", padx=0, pady=0, bd=0)
        if clip:
            lbl.configure(width=1)  # request no width: long values clip instead of widening the window
        return lbl

    def _divider(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(parent, bg=D_BORDER, width=1, height=24)

    def _badge(self, parent: tk.Misc) -> tk.Frame:
        b = tk.Frame(parent, bg=D_SUCCESS_SURFACE, highlightthickness=1, highlightbackground=D_SUCCESS)
        dot = tk.Canvas(b, width=8, height=8, bg=D_SUCCESS_SURFACE, highlightthickness=0, bd=0)
        dot_id = dot.create_oval(0, 0, 8, 8, fill=D_SUCCESS, outline="")
        dot.pack(side="left", padx=(12, 8), pady=10)
        lbl = tk.Label(b, textvariable=self.node_status_var, font=F_MONO_LABEL, fg=D_SUCCESS, bg=D_SUCCESS_SURFACE)
        lbl.pack(side="left", padx=(0, 12), pady=5)
        self._badge_parts.append((b, dot, dot_id, lbl))
        return b

    def _build_page_dashboard(self, parent: tk.Frame) -> None:
        self._apply_dash_styles()
        self._badge_parts: list = []
        self._dash_compact: bool | None = None
        outer = tk.Frame(parent, bg=D_BG)
        outer.pack(fill="both", expand=True)
        self._dash_outer = outer

        self.banner_frame = tk.Frame(outer, bg=WARN_BG)
        self.banner_label = tk.Label(self.banner_frame, text="", anchor="w", justify="left", wraplength=700,
                                     padx=12, pady=6, fg="#fff", bg=WARN_BG, font=F_BODY)
        self.banner_label.pack(fill="x")
        self._autowrap(self.banner_label, pad=28)

        # page header (tall) / top bar (compact)
        self._d_head = tk.Frame(outer, bg=D_BG)
        self._d_titleblock = tk.Frame(outer, bg=D_BG)
        self._d_crumb_row = tk.Frame(outer, bg=D_BG)
        self._d_title_row = tk.Frame(outer, bg=D_BG)
        self._d_crumb = self._dlabel(outer, "PIPELINE / MAIN", font=F_MONO_LABEL, fg=D_ACCENT)
        self._d_title = self._dlabel(outer, "Pipeline Watcher", font=F_PAGE)
        self._d_sub = self._dlabel(outer, "Watch folder \u2022 Whisper \u2022 Handoff", fg=D_MUTED)
        self._d_badge = self._badge(outer)

        # watcher controls bar
        self._d_ctrl, ci = self._dcard(outer)
        ci.pack_configure(pady=8)
        self._dlabel(ci, "Watcher Control", font=F_SECTION, fg=D_ACCENT).pack(side="left")
        self._divider(ci).pack(side="left", padx=12)
        self.power_btn = ttk.Button(ci, text="\u25b6  Start watcher", style="Dash.Primary.TButton",
                                    command=self._toggle_watcher_power)
        self.power_btn.pack(side="left")
        ttk.Button(ci, text="\u27f3  Refresh now", style="Dash.Secondary.TButton", command=self.refresh_once).pack(
            side="left", padx=(12, 0))
        self._divider(ci).pack(side="left", padx=12)
        self._auto_box = tk.Canvas(ci, width=16, height=16, bg=D_SURFACE, highlightthickness=0, bd=0, cursor="hand2")
        self._auto_box.pack(side="left")
        auto_lbl = self._dlabel(ci, "Auto-refresh")
        auto_lbl.configure(cursor="hand2")
        auto_lbl.pack(side="left", padx=(8, 0))
        self._poll_hint_var = tk.StringVar(value="")
        self._dlabel(ci, textvariable=self._poll_hint_var, font=F_VALUE, fg=D_MUTED).pack(side="left", padx=(8, 0))
        for w in (self._auto_box, auto_lbl):
            w.bind("<Button-1>", lambda _e: self.auto_refresh_var.set(not self.auto_refresh_var.get()))
        self.auto_refresh_var.trace_add("write", lambda *_a: self._draw_auto_refresh())
        self._draw_auto_refresh()

        # stat tiles
        self._d_stats = tk.Frame(outer, bg=D_BG)
        self._stat_values: dict[str, tk.Label] = {}
        stats = [("queue", "QUEUE", self.queue_var), ("completed", "COMPLETED", self.completed_var),
                 ("failed", "FAILED", self.failed_var), ("processed", "PROCESSED", self.processed_var),
                 ("segment", "SEGMENT (S)", self.segment_var)]
        for i, (key, title, var) in enumerate(stats):
            self._d_stats.columnconfigure(i, weight=1, uniform="tiles")
            tile = tk.Frame(self._d_stats, bg=D_SURFACE_2, highlightthickness=1, highlightbackground=D_BORDER)
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            tk.Label(tile, text=title, font=F_LABEL, fg=D_MUTED, bg=D_SURFACE_2, anchor="w").pack(fill="x", padx=12, pady=(8, 0))
            v = tk.Label(tile, textvariable=var, font=F_VALUE_LG, fg=D_ACCENT, bg=D_SURFACE_2, anchor="w", width=1)
            v.pack(fill="x", padx=12, pady=(0, 8))
            self._stat_values[key] = v

        # URL Lister + Add links cards
        self._d_url = self._build_easy_import(outer, compact=True, dash=True)
        self._d_links = youtube_intake.build_panel(
            outer, self.youtube, theme=self._youtube_theme(), get_dirs=self._youtube_dirs, compact=True,
            on_open_transcripts=self._open_library_transcripts,
        )

        # watcher status card
        self._d_status = tk.Frame(outer, bg=D_SURFACE, highlightthickness=1, highlightbackground=D_BORDER)
        sh = tk.Frame(self._d_status, bg=D_RAISED, height=40)
        sh.pack(fill="x")
        sh.pack_propagate(False)
        self._status_dot = tk.Canvas(sh, width=8, height=8, bg=D_RAISED, highlightthickness=0, bd=0)
        self._status_dot_id = self._status_dot.create_oval(0, 0, 8, 8, fill=D_SUCCESS, outline="")
        self._status_dot.pack(side="left", padx=(12, 8))
        tk.Label(sh, textvariable=self.summary_var, font=F_SECTION, fg=D_TEXT, bg=D_RAISED).pack(side="left")
        self._load_val = tk.Label(sh, textvariable=self.queue_health_var, font=F_MONO_LABEL, fg=D_SUCCESS, bg=D_RAISED)
        self._load_val.pack(side="right", padx=(0, 12))
        tk.Label(sh, text="LOAD:", font=F_MONO_LABEL, fg=D_MUTED, bg=D_RAISED).pack(side="right", padx=(0, 4))
        sb = tk.Frame(self._d_status, bg=D_SURFACE)
        sb.pack(fill="both", expand=True, padx=12, pady=12)
        grid = tk.Frame(sb, bg=D_SURFACE)
        grid.pack(fill="x")
        grid.columnconfigure(0, minsize=120)
        grid.columnconfigure(1, weight=1, uniform="stcol")
        grid.columnconfigure(2, weight=1, uniform="stcol")
        cols = [
            [("PHASE", self.phase_var), ("WORKER PID", self.worker_pid_var), ("SEGMENT END", self.segment_var)],
            [("CURRENT FILE", self.file_var), ("HEARTBEAT", self.heartbeat_var), ("ACTIVE JOBS", self.active_jobs_var)],
            [("WATCHER PID", self.pid_var), ("LAST PROGRESS", self.progress_var), ("LAST ERROR", self.error_var)],
        ]
        for c, fields in enumerate(cols):
            for r, (label, var) in enumerate(fields):
                cell = tk.Frame(grid, bg=D_SURFACE)
                cell.grid(row=r, column=c, sticky="ew", padx=(0 if c == 0 else 16, 0), pady=(0 if r == 0 else 8, 0))
                self._dlabel(cell, label, font=F_LABEL, fg=D_MUTED).pack(fill="x")
                self._dlabel(cell, textvariable=var, font=F_VALUE, clip=True).pack(fill="x")
        foot = tk.Frame(sb, bg=D_SURFACE)
        foot.pack(fill="x", pady=(8, 0))
        tk.Label(foot, textvariable=self.heartbeat_var, font=F_VALUE, fg=D_TEXT, bg=D_SURFACE, anchor="e").pack(side="right")
        tk.Label(foot, text="RUNTIME:", font=F_MONO_LABEL, fg=D_MUTED, bg=D_SURFACE).pack(side="right", padx=(12, 6))
        self._dlabel(foot, textvariable=self.detail_var, fg=D_MUTED, clip=True).pack(side="left", fill="x", expand=True)

        # historical counters card
        self._d_counters, cf = self._dcard(outer)
        self._dlabel(cf, "Historical Counters", font=F_SECTION).pack(fill="x", pady=(0, 8))
        bars = tk.Frame(cf, bg=D_SURFACE)
        bars.pack(fill="x")
        bars.columnconfigure(0, minsize=88)
        bars.columnconfigure(1, weight=1)
        self._counter_values: dict[str, tk.Label] = {}
        for r, (key, title, var) in enumerate([("queue", "QUEUE", self.queue_var), ("completed", "COMPLETED", self.completed_var),
                                               ("failed", "FAILED", self.failed_var), ("processed", "PROCESSED", self.processed_var)]):
            tk.Label(bars, text=title, font=F_LABEL, fg=D_MUTED, bg=D_SURFACE, anchor="w").grid(
                row=r, column=0, sticky="w", pady=(0 if r == 0 else 12, 0))
            bar = MiniBar(bars, fill=D_DANGER_TEXT if key == "failed" else D_ACCENT, bg=D_SURFACE)
            bar.grid(row=r, column=1, sticky="ew", padx=(12, 12), pady=(0 if r == 0 else 12, 0))
            setattr(self, f"{key}_bar", bar)
            v = tk.Label(bars, textvariable=var, font=F_VALUE, fg=D_MUTED, bg=D_SURFACE, anchor="e", width=4)
            v.grid(row=r, column=2, sticky="e", pady=(0 if r == 0 else 12, 0))
            self._counter_values[key] = v

        # latest transcripts card
        self._d_latest, lf = self._dcard(outer)
        lh = tk.Frame(lf, bg=D_SURFACE)
        lh.pack(fill="x", pady=(0, 8))
        self._dlabel(lh, "Latest transcripts", font=F_SECTION).pack(side="left")
        self._dlabel(lh, "Last 5 \u00b7 double-click to open", fg=D_MUTED).pack(side="right")
        self._build_latest_transcripts(lf)
        self.coverage_warning_var = tk.StringVar(value="")
        self._coverage_label = tk.Label(lf, textvariable=self.coverage_warning_var, fg=D_WARNING, bg=D_WARNING_SURFACE,
                                        anchor="w", justify="left", wraplength=420, font=F_MONO_LABEL, padx=8, pady=4,
                                        highlightthickness=1, highlightbackground=D_WARNING)
        self._autowrap(self._coverage_label, pad=20)

        # terminal quick actions
        self._d_quick, qf = self._dcard(outer)
        self._d_quick_title = self._dlabel(qf, "TERMINAL QUICK ACTIONS", font=F_MONO_LABEL, fg=D_MUTED)
        self._d_qrow1 = tk.Frame(qf, bg=D_SURFACE)
        self._d_qrow2 = tk.Frame(qf, bg=D_SURFACE)
        self._d_qbtns1 = [
            ttk.Button(qf, text="Open watch folder", style="Dash.Small.TButton", command=lambda: open_path(self._watch_dir())),
            ttk.Button(qf, text="Open videos", style="Dash.Small.TButton", command=lambda: open_path(self._completed_dir())),
            ttk.Button(qf, text="Open transcripts", style="Dash.Small.TButton", command=self._open_library_transcripts),
            ttk.Button(qf, text="Open CSV", style="Dash.Small.TButton", command=lambda: open_path(self._csv_dir())),
            ttk.Button(qf, text="Open JSON", style="Dash.Small.TButton", command=lambda: open_path(self._json_dir())),
        ]
        self._d_qbtns2 = [
            ttk.Button(qf, text="Reconcile finished job", style="Dash.Small.TButton", command=self._reconcile_finished_job),
            ttk.Button(qf, text="Clear last error", style="Dash.Small.TButton", command=self._clear_last_error),
        ]
        self.quick_actions_frame = self._d_quick

        # containers for the two arrangements
        self._d_lower = tk.Frame(outer, bg=D_BG)
        self._d_lower.columnconfigure(0, weight=1, uniform="lower")
        self._d_lower.columnconfigure(1, weight=1, uniform="lower")
        self._d_cols = tk.Frame(outer, bg=D_BG)
        self._d_cols.columnconfigure(0, weight=1)
        self._d_cols.columnconfigure(1, minsize=400)
        self._d_left = tk.Frame(outer, bg=D_BG)
        self._d_right = tk.Frame(outer, bg=D_BG)

        # containers were created after the cards; raise the cards so they draw above them
        for w in (self._d_url, self._d_links, self._d_status, self._d_counters, self._d_latest, self._d_ctrl):
            w.lift()
        self._layout_dashboard(compact=False)
        self.bind("<Configure>", self._maybe_relayout_dashboard, add="+")
        self._set_dashboard_state(running=False, stale=False)

    def _draw_auto_refresh(self) -> None:
        c = self._auto_box
        c.delete("all")
        on = bool(self.auto_refresh_var.get())
        if on:
            c.create_rectangle(0, 0, 15, 15, fill=D_ACCENT, outline=D_ACCENT)
            c.create_line(4, 8, 7, 11, 12, 4, fill=D_BG_DEEP, width=2)
            self._poll_hint_var.set("Polling every 1.0s")
        else:
            c.create_rectangle(0, 0, 15, 15, fill=D_SURFACE_2, outline=D_BORDER)
            self._poll_hint_var.set("Polling paused")

    def _maybe_relayout_dashboard(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        compact = event.width >= 1200 and event.height < 900
        if compact != self._dash_compact:
            self._layout_dashboard(compact=compact)
            self._rerun_layouts()

    @staticmethod
    def _forget(*widgets: tk.Misc) -> None:
        for w in widgets:
            w.pack_forget()
            w.grid_forget()

    def _layout_dashboard(self, *, compact: bool) -> None:
        self._dash_compact = compact
        banner_shown = self.banner_frame.winfo_ismapped()
        self._forget(self.banner_frame, self._d_head, self._d_titleblock, self._d_crumb_row, self._d_title_row,
                     self._d_crumb, self._d_title, self._d_sub, self._d_badge, self._d_ctrl, self._d_stats,
                     self._d_url, self._d_links, self._d_status, self._d_counters, self._d_latest, self._d_quick,
                     self._d_quick_title, self._d_qrow1, self._d_qrow2, self._d_lower, self._d_cols,
                     self._d_left, self._d_right, *self._d_qbtns1, *self._d_qbtns2)
        o = self._dash_outer
        gap = 12
        if banner_shown:
            self.banner_frame.pack(in_=o, fill="x", pady=(0, 10))
        if not compact:
            self._d_head.pack(in_=o, fill="x", pady=(0, gap))
            self._d_titleblock.pack(in_=self._d_head, side="left")
            self._d_crumb_row.pack(in_=self._d_titleblock, fill="x")
            self._d_title_row.pack(in_=self._d_titleblock, fill="x", pady=(4, 0))
            self._d_crumb.pack(in_=self._d_crumb_row, side="left")
            self._d_title.pack(in_=self._d_title_row, side="left")
            self._d_sub.pack(in_=self._d_title_row, side="left", padx=(12, 0), pady=(6, 0))
            self._d_badge.pack(in_=self._d_head, side="right")
            self._d_ctrl.pack(in_=o, fill="x", pady=(0, gap))
            self._d_stats.pack(in_=o, fill="x", pady=(0, gap))
            self._d_url.pack(in_=o, fill="x", pady=(0, gap))
            self._d_links.pack(in_=o, fill="x", pady=(0, gap))
            self._d_status.pack(in_=o, fill="x", pady=(0, gap))
            self._d_lower.pack(in_=o, fill="x", pady=(0, gap))
            self._d_counters.grid(in_=self._d_lower, row=0, column=0, sticky="nsew", padx=(0, 6))
            self._d_latest.grid(in_=self._d_lower, row=0, column=1, sticky="nsew", padx=(6, 0))
            self._d_quick.pack(in_=o, fill="x")
            self._d_quick_title.pack(fill="x", pady=(0, 8))
            self._d_qrow1.pack(fill="x", pady=(0, 8))
            self._d_qrow2.pack(fill="x")
            for i, b in enumerate(self._d_qbtns1):
                b.pack(in_=self._d_qrow1, side="left", padx=(0 if i == 0 else 8, 0))
            for i, b in enumerate(self._d_qbtns2):
                b.pack(in_=self._d_qrow2, side="left", padx=(0 if i == 0 else 8, 0))
        else:
            self._d_head.pack(in_=o, fill="x", pady=(0, gap))
            self._d_titleblock.pack(in_=self._d_head, side="left")
            self._d_crumb_row.pack(in_=self._d_titleblock, fill="x")
            self._d_title_row.pack(in_=self._d_titleblock, fill="x", pady=(4, 0))
            self._d_crumb.pack(in_=self._d_crumb_row, side="left")
            self._d_sub.pack(in_=self._d_crumb_row, side="left", padx=(12, 0))
            self._d_title.pack(in_=self._d_title_row, side="left")
            self._d_badge.pack(in_=self._d_title_row, side="left", padx=(12, 0))
            self._d_ctrl.pack(in_=self._d_head, side="left", fill="x", expand=True, padx=(16, 0))
            self._d_stats.pack(in_=o, fill="x", pady=(0, gap))
            self._d_cols.pack(in_=o, fill="x", pady=(0, gap))
            self._d_left.grid(in_=self._d_cols, row=0, column=0, sticky="nsew", padx=(0, gap))
            self._d_right.grid(in_=self._d_cols, row=0, column=1, sticky="nsew")
            self._d_url.pack(in_=self._d_left, fill="x", pady=(0, gap))
            self._d_links.pack(in_=self._d_left, fill="x", pady=(0, gap))
            self._d_status.pack(in_=self._d_left, fill="x")
            self._d_latest.pack(in_=self._d_right, fill="x", pady=(0, gap))
            self._d_counters.pack(in_=self._d_right, fill="both", expand=True)
            self._d_quick.pack(in_=o, fill="x")
            self._d_quick_title.pack(side="left", padx=(0, 16))
            self._d_qrow1.pack(side="left", fill="x", expand=True)
            for i, b in enumerate(self._d_qbtns1 + self._d_qbtns2):
                b.pack(in_=self._d_qrow1, side="left", padx=(0 if i == 0 else 8, 0))

    def _set_dashboard_state(self, *, running: bool, stale: bool) -> None:
        if running:
            self.power_btn.configure(text="\u25a0  Stop watcher", style="Dash.Danger.TButton")
        else:
            self.power_btn.configure(text="\u25b6  Start watcher", style="Dash.Primary.TButton")
        ok = running and not stale
        fg = D_SUCCESS if ok else D_DANGER_TEXT
        surface = D_SUCCESS_SURFACE if ok else D_DANGER_SURFACE
        for frame, dot, dot_id, lbl in getattr(self, "_badge_parts", []):
            frame.configure(bg=surface, highlightbackground=fg)
            dot.configure(bg=surface)
            dot.itemconfigure(dot_id, fill=fg)
            lbl.configure(bg=surface, fg=fg)
        if hasattr(self, "_hdr_dot"):
            self._hdr_dot.itemconfigure(self._hdr_dot_id, fill=fg)
            self._hdr_status_lbl.configure(fg=fg)
        if hasattr(self, "_status_dot"):
            self._status_dot.itemconfigure(self._status_dot_id, fill=fg)
            load = self.queue_health_var.get()
            self._load_val.configure(fg=D_SUCCESS if load == "ACTIVE" else (D_DANGER_TEXT if load in ("FAULT", "STALE") else D_MUTED))
        self._color_counts()

    def _color_counts(self) -> None:
        if not hasattr(self, "_stat_values"):
            return
        def num(var: tk.Variable) -> float:
            try:
                return float(var.get())
            except (tk.TclError, ValueError, TypeError):
                return 0.0
        failed = num(self.failed_var)
        self._stat_values["failed"].configure(fg=D_DANGER_TEXT if failed > 0 else D_MUTED)
        self._stat_values["completed"].configure(fg=D_SUCCESS)
        for key, var in (("queue", self.queue_var), ("completed", self.completed_var),
                         ("failed", self.failed_var), ("processed", self.processed_var)):
            v = num(var)
            color = (D_DANGER_TEXT if key == "failed" else D_ACCENT) if v > 0 else D_MUTED
            self._counter_values[key].configure(fg=color)

    def _youtube_theme(self) -> dict:
        return {
            "card": BG_CARD, "fg": FG, "dim": FG_DIM, "accent": ACCENT, "red": ACCENT_RED,
            "green": ACCENT_MINT, "field": FIELD_BG, "border": CARD_BORDER,
            "headline_font": FONT_HEADLINE, "body_font": FONT_BODY, "mono_font": FONT_MONO,
            "card": D_SURFACE, "fg": D_TEXT, "dim": D_MUTED, "accent": D_ACCENT, "green": D_SUCCESS,
            "field": D_INPUT, "border": D_BORDER, "red": D_DANGER_TEXT,
            "pad": 12, "pad_y": 12, "title_font": F_SECTION, "status_font": F_BODY, "entry_font": F_BODY,
            "progress_font": F_VALUE, "primary_style": "Dash.Primary.TButton",
            "secondary_style": "Dash.Secondary.TButton",
        }

    def _youtube_dirs(self) -> tuple[Path, list[Path]]:
        """Where YouTube audio goes (the watch folder) and where to look for videos we already have."""
        d = resolved_output_dirs(ROOT, self._settings) if callable(resolved_output_dirs) else {}
        known = [Path(v) for k, v in (d or {}).items() if v and k in (
            "completed_videos", "completed_audio", "completed_transcripts")]
        known.append(ROOT / "watch_incoming_staging")
        return self._watch_dir(), known

    def _row_dark(self, parent: tk.Frame, row: int, label: str, var: tk.StringVar, *, cols: int = 3) -> None:
        col = row % cols
        grid_row = row // cols
        cell = tk.Frame(parent, bg=BG_CARD)
        cell.grid(row=grid_row, column=col, sticky="ew", padx=(0 if col == 0 else 18, 0), pady=3)
        self._body_label(cell, label.upper(), fg=FG_DIM, size=8, bold=True).pack(anchor="w")
        self._body_label(cell, textvariable=var, fg=FG, mono=True, size=10).pack(anchor="w", pady=(1, 0))

    def _gauge_dark(self, parent: tk.Frame, row: int, name: str, var: tk.IntVar, *, col: int = 0) -> ttk.Progressbar:
        tk.Label(parent, text=name.upper(), fg=FG_DIM, bg=BG_CARD, width=11, anchor="w", font=(FONT_HEADLINE, 9, "bold")).grid(
            row=row, column=col, sticky="w", pady=1, padx=(18 if col else 0, 0)
        )
        bar = ttk.Progressbar(parent, orient="horizontal", mode="determinate", maximum=100)
        bar.grid(row=row, column=col + 1, sticky="ew", pady=1, padx=(0, 8))
        parent.columnconfigure(col + 1, weight=1)
        tk.Label(parent, textvariable=var, fg=ACCENT, bg=BG_CARD, width=6, anchor="e", font=(FONT_MONO, 11, "bold")).grid(
            row=row, column=col + 2, sticky="e", pady=1
        )
        return bar

    # ---- 1.3.0 shared components (screens_redesign_spec.md section 2) ----
    def _apply_screen_styles(self) -> None:
        st = ttk.Style(self)
        base = dict(font=F_BODY, padding=(12, 6), relief="flat", borderwidth=1, focusthickness=0)
        small = dict(base, padding=(12, 4))
        st.configure("Dash.Warning.TButton", background=D_WARNING_SURFACE, foreground=D_WARNING, bordercolor=D_WARNING,
                     lightcolor=D_WARNING_SURFACE, darkcolor=D_WARNING_SURFACE, focuscolor=D_WARNING_SURFACE, **small)
        st.configure("Dash.SmallSecondary.TButton", background=D_RAISED, foreground=D_TEXT, bordercolor=D_BORDER,
                     lightcolor=D_RAISED, darkcolor=D_RAISED, focuscolor=D_RAISED, **small)
        maps = {
            "Dash.Primary.TButton": PRIMARY_HOVER, "Dash.Secondary.TButton": D_INPUT, "Dash.Danger.TButton": DANGER_HOVER,
            "Dash.Small.TButton": D_INPUT, "Dash.SmallSecondary.TButton": D_INPUT, "Dash.Warning.TButton": WARNING_HOVER,
        }
        for name, active in maps.items():
            st.map(name, background=[("disabled", D_SURFACE_2), ("active", active)],
                   foreground=[("disabled", D_TEXT_DISABLED)], bordercolor=[("disabled", D_BORDER)])
        st.configure("Screen.Treeview", background=D_SURFACE_2, fieldbackground=D_SURFACE_2, foreground=D_TEXT,
                     rowheight=24, font=F_BODY, borderwidth=0, bordercolor=D_BORDER, lightcolor=D_SURFACE_2,
                     darkcolor=D_SURFACE_2)
        st.map("Screen.Treeview", background=[("selected", D_NAV_SEL)], foreground=[("selected", D_ACCENT)])
        st.configure("Screen.Treeview.Heading", background=D_RAISED, foreground=D_MUTED, font=F_LABEL, relief="flat",
                     borderwidth=0, bordercolor=D_BORDER, lightcolor=D_RAISED, darkcolor=D_RAISED, padding=(8, 4))
        st.map("Screen.Treeview.Heading", background=[("active", D_RAISED)], foreground=[("active", D_TEXT)])
        for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
            name = f"Screen.{orient}.TScrollbar"
            st.configure(name, troughcolor=D_SURFACE_2, background=D_BORDER, bordercolor=D_SURFACE_2,
                         lightcolor=D_BORDER, darkcolor=D_BORDER, arrowsize=8, width=8, gripcount=0, relief="flat")
            st.map(name, background=[("active", D_MUTED)])
            st.layout(name, [(f"{orient}.Scrollbar.trough", {"sticky": sticky, "children": [
                (f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        for bg_name, bg in (("Screen", D_SURFACE), ("ScreenBg", D_BG)):
            st.configure(f"{bg_name}.TCheckbutton", background=bg, foreground=D_TEXT, font=F_BODY,
                         indicatorbackground=D_SURFACE_2, indicatorforeground=D_BG_DEEP, indicatormargin=(0, 0, 8, 0),
                         focuscolor=bg)
            st.map(f"{bg_name}.TCheckbutton", background=[("active", bg)], foreground=[("active", D_TEXT)],
                   indicatorbackground=[("selected", D_ACCENT), ("active", D_INPUT)])
        st.configure("Screen.TCombobox", fieldbackground=D_INPUT, background=D_INPUT, foreground=D_TEXT,
                     arrowcolor=D_ACCENT, bordercolor=D_BORDER, lightcolor=D_INPUT, darkcolor=D_INPUT, padding=(8, 6))
        st.map("Screen.TCombobox", fieldbackground=[("readonly", D_INPUT)], foreground=[("readonly", D_TEXT)],
               selectbackground=[("readonly", D_INPUT)], selectforeground=[("readonly", D_TEXT)],
               bordercolor=[("focus", D_ACCENT)])
        st.configure("Screen.TSpinbox", fieldbackground=D_INPUT, background=D_INPUT, foreground=D_TEXT,
                     arrowcolor=D_ACCENT, bordercolor=D_BORDER, lightcolor=D_INPUT, darkcolor=D_INPUT,
                     insertcolor=D_TEXT, padding=(8, 6))
        st.map("Screen.TSpinbox", bordercolor=[("focus", D_ACCENT)])
        st.configure("Invalid.TSpinbox", fieldbackground=D_INPUT, background=D_INPUT, foreground=D_DANGER_TEXT,
                     arrowcolor=D_ACCENT, bordercolor=D_DANGER_TEXT, lightcolor=D_DANGER_TEXT, darkcolor=D_DANGER_TEXT,
                     insertcolor=D_TEXT, padding=(8, 6))
        self.option_add("*TCombobox*Listbox.background", D_INPUT)
        self.option_add("*TCombobox*Listbox.foreground", D_TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", D_NAV_SEL)
        self.option_add("*TCombobox*Listbox.selectForeground", D_ACCENT)

    CHIP_KINDS = {
        "success": (D_SUCCESS, D_SUCCESS_SURFACE), "warning": (D_WARNING, D_WARNING_SURFACE),
        "danger": (D_DANGER_TEXT, D_DANGER_SURFACE), "accent": (D_ACCENT, D_NAV_SEL),
        "muted": (D_MUTED, D_RAISED), "off": (D_TEXT_DISABLED, D_SURFACE_2),
    }

    def _chip(self, parent: tk.Misc, text: str = "", *, kind: str = "accent",
              textvariable: tk.Variable | None = None) -> tk.Frame:
        """Status-only chip (spec 2): dot + mono label, no border, never clickable."""
        fg, bg = self.CHIP_KINDS[kind]
        f = tk.Frame(parent, bg=bg, cursor="arrow")
        dot = tk.Canvas(f, width=8, height=8, bg=bg, highlightthickness=0, bd=0)
        oid = dot.create_oval(0, 0, 8, 8, fill=fg, outline="")
        dot.pack(side="left", padx=(8, 0), pady=8)
        lbl = tk.Label(f, text=text, font=F_MONO_LABEL, fg=fg, bg=bg, padx=0, pady=0)
        if textvariable is not None:
            lbl.configure(textvariable=textvariable)
        lbl.pack(side="left", padx=8, pady=4)
        f._chip_parts = (dot, oid, lbl)  # noqa: SLF001
        return f

    def _set_chip(self, chip: tk.Frame, kind: str, text: str | None = None) -> None:
        fg, bg = self.CHIP_KINDS[kind]
        dot, oid, lbl = chip._chip_parts  # noqa: SLF001
        chip.configure(bg=bg)
        dot.configure(bg=bg)
        dot.itemconfigure(oid, fill=fg)
        lbl.configure(bg=bg, fg=fg)
        if text is not None:
            lbl.configure(text=text)

    def _wrap_label(self, label: tk.Label, minimum: int = 80) -> tk.Label:
        def on_cfg(e: tk.Event) -> None:
            w = max(minimum, e.width - 4)
            if int(str(label.cget("wraplength")) or 0) != w:
                label.configure(wraplength=w)
        label.configure(width=1)
        label.bind("<Configure>", on_cfg, add="+")
        return label

    def _page_header(self, parent: tk.Misc, crumb: str, title: str, sub: str) -> dict:
        """Page header (spec 2): breadcrumb over title + inline subtitle. Pieces are re-packed per layout."""
        h = {
            "head": tk.Frame(parent, bg=D_BG), "block": tk.Frame(parent, bg=D_BG),
            "crumb_row": tk.Frame(parent, bg=D_BG), "title_row": tk.Frame(parent, bg=D_BG),
        }
        h["crumb"] = self._dlabel(parent, crumb, font=F_MONO_LABEL, fg=D_ACCENT)
        h["title"] = self._dlabel(parent, title, font=F_PAGE)
        h["sub"] = self._dlabel(parent, sub, fg=D_MUTED)
        return h

    def _pack_header(self, h: dict, outer: tk.Frame, *, sub_in_crumb: bool = False,
                     right: tk.Misc | None = None, title_extra: tk.Misc | None = None,
                     right_fill: bool = False) -> None:
        h["head"].pack(in_=outer, fill="x", pady=(0, 12))
        h["block"].pack(in_=h["head"], side="left")
        h["crumb_row"].pack(in_=h["block"], fill="x")
        h["title_row"].pack(in_=h["block"], fill="x", pady=(4, 0))
        h["crumb"].pack(in_=h["crumb_row"], side="left")
        h["title"].pack(in_=h["title_row"], side="left")
        if sub_in_crumb:
            h["sub"].pack(in_=h["crumb_row"], side="left", padx=(12, 0))
        else:
            h["sub"].pack(in_=h["title_row"], side="left", padx=(12, 0), pady=(6, 0))
        if title_extra is not None:
            title_extra.pack(in_=h["title_row"], side="left", padx=(12, 0))
        if right is not None:
            if right_fill:
                right.pack(in_=h["head"], side="left", fill="x", expand=True, padx=(16, 0))
            else:
                right.pack(in_=h["head"], side="right")

    def _header_parts(self, h: dict) -> list:
        return [h["head"], h["block"], h["crumb_row"], h["title_row"], h["crumb"], h["title"], h["sub"]]

    def _control_bar(self, parent: tk.Misc) -> tuple[tk.Frame, tk.Frame]:
        bar = tk.Frame(parent, bg=D_SURFACE, highlightthickness=1, highlightbackground=D_BORDER)
        inner = tk.Frame(bar, bg=D_SURFACE)
        inner.pack(fill="both", expand=True, padx=12, pady=7)
        return bar, inner

    def _card_header(self, parent: tk.Misc, title: str) -> tk.Frame:
        row = tk.Frame(parent, bg=parent.cget("bg"))
        row.pack(fill="x")
        self._dlabel(row, title, font=F_SECTION).pack(side="left")
        return row

    def _tiles(self, parent: tk.Misc, items: list) -> tuple[tk.Frame, dict]:
        """60 px stat tiles, same component as the Dashboard."""
        frame = tk.Frame(parent, bg=D_BG)
        values: dict[str, tk.Label] = {}
        for i, (key, title, var, color) in enumerate(items):
            frame.columnconfigure(i, weight=1, uniform="tiles")
            tile = tk.Frame(frame, bg=D_SURFACE_2, highlightthickness=1, highlightbackground=D_BORDER)
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            tk.Label(tile, text=title, font=F_LABEL, fg=D_MUTED, bg=D_SURFACE_2, anchor="w").pack(fill="x", padx=12, pady=(8, 0))
            v = tk.Label(tile, textvariable=var, font=F_VALUE_LG, fg=color, bg=D_SURFACE_2, anchor="w", width=1)
            v.pack(fill="x", padx=12, pady=(0, 8))
            values[key] = v
        return frame, values

    def _textview(self, parent: tk.Misc, *, wrap: str = "none", height: int = 10,
                  bg: str = D_BG_DEEP) -> tuple[tk.Frame, tk.Text]:
        """Text/log view (spec 2): bg-deep, 1 px border, mono 12 px, 8 px flat scrollbars."""
        shell = tk.Frame(parent, bg=bg, highlightthickness=1, highlightbackground=D_BORDER)
        t = tk.Text(shell, wrap=wrap, height=height, width=1, bg=bg, fg=D_TEXT, insertbackground=D_TEXT,
                    relief="flat", bd=0, highlightthickness=0, font=F_VALUE, padx=0, pady=0,
                    selectbackground=D_NAV_SEL, selectforeground=D_ACCENT)
        ybar = ttk.Scrollbar(shell, orient="vertical", command=t.yview, style="Screen.Vertical.TScrollbar")
        t.configure(yscrollcommand=ybar.set)
        t.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 8))
        ybar.grid(row=0, column=1, sticky="ns", padx=(8, 4), pady=8)
        if wrap == "none":
            xbar = ttk.Scrollbar(shell, orient="horizontal", command=t.xview, style="Screen.Horizontal.TScrollbar")
            t.configure(xscrollcommand=xbar.set)
            xbar.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(0, 4))
        shell.grid_rowconfigure(0, weight=1)
        shell.grid_columnconfigure(0, weight=1)
        t.tag_configure("warn", foreground=D_WARNING)
        t.tag_configure("error", foreground=D_DANGER_TEXT)
        t.tag_configure("debug", foreground=D_MUTED)
        t.tag_configure("latest", foreground=D_ACCENT)
        return shell, t

    def _table(self, parent: tk.Misc, cols: list, *, height: int = 8) -> tuple[tk.Frame, ttk.Treeview]:
        """Table (spec 2): 24 px header + rows, surface-2 body, warning rows, 8 px scrollbar."""
        shell = tk.Frame(parent, bg=D_SURFACE_2, highlightthickness=1, highlightbackground=D_BORDER)
        tv = ttk.Treeview(shell, columns=[c for c, _l in cols], show="headings", height=height, selectmode="browse",
                          style="Screen.Treeview")
        for c, label in cols:
            tv.heading(c, text=label.upper(), anchor="w")
            tv.column(c, width=60, minwidth=40, stretch=False, anchor="w")
        tv.column(cols[-1][0], stretch=True)
        sb = ttk.Scrollbar(shell, orient="vertical", command=tv.yview, style="Screen.Vertical.TScrollbar")
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=(28, 4))
        tv.pack(side="left", fill="both", expand=True)
        tv.tag_configure("warn", background=D_WARNING_SURFACE, foreground=D_WARNING)
        tv.tag_configure("st_success", foreground=D_SUCCESS)
        tv.tag_configure("st_accent", foreground=D_ACCENT)
        tv.tag_configure("st_muted", foreground=D_MUTED)
        tv.tag_configure("st_danger", foreground=D_DANGER_TEXT)
        return shell, tv

    @staticmethod
    def _set_columns(tv: ttk.Treeview, widths: dict) -> None:
        """Spec column widths; body cells are 12 px narrower with the scrollbar, so the stretch column gives way."""
        cols = list(tv["columns"])
        for c in cols:
            w = widths[c] - (16 if c == cols[-1] else 0)
            tv.column(c, width=w, minwidth=min(40, w))

    def _rerun_layouts(self) -> None:
        for fn in getattr(self, "_page_layouts", []):
            try:
                fn(compact=bool(self._dash_compact))
            except tk.TclError:
                pass

    # ---------- Queue (spec 3) ----------
    def _build_page_queue(self, parent: tk.Frame) -> None:
        o = tk.Frame(parent, bg=D_BG)
        o.pack(fill="both", expand=True)
        q = self._q = {"outer": o}
        q["hdr"] = self._page_header(o, "QUEUE / INGESTION", "Queue Management", "Watch folder, downloads and the running queue")
        q["badge"] = self._badge(o)
        q["ctrl"], ci = self._control_bar(o)
        self._dlabel(ci, "Watcher", font=F_SECTION, fg=D_ACCENT).pack(side="left")
        self._divider(ci).pack(side="left", padx=12)
        self.q_stop_btn = ttk.Button(ci, text="\u25a0  Stop watcher", style="Dash.Danger.TButton", command=self._stop_watcher)
        self.q_stop_btn.pack(side="left")
        self.q_start_btn = ttk.Button(ci, text="\u25b6  Start watcher (scan existing)", style="Dash.Secondary.TButton",
                                      command=self._start_watcher)
        self.q_start_btn.pack(side="left", padx=(12, 0))
        q["tiles"], self._q_tile_values = self._tiles(o, [
            ("active", "ACTIVE TASKS", self.queue_active_var, D_ACCENT),
            ("depth", "QUEUE DEPTH", self.queue_depth_var, D_TEXT),
            ("throughput", "THROUGHPUT", self.queue_throughput_var, D_SUCCESS),
            ("health", "SYSTEM HEALTH", self.queue_health_var, D_SUCCESS)])

        # Add media: watch folder, URL Lister, YouTube / Suno links (all pre-1.3.0 controls kept)
        q["add"], af = self._dcard(o)
        q["add_body"] = af
        wrow = tk.Frame(af, bg=D_SURFACE)
        q["w_title"] = self._dlabel(wrow, "Watch folder", font=F_SECTION)
        q["w_title"].configure(width=24)
        q["w_btn"] = ttk.Button(wrow, text="Open watch folder", style="Dash.Primary.TButton",
                                command=lambda: open_path(self._watch_dir()))
        q["w_help"] = self._wrap_label(self._dlabel(wrow, "Drop video or audio files here; the watcher transcribes them.",
                                                    fg=D_MUTED))
        q["w_row"] = wrow
        urow = tk.Frame(af, bg=D_SURFACE)
        q["u_title"] = self._dlabel(urow, "From the URL Lister", font=F_SECTION)
        q["u_title"].configure(width=24)
        q["u_btns"] = tk.Frame(urow, bg=D_SURFACE)
        ttk.Button(q["u_btns"], text="Import from URL Lister", style="Dash.Primary.TButton",
                   command=self._easy_import).pack(side="left")
        ttk.Button(q["u_btns"], text="Open handoff folder", style="Dash.Secondary.TButton",
                   command=self._open_handoff_folder).pack(side="left", padx=(8, 0))
        q["u_status"] = self._wrap_label(tk.Label(urow, textvariable=self.easy_status_var, fg=D_MUTED, bg=D_SURFACE,
                                                  font=F_BODY, anchor="w", justify="left"))
        self._easy_status_labels.append(q["u_status"])
        q["u_row"] = urow
        theme = self._youtube_theme()
        q["links"] = youtube_intake.build_panel(af, self.youtube, theme=theme, get_dirs=self._youtube_dirs, compact=True)
        q["links"].configure(highlightthickness=0)
        try:
            q["links"].winfo_children()[0].pack_configure(padx=0, pady=0)
            q["links_title"] = q["links"].winfo_children()[0].winfo_children()[0].winfo_children()[0]
        except (IndexError, tk.TclError):
            q["links_title"] = None

        # Active Queue
        q["active"], qa = self._dcard(o)
        hdr = tk.Frame(qa, bg=D_SURFACE)
        hdr.pack(fill="x")
        self._dlabel(hdr, "Active Queue", font=F_SECTION).pack(side="left")
        self.queue_pause_text = tk.StringVar(value="Pause queue")
        self.q_pause_btn = ttk.Button(hdr, textvariable=self.queue_pause_text, style="Dash.Primary.TButton",
                                      command=self._queue_toggle_pause)
        self.q_pause_btn.pack(side="right")
        self._dlabel(hdr, textvariable=self.queue_meta_var, fg=D_MUTED, clip=True).pack(side="right", fill="x", expand=True,
                                                                                        padx=(12, 8))
        self._dlabel(hdr, "", fg=D_MUTED)  # spacer keeps the header 32 px
        now = tk.Frame(qa, bg=D_SURFACE)
        now.pack(fill="x", pady=(8, 0))
        nrow = tk.Frame(now, bg=D_SURFACE)
        nrow.pack(fill="x")
        self.live_name_var = tk.StringVar(value="Nothing transcribing right now")
        self.live_detail_var = tk.StringVar(value="")
        self.live_pct_var = tk.StringVar(value="")
        self._dlabel(nrow, "Now transcribing", font=F_LABEL, fg=D_MUTED).pack(side="left")
        self._dlabel(nrow, textvariable=self.live_pct_var, font=F_VALUE, fg=D_ACCENT).pack(side="right")
        self._dlabel(nrow, textvariable=self.live_name_var, clip=True).pack(side="left", fill="x", expand=True, padx=(8, 8))
        bar1 = MiniBar(now, fill=D_ACCENT, bg=D_SURFACE)
        bar1.pack(fill="x", pady=(4, 0))
        self._live_bars = [bar1]
        self._dlabel(now, textvariable=self.live_detail_var, fg=D_MUTED, clip=True).pack(fill="x", pady=(4, 0))
        q["table"], self.queue_tree = self._table(qa, [("file", "Source file"), ("state", "State"), ("engine", "Engine"),
                                                        ("progress", "Progress"), ("eta", "ETA")], height=8)
        self.queue_tree.column("eta", anchor="e")
        q["table"].pack(fill="both", expand=True, pady=(8, 0))
        acts = tk.Frame(qa, bg=D_SURFACE)
        acts.pack(fill="x", pady=(8, 0))
        ttk.Button(acts, text="Move up", style="Dash.SmallSecondary.TButton", command=lambda: self._queue_move(-1)).pack(side="left")
        ttk.Button(acts, text="Move down", style="Dash.SmallSecondary.TButton", command=lambda: self._queue_move(1)).pack(
            side="left", padx=(8, 0))
        ttk.Button(acts, text="Skip selected", style="Dash.SmallSecondary.TButton", command=self._queue_skip).pack(
            side="left", padx=(8, 0))
        self._wrap_label(self._dlabel(acts, "Pause queue = finish the current file, start no new ones (watcher stays on).",
                                      fg=D_MUTED)).pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._queue_paused = False
        self._queue_order: list[str] = []
        self._last_queue_data: dict | None = None

        # Recently Completed
        q["recent"], rf = self._dcard(o)
        rh = tk.Frame(rf, bg=D_SURFACE)
        rh.pack(fill="x")
        self._dlabel(rh, "Recently Completed", font=F_SECTION).pack(side="left")
        ttk.Button(rh, text="Open videos", style="Dash.SmallSecondary.TButton",
                   command=lambda: open_path(self._completed_dir())).pack(side="right")
        q["recent_shell"], self.queue_recent_text = self._textview(rf, wrap="none", height=4)
        q["recent_shell"].pack(fill="both", expand=True, pady=(8, 0))

        q["cols"] = tk.Frame(o, bg=D_BG)
        q["cols"].columnconfigure(0, weight=1)
        q["cols"].columnconfigure(1, minsize=400)
        q["right"] = tk.Frame(o, bg=D_BG)
        for w in (q["ctrl"], q["tiles"], q["add"], q["active"], q["recent"], q["badge"]):
            w.lift()
        self._page_layouts.append(self._layout_queue)
        self._layout_queue(compact=False)

    def _layout_queue(self, *, compact: bool) -> None:
        q = self._q
        o = q["outer"]
        self._forget(*self._header_parts(q["hdr"]), q["badge"], q["ctrl"], q["tiles"], q["add"], q["active"],
                     q["recent"], q["cols"], q["right"], q["w_title"], q["w_btn"], q["w_help"], q["w_row"],
                     q["u_title"], q["u_btns"], q["u_status"], q["u_row"], q["links"])
        af = q["add_body"]
        if not compact:
            self._pack_header(q["hdr"], o, right=q["badge"])
            q["ctrl"].pack(in_=o, fill="x", pady=(0, 12))
            q["tiles"].pack(in_=o, fill="x", pady=(0, 12))
            q["add"].pack(in_=o, fill="x", pady=(0, 12))
            q["active"].pack(in_=o, fill="both", expand=True, pady=(0, 12))
            q["recent"].pack(in_=o, fill="x")
            q["w_row"].pack(in_=af, fill="x")
            q["w_title"].pack(side="left")
            q["w_btn"].pack(side="left", padx=(8, 0))
            q["w_help"].pack(side="left", fill="x", expand=True, padx=(8, 0))
            q["u_row"].pack(in_=af, fill="x", pady=(12, 0))
            q["u_title"].pack(side="left")
            q["u_btns"].pack(side="left", padx=(8, 0))
            q["u_status"].pack(side="left", fill="x", expand=True, padx=(8, 0))
            q["links"].pack(in_=af, fill="x", pady=(12, 0))
            if q["links_title"] is not None:
                q["links_title"].pack_configure(side="left", fill="none")
            self.queue_tree.configure(height=8)
            self._set_columns(self.queue_tree, {"file": 332, "state": 104, "engine": 132, "progress": 144, "eta": 56})
            self.queue_recent_text.configure(height=4)
        else:
            self._pack_header(q["hdr"], o, sub_in_crumb=True, title_extra=q["badge"], right=q["ctrl"], right_fill=True)
            q["tiles"].pack(in_=o, fill="x", pady=(0, 12))
            q["cols"].pack(in_=o, fill="both", expand=True)
            q["active"].grid(in_=q["cols"], row=0, column=0, sticky="nsew", padx=(0, 12))
            q["right"].grid(in_=q["cols"], row=0, column=1, sticky="nsew")
            q["cols"].rowconfigure(0, weight=1)
            q["add"].pack(in_=q["right"], fill="x", pady=(0, 12))
            q["recent"].pack(in_=q["right"], fill="both", expand=True)
            q["w_row"].pack(in_=af, fill="x")
            q["w_help"].pack(side="bottom", fill="x", pady=(8, 0))
            q["w_title"].pack(side="left")
            q["w_btn"].pack(side="right")
            q["u_row"].pack(in_=af, fill="x", pady=(12, 0))
            q["u_title"].pack(side="top", anchor="w")
            q["u_btns"].pack(side="top", anchor="w", pady=(8, 0))
            q["u_status"].pack(side="top", fill="x", pady=(8, 0))
            q["links"].pack(in_=af, fill="x", pady=(12, 0))
            if q["links_title"] is not None:
                q["links_title"].pack_configure(side="top", fill="x", pady=(0, 8))
            self.queue_tree.configure(height=11)
            self._set_columns(self.queue_tree, {"file": 244, "state": 96, "engine": 120, "progress": 120, "eta": 56})
            self.queue_recent_text.configure(height=5)

    # ---------- Monitor (spec 4; trends chart is a proposal, not built) ----------
    def _build_page_monitor(self, parent: tk.Frame) -> None:
        o = tk.Frame(parent, bg=D_BG)
        o.pack(fill="both", expand=True)
        m = self._m = {"outer": o}
        m["hdr"] = self._page_header(o, "LIVE STREAMING", "System Performance", "CPU, memory and GPU while jobs run")
        m["badge"] = self._badge(o)
        self._m_tile_vars = {k: tk.StringVar(value="--") for k in ("cpu", "mem", "gpu")}

        def mirror(src: tk.StringVar, dst: tk.StringVar, prefix: str) -> None:
            v = src.get()
            dst.set(v[len(prefix):] if v.startswith(prefix) else v)

        for key, src, prefix in (("cpu", self.monitor_cpu_var, "CPU "), ("mem", self.monitor_mem_var, "MEM "),
                                 ("gpu", self.monitor_gpu_var, "GPU ")):
            src.trace_add("write", lambda *_a, s=src, d=self._m_tile_vars[key], p=prefix: mirror(s, d, p))
            mirror(src, self._m_tile_vars[key], prefix)
        m["tiles"], self._m_tile_values = self._tiles(o, [
            ("cpu", "CPU", self._m_tile_vars["cpu"], D_ACCENT), ("mem", "MEMORY", self._m_tile_vars["mem"], D_TEXT),
            ("gpu", "GPU", self._m_tile_vars["gpu"], D_SUCCESS), ("health", "LIVE HEALTH", self.queue_health_var, D_SUCCESS)])
        m["proc"], pf = self._dcard(o)
        ph = tk.Frame(pf, bg=D_SURFACE)
        ph.pack(fill="x")
        self._dlabel(ph, "Processing Unit", font=F_SECTION).pack(side="left")
        self._dlabel(ph, "watch_resource.jsonl \u00b7 newest at the bottom", fg=D_MUTED).pack(side="right")
        self._dlabel(pf, textvariable=self.monitor_hint_var, fg=D_MUTED, clip=True).pack(fill="x", pady=(8, 0))
        shell, self.resource_text = self._textview(pf, wrap="none", height=10)
        shell.pack(fill="both", expand=True, pady=(8, 0))
        m["disk"], df = self._dcard(o)
        dh = tk.Frame(df, bg=D_SURFACE)
        dh.pack(fill="x")
        self._dlabel(dh, "Disk / Telemetry Stream", font=F_SECTION).pack(side="left")
        self._dlabel(dh, textvariable=self.monitor_disk_var, font=F_VALUE, fg=D_ACCENT).pack(side="right")
        self._wrap_label(self._dlabel(df, "Telemetry rolls into the watcher resource log while jobs are active. "
                                          "Set the log profile to tuning or debug in Settings for more samples.",
                                      fg=D_MUTED)).pack(fill="x", pady=(8, 0))
        for w in (m["tiles"], m["proc"], m["disk"], m["badge"]):
            w.lift()
        self._page_layouts.append(self._layout_monitor)
        self._layout_monitor(compact=False)

    def _layout_monitor(self, *, compact: bool) -> None:
        m = self._m
        o = m["outer"]
        self._forget(*self._header_parts(m["hdr"]), m["badge"], m["tiles"], m["proc"], m["disk"])
        self._pack_header(m["hdr"], o, sub_in_crumb=compact, right=m["badge"])
        m["tiles"].pack(in_=o, fill="x", pady=(0, 12))
        m["proc"].pack(in_=o, fill="both", expand=True, pady=(0, 12))
        m["disk"].pack(in_=o, fill="x")
        self.resource_text.configure(height=24 if not compact else 12)

    # ---------- Logs (spec 5; level filter is a proposal, not built) ----------
    def _build_page_logs(self, parent: tk.Frame) -> None:
        o = tk.Frame(parent, bg=D_BG)
        o.pack(fill="both", expand=True)
        lg = self._lg = {"outer": o}
        lg["hdr"] = self._page_header(o, "SYSTEM / LIVE LOGS", "System Logs", "watch_events.log for the running watcher")
        lg["chip"] = self._chip(o, kind="success", textvariable=self.log_status_var)
        lg["ctrl"], ci = self._control_bar(o)
        lg["ctrl_inner"] = ci
        ttk.Button(ci, text="Open log file", style="Dash.Secondary.TButton",
                   command=lambda: open_path(self._watch_dir() / "watch_events.log")).pack(side="left")
        ttk.Button(ci, text="Unified log", style="Dash.Secondary.TButton", command=self._open_unified_log).pack(
            side="left", padx=(8, 0))
        ttk.Button(ci, text="Copy for support", style="Dash.Secondary.TButton", command=self._copy_for_support).pack(
            side="left", padx=(8, 0))
        ttk.Button(ci, text="\u27f3  Refresh", style="Dash.Secondary.TButton", command=self.refresh_once).pack(
            side="left", padx=(8, 0))
        lg["view"], vf = self._dcard(o)
        fr = tk.Frame(vf, bg=D_SURFACE)
        fr.pack(fill="x")
        self._dlabel(fr, "Filter", fg=D_MUTED).pack(side="left")
        ent = ttk.Entry(fr, textvariable=self.log_filter_var, font=F_BODY, width=1)
        ent.pack(side="left", fill="x", expand=True, padx=(8, 8), ipady=4)
        ttk.Button(fr, text="\u2715", width=3, style="Dash.Secondary.TButton",
                   command=lambda: self.log_filter_var.set("")).pack(side="left")
        self.log_filter_var.trace_add("write", lambda *_a: self.after_idle(self._load_log_tail))
        self._dlabel(vf, textvariable=self.logs_hint_var, fg=D_MUTED, clip=True).pack(fill="x", pady=(8, 0))
        shell, self.log_text = self._textview(vf, wrap="none", height=20)
        shell.pack(fill="both", expand=True, pady=(8, 0))
        for w in (lg["chip"], lg["ctrl"], lg["view"]):
            w.lift()
        self._page_layouts.append(self._layout_logs)
        self._layout_logs(compact=False)

    def _layout_logs(self, *, compact: bool) -> None:
        lg = self._lg
        o = lg["outer"]
        self._forget(*self._header_parts(lg["hdr"]), lg["chip"], lg["ctrl"], lg["view"])
        if not compact:
            self._pack_header(lg["hdr"], o, right=lg["chip"])
            lg["ctrl"].pack(in_=o, fill="x", pady=(0, 12))
        else:
            self._pack_header(lg["hdr"], o, sub_in_crumb=True, title_extra=lg["chip"], right=lg["ctrl"], right_fill=True)
        lg["view"].pack(in_=o, fill="both", expand=True)
        self.log_text.configure(height=40 if not compact else 22)

    def _color_log_lines(self, widget: tk.Text) -> None:
        for tag in ("warn", "error", "debug"):
            widget.tag_remove(tag, "1.0", "end")
        try:
            n = int(widget.index("end-1c").split(".")[0])
        except (tk.TclError, ValueError):
            return
        for i in range(1, n + 1):
            line = widget.get(f"{i}.0", f"{i}.end").lower()
            if "error" in line or "failed" in line or "traceback" in line:
                widget.tag_add("error", f"{i}.0", f"{i}.end")
            elif "warn" in line or "stalled" in line:
                widget.tag_add("warn", f"{i}.0", f"{i}.end")
            elif "debug" in line:
                widget.tag_add("debug", f"{i}.0", f"{i}.end")

    # ---------- Live Transcribe (spec 6) ----------
    def _build_page_live_transcribe(self, parent: tk.Frame) -> None:
        o = tk.Frame(parent, bg=D_BG)
        o.pack(fill="both", expand=True)
        lt = self._lt = {"outer": o}
        lt["hdr"] = self._page_header(o, "SYSTEM / LIVE TRANSCRIBE", "Live Transcribe",
                                      "Whisper segments for the file being decoded, newest first")
        lt["now"], nf = self._dcard(o)
        nh = tk.Frame(nf, bg=D_SURFACE)
        nh.pack(fill="x")
        self._dlabel(nh, textvariable=self.live_name_var, font=F_SECTION, clip=True).pack(side="left", fill="x", expand=True)
        ttk.Button(nh, text="\u27f3  Refresh", style="Dash.Secondary.TButton", command=self.refresh_once).pack(side="right")
        ttk.Button(nh, text="Open raw stream", style="Dash.Secondary.TButton",
                   command=lambda: open_path(self._watch_dir() / "whisper_live.log")).pack(side="right", padx=(0, 8))
        self.lt_chip = self._chip(nh, "IDLE", kind="muted")
        self.lt_chip.pack(side="right", padx=(8, 8))
        pr = tk.Frame(nf, bg=D_SURFACE)
        pr.pack(fill="x", pady=(8, 0))
        self._dlabel(pr, textvariable=self.live_pct_var, font=F_VALUE, fg=D_ACCENT, clip=False).pack(side="right", padx=(8, 0))
        bar2 = MiniBar(pr, fill=D_ACCENT, bg=D_SURFACE)
        bar2.pack(side="left", fill="x", expand=True)
        self._live_bars.append(bar2)
        self._dlabel(nf, textvariable=self.live_detail_var, fg=D_MUTED, clip=True).pack(fill="x", pady=(8, 0))
        lt["stream"], sf = self._dcard(o)
        sh = tk.Frame(sf, bg=D_SURFACE)
        sh.pack(fill="x")
        self._dlabel(sh, "Segment stream", font=F_SECTION).pack(side="left")
        self.lt_wrap_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(sh, text="Word wrap", variable=self.lt_wrap_var, style="Screen.TCheckbutton",
                        command=lambda: self.live_transcribe_text.configure(
                            wrap="word" if self.lt_wrap_var.get() else "none")).pack(side="right")
        self._dlabel(sh, "whisper_live.log", font=F_VALUE, fg=D_MUTED).pack(side="right", padx=(0, 8))
        self._dlabel(sf, textvariable=self.live_transcribe_hint_var, fg=D_MUTED, clip=True).pack(fill="x", pady=(8, 0))
        shell, self.live_transcribe_text = self._textview(sf, wrap="none", height=20)
        self.live_transcribe_text.configure(wrap="word")
        shell.pack(fill="both", expand=True, pady=(8, 0))
        for w in (lt["now"], lt["stream"]):
            w.lift()
        self._page_layouts.append(self._layout_live)
        self._layout_live(compact=False)

    def _layout_live(self, *, compact: bool) -> None:
        lt = self._lt
        o = lt["outer"]
        self._forget(*self._header_parts(lt["hdr"]), lt["now"], lt["stream"])
        self._pack_header(lt["hdr"], o)
        lt["now"].pack(in_=o, fill="x", pady=(0, 12))
        lt["stream"].pack(in_=o, fill="both", expand=True)
        self.live_transcribe_text.configure(height=38 if not compact else 18)

    # ---------- Handoff (spec 7) ----------
    HANDOFF_FLAGS = (
        ("ASK_EACH_TIME", "post_transcript_always_ask", False),
        ("LLM_API_FIRST", "handoff_try_api_first", False),
        ("RESUME_ON_START", "resume_on_start", True),
        ("SKIP_DUPLICATES", "skip_duplicates", True),
    )

    def _build_page_handoff(self, parent: tk.Frame) -> None:
        o = tk.Frame(parent, bg=D_BG)
        o.pack(fill="both", expand=True)
        h = self._h = {"outer": o}
        h["hdr"] = self._page_header(o, "HANDOFF / ROUTING", "Post-Transcript Routing",
                                     "Send finished transcripts to NotebookLM or an LLM")
        h["badge"] = self._badge(o)
        h["primary"], pf = self._dcard(o)
        row = self._card_header(pf, "Primary Destination")
        self._chip(row, kind="accent", textvariable=self.handoff_destination_var).pack(side="right")
        acts = tk.Frame(pf, bg=D_SURFACE)
        acts.pack(fill="x", pady=(12, 0))
        ttk.Button(acts, text="Send last completed transcript", style="Dash.Primary.TButton",
                   command=self._open_handoff).pack(side="left")
        ttk.Button(acts, text="Open NotebookLM staging", style="Dash.Secondary.TButton",
                   command=lambda: handoff_open_folder(self._watch_dir() / "staged_for_notebooklm")).pack(side="left", padx=(8, 0))
        ttk.Button(acts, text="Open videos folder", style="Dash.Secondary.TButton",
                   command=lambda: open_path(self._completed_dir())).pack(side="left", padx=(8, 0))
        h["diag"], dfm = self._dcard(o)
        row = self._card_header(dfm, "Endpoint diagnostics")
        self._chip(row, "READ-ONLY", kind="muted").pack(side="right")
        kv = tk.Frame(dfm, bg=D_SURFACE)
        kv.pack(fill="x", pady=(12, 0))
        kv.columnconfigure(1, weight=1)
        self.handoff_diag_vars: dict[str, tk.StringVar] = {}
        rows = [("watch_dir", str(self._watch_dir())), ("staging_dir", str(self._watch_dir() / "staged_for_notebooklm")),
                ("notebooklm", "manual browser upload / clipboard workflow"),
                ("default_provider", str(self._settings.get("default_llm_provider", "chatgpt")))]
        for r, (key, value) in enumerate(rows):
            tk.Label(kv, text=key, font=F_LABEL, fg=D_MUTED, bg=D_SURFACE, anchor="w", width=1).grid(
                row=r, column=0, sticky="ew", pady=(0 if r == 0 else 4, 0), ipadx=56)
            var = tk.StringVar(value=value)
            self.handoff_diag_vars[key] = var
            tk.Entry(kv, textvariable=var, state="readonly", readonlybackground=D_SURFACE, fg=D_TEXT, relief="flat",
                     bd=0, highlightthickness=0, font=F_VALUE, width=1).grid(row=r, column=1, sticky="ew",
                                                                              pady=(0 if r == 0 else 4, 0))
        h["fmt"], ff = self._dcard(o)
        row = self._card_header(ff, "Default Formatting")
        self._chip(row, kind="success", textvariable=self.handoff_format_var).pack(side="right")
        self._wrap_label(self._dlabel(ff, "Raw text, structured JSON and summary flows all stay available in the "
                                          "routing dialog.", fg=D_MUTED)).pack(fill="x", pady=(12, 0))
        fa = tk.Frame(ff, bg=D_SURFACE)
        fa.pack(fill="x", pady=(12, 0))
        ttk.Button(fa, text="Open transcripts", style="Dash.Secondary.TButton",
                   command=lambda: open_path(self._transcript_dir())).pack(side="left")
        ttk.Button(fa, text="Open JSON", style="Dash.Secondary.TButton",
                   command=lambda: open_path(self._json_dir())).pack(side="left", padx=(8, 0))
        h["flags"], flf = self._dcard(o)
        row = self._card_header(flf, "Handoff flags")
        self._dlabel(row, "Status only, not clickable", fg=D_MUTED).pack(side="right")
        chips = tk.Frame(flf, bg=D_SURFACE)
        chips.pack(fill="x", pady=(12, 0))
        self._handoff_flag_chips: dict[str, tk.Frame] = {}
        items = [name for name, _k, _d in self.HANDOFF_FLAGS] + ["CPU_RETRY_ON_CRASH"]
        for i, name in enumerate(items):
            c = self._chip(chips, f"{name} \u00b7 \u2014", kind="off")
            c.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 8), pady=(0, 8))
            self._handoff_flag_chips[name] = c
        self._refresh_handoff_flags()
        h["cols"] = tk.Frame(o, bg=D_BG)
        h["left"] = tk.Frame(o, bg=D_BG)
        h["right"] = tk.Frame(o, bg=D_BG)
        h["lower"] = tk.Frame(o, bg=D_BG)
        for w in (h["primary"], h["diag"], h["fmt"], h["flags"], h["badge"]):
            w.lift()
        self._page_layouts.append(self._layout_handoff)
        self._layout_handoff(compact=False)

    def _refresh_handoff_flags(self) -> None:
        chips = getattr(self, "_handoff_flag_chips", None)
        if not chips:
            return
        s = self._settings or {}
        for name, key, default in self.HANDOFF_FLAGS:
            on = bool(s.get(key, default))
            self._set_chip(chips[name], "success" if on else "off", f"{name} \u00b7 {'ON' if on else 'OFF'}")
        # Built into the watcher: a crashed worker is retried once on CPU (no setting).
        self._set_chip(chips["CPU_RETRY_ON_CRASH"], "success", "CPU_RETRY_ON_CRASH \u00b7 ON")
        for key, var in getattr(self, "handoff_diag_vars", {}).items():
            if key == "default_provider":
                var.set(str(s.get("default_llm_provider", "chatgpt")))

    def _layout_handoff(self, *, compact: bool) -> None:
        h = self._h
        o = h["outer"]
        self._forget(*self._header_parts(h["hdr"]), h["badge"], h["primary"], h["diag"], h["fmt"], h["flags"],
                     h["cols"], h["left"], h["right"], h["lower"])
        self._pack_header(h["hdr"], o, right=h["badge"])
        if not compact:
            h["primary"].pack(in_=o, fill="x", pady=(0, 12))
            h["diag"].pack(in_=o, fill="x", pady=(0, 12))
            h["lower"].pack(in_=o, fill="x")
            h["lower"].columnconfigure(0, weight=1, uniform="hl")
            h["lower"].columnconfigure(1, weight=1, uniform="hl")
            h["fmt"].grid(in_=h["lower"], row=0, column=0, sticky="nsew", padx=(0, 6))
            h["flags"].grid(in_=h["lower"], row=0, column=1, sticky="nsew", padx=(6, 0))
        else:
            h["cols"].pack(in_=o, fill="x")
            h["cols"].columnconfigure(0, weight=1)
            h["cols"].columnconfigure(1, minsize=420)
            h["left"].grid(in_=h["cols"], row=0, column=0, sticky="nsew", padx=(0, 12))
            h["right"].grid(in_=h["cols"], row=0, column=1, sticky="nsew")
            h["primary"].pack(in_=h["left"], fill="x", pady=(0, 12))
            h["diag"].pack(in_=h["left"], fill="x")
            h["fmt"].pack(in_=h["right"], fill="x", pady=(0, 12))
            h["flags"].pack(in_=h["right"], fill="x")

    # ---------- Search (spec 8) ----------
    def _search_dirs(self) -> dict:
        return resolved_output_dirs(ROOT, self._settings)

    def _build_page_search(self, parent: tk.Frame) -> None:
        o = tk.Frame(parent, bg=D_BG)
        o.pack(fill="both", expand=True)
        s = self._s = {"outer": o}
        s["hdr"] = self._page_header(o, "TRANSCRIPTS / SEARCH", "Search Transcripts", "A word or \"exact phrase\" in every transcript")
        self.search_var = tk.StringVar()
        self.search_status_var = tk.StringVar(value="")
        self.search_index_var = tk.StringVar(value="")
        s["bar"], bi = self._control_bar(o)
        ent = ttk.Entry(bi, textvariable=self.search_var, font=F_VALUE, width=1)
        ent.pack(side="left", fill="x", expand=True, ipady=4)
        ent.bind("<Return>", lambda _e: self._run_search())
        ttk.Button(bi, text="Search", style="Dash.Primary.TButton", command=self._run_search).pack(side="left", padx=(8, 0))
        ttk.Button(bi, text="Rebuild index", style="Dash.Secondary.TButton", command=self._search_rebuild).pack(
            side="left", padx=(8, 0))
        s["results"], rf = self._dcard(o)
        rh = tk.Frame(rf, bg=D_SURFACE)
        rh.pack(fill="x")
        self._dlabel(rh, textvariable=self.search_status_var, font=F_VALUE, fg=D_SUCCESS).pack(side="left")
        self.search_open_btn = ttk.Button(rh, text="Open transcript", style="Dash.Secondary.TButton",
                                          command=self._search_open_transcript)
        self.search_open_btn.pack(side="right")
        self.search_play_btn = ttk.Button(rh, text="\u25b6  Play at time", style="Dash.Primary.TButton",
                                          command=self._search_open_video)
        self.search_play_btn.pack(side="right", padx=(0, 8))
        self._dlabel(rh, textvariable=self.search_index_var, font=F_VALUE, fg=D_MUTED, clip=True).pack(
            side="left", fill="x", expand=True, padx=(12, 12))
        self._dlabel(rf, "Double-click a hit to play the video at that moment. Select a row to open its transcript.",
                     fg=D_MUTED, clip=True).pack(fill="x", pady=(8, 0))
        s["table"], self.search_tree = self._table(rf, [("video", "Video"), ("time", "Time"), ("context", "Context")], height=20)
        self.search_tree.column("time", anchor="e")
        s["table"].pack(fill="both", expand=True, pady=(8, 0))
        self.search_tree.bind("<Double-1>", lambda _e: self._search_open_video())
        self.search_tree.bind("<<TreeviewSelect>>", lambda _e: self._search_buttons())
        self._search_hits: dict[str, dict] = {}
        self._search_buttons()
        try:
            n, segs = transcript_search.stats()
            self.search_index_var.set(f"index: {n} transcripts, {segs} segments")
        except Exception as exc:
            self.search_index_var.set(f"index unavailable: {exc}")
        for w in (s["bar"], s["results"]):
            w.lift()
        self._page_layouts.append(self._layout_search)
        self._layout_search(compact=False)

    def _search_buttons(self) -> None:
        state = ["!disabled"] if self.search_tree.selection() else ["disabled"]
        for b in (self.search_play_btn, self.search_open_btn):
            b.state(state)

    def _layout_search(self, *, compact: bool) -> None:
        s = self._s
        o = s["outer"]
        self._forget(*self._header_parts(s["hdr"]), s["bar"], s["results"])
        self._pack_header(s["hdr"], o)
        s["bar"].pack(in_=o, fill="x", pady=(0, 12))
        s["results"].pack(in_=o, fill="both", expand=True)
        if not compact:
            self.search_tree.configure(height=29)
            self._set_columns(self.search_tree, {"video": 232, "time": 80, "context": 456})
        else:
            self.search_tree.configure(height=16)
            self._set_columns(self.search_tree, {"video": 280, "time": 80, "context": 688})

    # ---------- Review (spec 9; Prev/Next flag + progress are proposals, not built) ----------
    def _build_page_review(self, parent: tk.Frame) -> None:
        o = tk.Frame(parent, bg=D_BG)
        o.pack(fill="both", expand=True)
        r = self._r = {"outer": o}
        r["hdr"] = self._page_header(o, "TRANSCRIPTS / REVIEW", "Review Flagged Spots",
                                     "Play the audio, fix the text, save; the original is kept")
        self.review_stem_var = tk.StringVar()
        self.review_status_var = tk.StringVar(value="")
        r["bar"], bi = self._control_bar(o)
        self._dlabel(bi, "Transcript", fg=D_MUTED).pack(side="left")
        self.review_combo = ttk.Combobox(bi, textvariable=self.review_stem_var, state="readonly", width=1,
                                         style="Screen.TCombobox", font=F_BODY)
        self.review_combo.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.review_combo.bind("<<ComboboxSelected>>", lambda _e: self._review_load_flags())
        ttk.Button(bi, text="\u27f3  Refresh list", style="Dash.Secondary.TButton", command=self._review_refresh_list).pack(side="left")
        self._dlabel(bi, textvariable=self.review_status_var, font=F_VALUE, fg=D_SUCCESS, clip=True).pack(
            side="left", padx=(8, 0))
        self._review_status_lbl = bi.winfo_children()[-1]
        # amber missed-audio strip (spec 12)
        r["warn"] = tk.Frame(o, bg=D_WARNING_SURFACE, highlightthickness=1, highlightbackground=D_WARNING)
        wi = tk.Frame(r["warn"], bg=D_WARNING_SURFACE)
        wi.pack(fill="x", padx=(12, 8), pady=7)
        tk.Label(wi, text="\u26a0", font=F_SECTION, fg=D_WARNING, bg=D_WARNING_SURFACE).pack(side="left")
        self.review_warn_title = tk.StringVar(value="")
        self.review_warn_detail = tk.StringVar(value="")
        tk.Label(wi, textvariable=self.review_warn_title, font=F_NAV, fg=D_WARNING, bg=D_WARNING_SURFACE).pack(side="left", padx=(8, 0))
        ttk.Button(wi, text="Jump to spot", style="Dash.Warning.TButton", command=self._review_jump_coverage).pack(side="right")
        self._dlabel(wi, textvariable=self.review_warn_detail, fg=D_MUTED, clip=True).pack(side="left", fill="x", expand=True, padx=(8, 8))
        wi.winfo_children()[-1].configure(bg=D_WARNING_SURFACE)
        # flags table
        r["flags"], ff = self._dcard(o)
        fh = tk.Frame(ff, bg=D_SURFACE)
        fh.pack(fill="x")
        self._dlabel(fh, "Flagged spots", font=F_SECTION).pack(side="left")
        self.review_progress_var = tk.StringVar(value="")
        self._dlabel(fh, textvariable=self.review_progress_var, font=F_VALUE, fg=D_MUTED).pack(side="right")
        self._dlabel(ff, "Select a row to edit it. Double-click plays the audio there.", fg=D_MUTED, clip=True).pack(fill="x", pady=(8, 0))
        r["table"], self.review_tree = self._table(ff, [("time", "Time"), ("state", "State"), ("reason", "Why flagged"),
                                                         ("text", "Text")], height=14)
        r["table"].pack(fill="both", expand=True, pady=(8, 0))
        self.review_tree.bind("<<TreeviewSelect>>", lambda _e: self._review_select())
        self.review_tree.bind("<Double-1>", lambda _e: self._review_play())
        # editor
        r["editor"], ef = self._dcard(o)
        eh = tk.Frame(ef, bg=D_SURFACE)
        eh.pack(fill="x")
        self._dlabel(eh, "Edit selected spot", font=F_SECTION).pack(side="left")
        self.review_chip = self._chip(eh, "NO SELECTION", kind="off")
        self.review_chip.pack(side="right")
        self.review_orig_var = tk.StringVar(value="Select a row in the table to edit it.")
        self.review_why_var = tk.StringVar(value="")
        r["lines"] = []
        for key, var in (("Original", self.review_orig_var), ("Why flagged", self.review_why_var)):
            line = tk.Frame(ef, bg=D_SURFACE)
            line.pack(fill="x", pady=(8, 0))
            k = tk.Label(line, text=key, font=F_LABEL, fg=D_MUTED, bg=D_SURFACE, anchor="nw", width=1)
            k.pack(side="left", ipadx=40, anchor="n")
            v = self._wrap_label(self._dlabel(line, textvariable=var))
            v.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._dlabel(ef, "Correction", font=F_LABEL, fg=D_MUTED).pack(fill="x", pady=(8, 0))
        box = tk.Frame(ef, bg=D_INPUT, highlightthickness=1, highlightbackground=D_BORDER, highlightcolor=D_ACCENT)
        box.pack(fill="x", pady=(8, 0))
        self.review_text = tk.Text(box, height=4, width=1, wrap="word", bg=D_INPUT, fg=D_TEXT, insertbackground=D_TEXT,
                                   relief="flat", bd=0, highlightthickness=0, font=F_VALUE)
        self.review_text.pack(fill="x", padx=8, pady=8)
        self.review_text.bind("<FocusIn>", lambda _e: box.configure(highlightbackground=D_ACCENT))
        self.review_text.bind("<FocusOut>", lambda _e: box.configure(highlightbackground=D_BORDER))
        r["acts1"] = tk.Frame(ef, bg=D_SURFACE)
        r["acts2"] = tk.Frame(ef, bg=D_SURFACE)
        self.review_btns = {
            "play": ttk.Button(ef, text="\u25b6  Play snippet", style="Dash.Secondary.TButton", command=self._review_play),
            "revert": ttk.Button(ef, text="Revert to original", style="Dash.Secondary.TButton", command=self._review_revert),
            "ok": ttk.Button(ef, text="Mark OK", style="Dash.Secondary.TButton", command=self._review_mark_ok),
            "save": ttk.Button(ef, text="Save correction", style="Dash.Primary.TButton", command=self._review_save),
        }
        r["hint"] = self._wrap_label(self._dlabel(ef, "Saving updates the .txt, .csv, subtitles and search index. "
                                                      "The original text is kept.", fg=D_MUTED))
        self._review_items: dict[str, dict] = {}
        self._review_index: dict[str, dict] = {}
        r["cols"] = tk.Frame(o, bg=D_BG)
        for w in (r["bar"], r["warn"], r["flags"], r["editor"], r["acts1"], r["acts2"], *self.review_btns.values(), r["hint"]):
            w.lift()
        self._page_layouts.append(self._layout_review)
        self._layout_review(compact=False)
        self._review_editor_state(None)
        self._review_refresh_list()

    def _layout_review(self, *, compact: bool) -> None:
        r = self._r
        o = r["outer"]
        warn_shown = bool(self.review_warn_title.get())
        b = self.review_btns
        self._forget(*self._header_parts(r["hdr"]), r["bar"], r["warn"], r["flags"], r["editor"], r["cols"],
                     r["acts1"], r["acts2"], *b.values(), r["hint"])
        self._pack_header(r["hdr"], o)
        r["bar"].pack(in_=o, fill="x", pady=(0, 12))
        if warn_shown:
            r["warn"].pack(in_=o, fill="x", pady=(0, 12))
        ef = r["editor"].winfo_children()[0]
        if not compact:
            r["flags"].pack(in_=o, fill="both", expand=True, pady=(0, 12))
            r["editor"].pack(in_=o, fill="x")
            r["acts1"].pack(in_=ef, fill="x", pady=(8, 0))
            b["play"].pack(in_=r["acts1"], side="left")
            b["save"].pack(in_=r["acts1"], side="right")
            b["ok"].pack(in_=r["acts1"], side="right", padx=(0, 8))
            b["revert"].pack(in_=r["acts1"], side="right", padx=(0, 8))
            self.review_tree.configure(height=15 if not warn_shown else 13)
            self._set_columns(self.review_tree, {"time": 80, "state": 120, "reason": 232, "text": 336})
        else:
            r["cols"].pack(in_=o, fill="both", expand=True)
            r["cols"].columnconfigure(0, weight=1)
            r["cols"].columnconfigure(1, minsize=400)
            r["cols"].rowconfigure(0, weight=1)
            r["flags"].grid(in_=r["cols"], row=0, column=0, sticky="nsew", padx=(0, 12))
            r["editor"].grid(in_=r["cols"], row=0, column=1, sticky="nsew")
            r["acts1"].pack(in_=ef, fill="x", pady=(8, 0))
            r["acts2"].pack(in_=ef, fill="x", pady=(8, 0))
            b["play"].pack(in_=r["acts1"], side="left")
            b["revert"].pack(in_=r["acts1"], side="right")
            b["save"].pack(in_=r["acts2"], side="right")
            b["ok"].pack(in_=r["acts2"], side="right", padx=(0, 8))
            self.review_tree.configure(height=14 if not warn_shown else 12)
            self._set_columns(self.review_tree, {"time": 80, "state": 112, "reason": 184, "text": 260})
        r["hint"].pack(in_=ef, fill="x", pady=(8, 0))

    def _review_editor_state(self, it: dict | None) -> None:
        b = getattr(self, "review_btns", {})
        if it is None:
            for btn in b.values():
                btn.state(["disabled"])
            self._set_chip(self.review_chip, "off", "NO SELECTION")
            return
        cov = it.get("kind") in ("gap", "long_block")
        b["play"].state(["!disabled"])
        for k in ("revert", "ok", "save"):
            b[k].state(["disabled"] if cov else ["!disabled"])
        if cov:
            self._set_chip(self.review_chip, "warning", "MISSED AUDIO" if it["kind"] == "gap" else "STRETCHED")
        elif it.get("original_text") is not None:
            self._set_chip(self.review_chip, "accent", "EDITED")
        elif it.get("reviewed"):
            self._set_chip(self.review_chip, "success", "OK")
        else:
            self._set_chip(self.review_chip, "warning", "TO REVIEW")

    def _review_jump_coverage(self) -> None:
        for iid, it in self._review_items.items():
            if it.get("kind") in ("gap", "long_block"):
                self.review_tree.selection_set(iid)
                self.review_tree.see(iid)
                self.review_tree.focus(iid)
                return

    # ---------- Settings (spec 10) ----------
    SETTINGS_FIELDS = (
        ("asr_engine", "Transcription engine"), ("whisper_gpu_model", "GPU Whisper model"),
        ("whisper_cpu_model", "CPU Whisper model"), ("whisper_language", "Language"),
        ("whisper_decode_profile", "Transcription profile"), ("coverage_max_block_s", "Coverage max block"),
        ("coverage_max_gap_s", "Coverage max gap"), ("prefer_youtube_captions", "YouTube captions"),
        ("whisper_batched", "Fast batched mode"), ("asr_crosscheck", "Voxtral cross-check"),
        ("max_concurrent_jobs", "Max concurrent jobs"), ("heartbeat_seconds", "Heartbeat"),
        ("stall_timeout_seconds", "Stall timeout"), ("start_scan_existing", "Scan-existing"),
        ("skip_duplicates", "Skip duplicates"), ("handoff_try_api_first", "LLM API first"),
        ("log_profile", "Log profile"), ("watch_dir", "Watch folder"), ("completed_video_dir", "Completed videos"),
        ("completed_audio_dir", "Completed audio"), ("library", "Library location"), ("theme", "Theme"),
    )
    THEME_HELP = "Harbor is the original look. Midnight, Graphite and Aurora are alternative skins."
    PROFILE_HELP = {
        "auto": "Transcribes normally; if the result has big gaps or stretched lines, redoes it once with music settings.",
        "speech": "Speech filter on. Best for talks, lectures and podcasts.",
        "music": "Speech filter off, for songs and music videos. Slower.",
        "legacy": "The pre-1.2 decode settings, kept for comparison.",
    }

    def _settings_vars(self) -> dict:
        return {
            "asr_engine": self.set_asr_engine, "whisper_gpu_model": self.set_whisper_gpu,
            "whisper_cpu_model": self.set_whisper_cpu, "whisper_language": self.set_whisper_language,
            "whisper_decode_profile": self.set_whisper_profile, "coverage_max_block_s": self.set_cov_block,
            "coverage_max_gap_s": self.set_cov_gap, "prefer_youtube_captions": self.set_prefer_captions,
            "whisper_batched": self.set_whisper_batched, "asr_crosscheck": self.set_asr_crosscheck,
            "max_concurrent_jobs": self.set_jobs, "heartbeat_seconds": self.set_hb,
            "stall_timeout_seconds": self.set_stall, "start_scan_existing": self.set_scan,
            "skip_duplicates": self.set_skip_duplicates, "handoff_try_api_first": self.set_handoff_api,
            "log_profile": self.set_profile, "watch_dir": self.set_watch_dir, "completed_video_dir": self.set_completed_dir,
            "completed_audio_dir": self.set_completed_audio_dir, "library": self.set_library_dir,
            "theme": self.set_theme,
        }

    def _settings_snapshot(self) -> dict:
        out = {}
        for key, var in self._settings_vars().items():
            try:
                v = var.get()
            except (tk.TclError, ValueError):
                v = "\x00invalid"
            out[key] = str(v).strip() if not isinstance(v, bool) else v
        return out

    def _settings_mark_clean(self) -> None:
        self._settings_saved = self._settings_snapshot()
        self._settings_dirty_update()

    def _settings_dirty_update(self, *_a) -> None:
        if not hasattr(self, "_settings_saved") or not hasattr(self, "_sb"):
            return
        now = self._settings_snapshot()
        labels = dict(self.SETTINGS_FIELDS)
        changed = [k for k in now if now[k] != self._settings_saved.get(k)]
        for key, dot in self._settings_dots.items():
            dot.configure(fg=D_WARNING if key in changed else dot.cget("bg"))
        sb = self._sb
        if changed:
            sb["frame"].configure(highlightbackground=D_WARNING)
            sb["icon"].configure(text="\u25cf", fg=D_WARNING)
            sb["label"].configure(text=f"Unsaved changes ({len(changed)})", fg=D_WARNING)
            sb["detail"].configure(text=", ".join(labels.get(k, k) for k in changed), fg=D_MUTED)
            sb["discard"].state(["!disabled"])
            sb["save"].state(["!disabled"])
        else:
            sb["frame"].configure(highlightbackground=D_BORDER)
            sb["icon"].configure(text="\u2713", fg=D_SUCCESS)
            sb["label"].configure(text="All changes saved", fg=D_SUCCESS)
            when = getattr(self, "_settings_saved_at", None)
            sb["detail"].configure(text=f"Saved to {settings_path(ROOT).name}" + (f" at {when}" if when else ""), fg=D_MUTED)
            sb["discard"].state(["disabled"])
            sb["save"].state(["disabled"])

    def _build_page_settings(self, parent: tk.Frame) -> None:
        page = self.pages["settings"]
        o = tk.Frame(parent, bg=D_BG)
        o.pack(fill="both", expand=True)
        st = self._st = {"outer": o}
        self.set_cov_block = tk.StringVar(value=str(self._settings.get("coverage_max_block_s", 30)))
        self.set_cov_gap = tk.StringVar(value=str(self._settings.get("coverage_max_gap_s", 60)))
        self._settings_dots: dict[str, tk.Label] = {}
        self._settings_spins: dict[str, ttk.Spinbox] = {}
        st["hdr"] = self._page_header(o, "SYSTEM / SETTINGS", "Settings", f"Saved in {settings_path(ROOT).name}; one Save for the page")
        help_labels: list[tk.Label] = []

        def field(parent: tk.Misc, key: str, label: str, make, help_text: str | None = None,
                  help_var: tk.Variable | None = None, help_fg: str = D_MUTED, stacked: bool = False) -> tk.Frame:
            f = tk.Frame(parent, bg=D_SURFACE)
            f.pack(fill="x", pady=(12, 0))
            row = tk.Frame(f, bg=D_SURFACE)
            row.pack(fill="x")
            lab = tk.Frame(row, bg=D_SURFACE, width=136, height=32)
            dot = tk.Label(lab, text="\u25cf", font=("Segoe UI", -8), fg=D_SURFACE, bg=D_SURFACE)
            dot.pack(side="left", padx=(0, 4))
            self._settings_dots[key] = dot
            tk.Label(lab, text=label, font=F_BODY, fg=D_TEXT, bg=D_SURFACE, anchor="w").pack(side="left")
            if stacked:
                lab.pack(side="top", fill="x")
                ctl_parent = tk.Frame(f, bg=D_SURFACE)
                ctl_parent.pack(fill="x", pady=(4, 0))
            else:
                lab.pack(side="left")
                lab.pack_propagate(False)
                ctl_parent = row
            make(ctl_parent)
            if help_text or help_var is not None:
                hl = self._wrap_label(tk.Label(f, text=help_text or "", font=F_BODY, fg=help_fg, bg=D_SURFACE,
                                               anchor="w", justify="left"))
                if help_var is not None:
                    hl.configure(textvariable=help_var)
                hl.pack(fill="x", pady=(4, 0), padx=(0 if stacked else 144, 0))
                help_labels.append(hl)
            return f

        def combo(var, values, readonly=True):
            def make(p):
                ttk.Combobox(p, textvariable=var, values=values, state="readonly" if readonly else "normal", width=1,
                             style="Screen.TCombobox", font=F_BODY).pack(side="left", fill="x", expand=True)
            return make

        def spin(key, var, lo, hi, inc=1):
            def make(p):
                sp = ttk.Spinbox(p, textvariable=var, from_=lo, to=hi, increment=inc, width=8, style="Screen.TSpinbox",
                                 font=F_BODY)
                sp.pack(side="left")
                self._settings_spins[key] = sp
            return make

        def check(parent, key, var, text, help_text=None):
            f = tk.Frame(parent, bg=D_SURFACE)
            f.pack(fill="x", pady=(12, 0))
            row = tk.Frame(f, bg=D_SURFACE)
            row.pack(fill="x")
            ttk.Checkbutton(row, text=text, variable=var, style="Screen.TCheckbutton").pack(side="left")
            dot = tk.Label(row, text="\u25cf", font=("Segoe UI", -8), fg=D_SURFACE, bg=D_SURFACE)
            dot.pack(side="left", padx=(4, 0))
            self._settings_dots[key] = dot
            if help_text:
                hl = self._wrap_label(tk.Label(f, text=help_text, font=F_BODY, fg=D_MUTED, bg=D_SURFACE, anchor="w",
                                               justify="left"))
                hl.pack(fill="x", pady=(4, 0), padx=(24, 0))

        def folder(parent, key, var, label, help_text=None, readonly=False, browse=None):
            def make(p):
                e = ttk.Entry(p, textvariable=var, font=F_VALUE, width=1)
                if readonly:
                    e.configure(state="readonly")
                e.pack(side="left", fill="x", expand=True, ipady=4)
                if browse is not None:
                    ttk.Button(p, text="Browse\u2026", style="Dash.Secondary.TButton", command=browse).pack(
                        side="left", padx=(8, 0))
            st.setdefault("folder_fields", []).append((key, label, make, help_text, parent))

        models = ("tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo", "distil-large-v3")
        # Transcription
        st["tx"], tf = self._dcard(o)
        self._dlabel(tf, "Transcription", font=F_SECTION).pack(fill="x")
        field(tf, "asr_engine", "Transcription engine", combo(self.set_asr_engine, ("whisper", "voxtral-mini-3b", "granite-2b")),
              "whisper runs locally on your GPU or CPU. voxtral-mini-3b and granite-2b are optional local speech models "
              "(install them first).")
        field(tf, "whisper_gpu_model", "GPU Whisper model", combo(self.set_whisper_gpu, models))
        field(tf, "whisper_cpu_model", "CPU Whisper model", combo(self.set_whisper_cpu, models))
        field(tf, "whisper_language", "Language", combo(self.set_whisper_language, ("auto", "en", "de", "es", "fr"), readonly=False),
              "auto detects it, or type a code such as en.")
        self._profile_help_var = tk.StringVar(value="")
        field(tf, "whisper_decode_profile", "Transcription profile",
              combo(self.set_whisper_profile, ("auto", "speech", "music", "legacy")), help_var=self._profile_help_var)
        self.set_whisper_profile.trace_add("write", lambda *_a: self._profile_help_var.set(
            self.PROFILE_HELP.get(self.set_whisper_profile.get(), "")))
        self._profile_help_var.set(self.PROFILE_HELP.get(self.set_whisper_profile.get(), ""))
        # Advanced overrides disclosure: coverage thresholds only (decode profile is the dropdown above)
        disc = tk.Frame(tf, bg=D_SURFACE, cursor="hand2")
        disc.pack(fill="x", pady=(12, 0))
        self._adv_open = tk.BooleanVar(value=True)
        chev = tk.Label(disc, text="\u25be", font=F_NAV, fg=D_ACCENT, bg=D_SURFACE)
        chev.pack(side="left")
        dl = tk.Label(disc, text="Advanced overrides", font=F_NAV, fg=D_ACCENT, bg=D_SURFACE)
        dl.pack(side="left", padx=(8, 0))
        self._adv_summary = tk.Label(disc, text="", font=F_BODY, fg=D_MUTED, bg=D_SURFACE, anchor="w", width=1)
        self._adv_summary.pack(side="left", fill="x", expand=True, padx=(8, 0))
        adv = tk.Frame(tf, bg=D_SURFACE)
        advi = tk.Frame(adv, bg=D_SURFACE)
        advi.pack(fill="x", padx=(24, 0))
        field(advi, "coverage_max_block_s", "Max block (s)", spin("coverage_max_block_s", self.set_cov_block, 5, 3600))
        field(advi, "coverage_max_gap_s", "Max gap (s)", spin("coverage_max_gap_s", self.set_cov_gap, 5, 3600),
              "The missed-audio warning flags a line longer than max block or a silence longer than max gap.")
        self._adv_frame = adv
        self._adv_after = tk.Frame(tf, bg=D_BORDER, height=1)
        self._adv_after.pack(fill="x", pady=(12, 0))

        def toggle_adv(_e=None, force: bool | None = None) -> None:
            open_ = (not self._adv_open.get()) if force is None else force
            if _e is not None:
                self._adv_user_toggled = True
            self._adv_open.set(open_)
            chev.configure(text="\u25be" if open_ else "\u25b8")
            if open_:
                adv.pack(fill="x", before=self._adv_after)
            else:
                adv.pack_forget()
            self._adv_summary.configure(text=f"coverage {self.set_cov_block.get()} s / {self.set_cov_gap.get()} s")

        for w in (disc, chev, dl, self._adv_summary):
            w.bind("<Button-1>", toggle_adv)
        self._toggle_adv = toggle_adv
        toggle_adv(force=True)
        check(tf, "prefer_youtube_captions", self.set_prefer_captions, "Use YouTube captions when available",
              "For YouTube links, uses YouTube's own captions and only runs Whisper when there aren't any.")
        check(tf, "whisper_batched", self.set_whisper_batched, "Fast batched mode (about 4x faster)")
        check(tf, "asr_crosscheck", self.set_asr_crosscheck, "Voxtral cross-check",
              "Runs a second local model on flagged spots and marks lines where the two disagree.")
        # Appearance (v1.3.1 skins)
        st["ap"], apf = self._dcard(o)
        self._dlabel(apf, "Appearance", font=F_SECTION).pack(fill="x")
        self._theme_note_var = tk.StringVar(value=self.THEME_HELP)
        field(apf, "theme", "Theme", combo(self.set_theme, themes.SKIN_NAMES), help_var=self._theme_note_var)
        self.set_theme.trace_add("write", lambda *_a: self._theme_note_var.set(
            self.THEME_HELP if self.set_theme.get() == self._skin else "Click Save changes to apply this theme."))
        # Watcher
        st["wa"], wf = self._dcard(o)
        self._dlabel(wf, "Watcher", font=F_SECTION).pack(fill="x")
        field(wf, "max_concurrent_jobs", "Max concurrent jobs", spin("max_concurrent_jobs", self.set_jobs, 1, 10),
              "Raising this while jobs run can cause contention and stalls.", help_fg=D_WARNING)
        field(wf, "heartbeat_seconds", "Heartbeat (s)", spin("heartbeat_seconds", self.set_hb, 1, 120))
        field(wf, "stall_timeout_seconds", "Stall timeout (s)", spin("stall_timeout_seconds", self.set_stall, 60, 86400))
        check(wf, "start_scan_existing", self.set_scan, "Start with scan-existing")
        check(wf, "skip_duplicates", self.set_skip_duplicates, "Skip files already transcribed")
        # Handoff
        st["ho"], hf = self._dcard(o)
        self._dlabel(hf, "Handoff", font=F_SECTION).pack(fill="x")
        check(hf, "handoff_try_api_first", self.set_handoff_api, "LLM: try API before browser",
              "Sends to the LLM by API first, falling back to the browser.")
        # Advanced
        st["ad"], adf = self._dcard(o)
        ah = tk.Frame(adf, bg=D_SURFACE)
        ah.pack(fill="x")
        self._dlabel(ah, "Advanced", font=F_SECTION).pack(side="left")
        ttk.Button(ah, text="All settings\u2026", style="Dash.SmallSecondary.TButton", command=self._open_all_settings).pack(side="right")
        field(adf, "log_profile", "Log profile", combo(self.set_profile, ("standard", "tuning", "debug")),
              "tuning and debug add resource samples for the Monitor page.")
        sf = tk.Frame(adf, bg=D_SURFACE)
        sf.pack(fill="x", pady=(12, 0))
        self._dlabel(sf, "Settings file", fg=D_MUTED).pack(fill="x")
        self._wrap_label(self._dlabel(sf, str(settings_path(ROOT)), font=F_VALUE, fg=D_ACCENT)).pack(fill="x", pady=(4, 0))
        # Folders
        st["fo"], fof = self._dcard(o)
        st["fo_body"] = fof
        self._dlabel(fof, "Folders", font=F_SECTION).pack(fill="x")
        st["fo_fields"] = tk.Frame(fof, bg=D_SURFACE)
        st["fo_fields"].pack(fill="x")

        def browse_into(var: tk.StringVar, title: str):
            def go() -> None:
                from tkinter import filedialog
                start = var.get().strip()
                p = Path(start) if start else ROOT
                if not p.is_absolute():
                    p = ROOT / p
                chosen = filedialog.askdirectory(parent=self, title=title, initialdir=str(p if p.exists() else ROOT))
                if chosen:
                    var.set(str(Path(chosen)))
            return go

        folder(None, "watch_dir", self.set_watch_dir, "Watch folder", browse=browse_into(self.set_watch_dir, "Watch folder"))
        folder(None, "completed_video_dir", self.set_completed_dir, "Completed videos",
               browse=browse_into(self.set_completed_dir, "Completed videos folder"))
        folder(None, "completed_audio_dir", self.set_completed_audio_dir, "Completed audio",
               "Relative paths are inside the app folder.", browse=browse_into(self.set_completed_audio_dir, "Completed audio folder"))
        folder(None, "library", self.set_library_dir, "Library location",
               "Aeonimus Library: shared with the other Aeonimus apps. Finished transcripts are saved to its transcripts "
               "folder; changing it here changes it for every Aeonimus app (applied on Save).",
               browse=self._browse_library_location)
        folder(None, "_inbox", self.handoff_inbox_var, "Downloads inbox",
               "Where the URL Lister saves link lists (read-only).", readonly=True)
        st["fo_rows"] = []
        for key, label, make, help_text, _p in st["folder_fields"]:
            st["fo_rows"].append(field(st["fo_fields"], key, label, make, help_text, stacked=True))
        # sticky save bar: outside the scroll canvas (child of the ScrollablePage, grid row 1)
        bar = tk.Frame(page, bg=D_SURFACE, highlightthickness=1, highlightbackground=D_BORDER)
        bi = tk.Frame(bar, bg=D_SURFACE)
        bi.pack(fill="both", expand=True, padx=12, pady=7)
        icon = tk.Label(bi, text="\u2713", font=F_NAV, fg=D_SUCCESS, bg=D_SURFACE)
        icon.pack(side="left")
        lbl = tk.Label(bi, text="", font=F_NAV, fg=D_SUCCESS, bg=D_SURFACE)
        lbl.pack(side="left", padx=(8, 0))
        save = ttk.Button(bi, text="Save changes", style="Dash.Primary.TButton", command=self._settings_save)
        save.pack(side="right")
        discard = ttk.Button(bi, text="Discard", style="Dash.Secondary.TButton", command=self._settings_discard)
        discard.pack(side="right", padx=(0, 8))
        detail = tk.Label(bi, text="", font=F_BODY, fg=D_MUTED, bg=D_SURFACE, anchor="w", width=1)
        detail.pack(side="left", fill="x", expand=True, padx=(8, 8))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 16))
        page.inner.configure(pady=16)
        self._sb = {"frame": bar, "icon": icon, "label": lbl, "detail": detail, "save": save, "discard": discard}
        self.settings_save_bar = bar
        st["cols"] = tk.Frame(o, bg=D_BG)
        st["c1"] = tk.Frame(o, bg=D_BG)
        st["c2"] = tk.Frame(o, bg=D_BG)
        st["c3"] = tk.Frame(o, bg=D_BG)
        for w in (st["tx"], st["ap"], st["wa"], st["ho"], st["ad"], st["fo"]):
            w.lift()
        for var in self._settings_vars().values():
            var.trace_add("write", self._settings_dirty_update)
        self._settings_mark_clean()
        self._page_layouts.append(self._layout_settings)
        self._layout_settings(compact=False)

    def _layout_settings(self, *, compact: bool) -> None:
        st = self._st
        o = st["outer"]
        self._forget(*self._header_parts(st["hdr"]), st["tx"], st["ap"], st["wa"], st["ho"], st["ad"], st["fo"], st["cols"],
                     st["c1"], st["c2"], st["c3"])
        self._pack_header(st["hdr"], o)
        cols = st["cols"]
        cols.pack(in_=o, fill="x")
        for i in range(3):
            cols.columnconfigure(i, weight=0, uniform="")
        if not compact:
            cols.columnconfigure(0, weight=1, uniform="sc")
            cols.columnconfigure(1, weight=1, uniform="sc")
            st["c1"].grid(in_=cols, row=0, column=0, sticky="nsew", padx=(0, 6))
            st["c2"].grid(in_=cols, row=0, column=1, sticky="nsew", padx=(6, 0))
            st["tx"].pack(in_=st["c1"], fill="x")
            for w in (st["ap"], st["wa"], st["ho"], st["ad"]):
                w.pack(in_=st["c2"], fill="x", pady=(0, 12))
            st["fo"].pack(in_=o, fill="x", pady=(12, 0))
        else:
            for i in range(3):
                cols.columnconfigure(i, weight=1, uniform="sc")
            st["c1"].grid(in_=cols, row=0, column=0, sticky="nsew", padx=(0, 8))
            st["c2"].grid(in_=cols, row=0, column=1, sticky="nsew", padx=(4, 4))
            st["c3"].grid(in_=cols, row=0, column=2, sticky="nsew", padx=(8, 0))
            st["tx"].pack(in_=st["c1"], fill="x")
            for w in (st["ap"], st["wa"], st["ho"], st["ad"]):
                w.pack(in_=st["c2"], fill="x", pady=(0, 12))
            st["fo"].pack(in_=st["c3"], fill="x")
        if not getattr(self, "_adv_user_toggled", False):
            self._toggle_adv(force=not compact)

    def _settings_values(self) -> tuple[dict | None, list[str]]:
        bad: list[str] = []
        labels = dict(self.SETTINGS_FIELDS)

        def num(key: str, var: tk.Variable, cast, lo: float, hi: float):
            try:
                v = cast(float(str(var.get()).strip()))
            except (tk.TclError, ValueError, TypeError):
                bad.append(key)
                return None
            if not lo <= v <= hi:
                bad.append(key)
                return None
            return v

        vals = {
            "heartbeat_seconds": num("heartbeat_seconds", self.set_hb, float, 1, 120),
            "stall_timeout_seconds": num("stall_timeout_seconds", self.set_stall, float, 60, 86400),
            "max_concurrent_jobs": num("max_concurrent_jobs", self.set_jobs, int, 1, 10),
            "coverage_max_block_s": num("coverage_max_block_s", self.set_cov_block, float, 5, 3600),
            "coverage_max_gap_s": num("coverage_max_gap_s", self.set_cov_gap, float, 5, 3600),
        }
        for key, sp in self._settings_spins.items():
            sp.configure(style="Invalid.TSpinbox" if key in bad else "Screen.TSpinbox")
        if bad:
            return None, [labels.get(k, k) for k in bad]
        for k in ("coverage_max_block_s", "coverage_max_gap_s"):
            if float(vals[k]).is_integer():
                vals[k] = int(vals[k])
        vals.update({
            "log_profile": self.set_profile.get(),
            "start_scan_existing": bool(self.set_scan.get()),
            "watch_dir": self.set_watch_dir.get().strip(),
            "completed_video_dir": self.set_completed_dir.get().strip(),
            "completed_dir": self.set_completed_dir.get().strip(),
            "handoff_try_api_first": bool(self.set_handoff_api.get()),
            "whisper_gpu_model": self.set_whisper_gpu.get().strip(),
            "whisper_cpu_model": self.set_whisper_cpu.get().strip(),
            "whisper_decode_profile": self.set_whisper_profile.get().strip() or "auto",
            "prefer_youtube_captions": bool(self.set_prefer_captions.get()),
            "whisper_language": self.set_whisper_language.get().strip() or "auto",
            "asr_engine": self.set_asr_engine.get().strip() or "whisper",
            "asr_crosscheck": bool(self.set_asr_crosscheck.get()),
            "whisper_batched": bool(self.set_whisper_batched.get()),
            "skip_duplicates": bool(self.set_skip_duplicates.get()),
            "completed_audio_dir": self.set_completed_audio_dir.get().strip(),
            "theme": themes.normalize(self.set_theme.get()),
        })
        return vals, []

    def _settings_save(self) -> None:
        vals, bad = self._settings_values()
        if vals is None:
            sb = self._sb
            sb["icon"].configure(text="\u25cf", fg=D_DANGER_TEXT)
            sb["label"].configure(text="Can't save yet", fg=D_DANGER_TEXT)
            sb["detail"].configure(text="Fix these numbers: " + ", ".join(bad), fg=D_DANGER_TEXT)
            return
        library_changed = self._settings_saved.get("library") != self._settings_snapshot().get("library")
        save_settings(ROOT, vals)
        self._settings = load_settings(ROOT)
        if library_changed:
            self._apply_library_location()  # was the separate "Use this location" button
        self._settings_saved_at = datetime.now().strftime("%H:%M")
        self._refresh_handoff_flags()
        self._settings_mark_clean()
        new_skin = themes.normalize(self._settings.get("theme"))
        if new_skin != self._skin:
            self.apply_skin(new_skin)

    # ---------- Skins (v1.3.1) ----------
    _COLOR_OPTS = ("background", "foreground", "activebackground", "activeforeground", "highlightbackground",
                   "highlightcolor", "insertbackground", "selectbackground", "selectforeground",
                   "disabledforeground", "troughcolor", "selectcolor", "readonlybackground", "disabledbackground")

    def apply_skin(self, name: str) -> int:
        """Switch skin live: rebind colour constants, rebuild ttk styles, recolour plain Tk widgets.

        Returns how many widget options could not be recoloured (0 means fully applied).
        """
        new = themes.normalize(name)
        old = self._skin
        cmap = themes.color_map(old, new)
        globals().update(themes.panel_constants(new))
        self._skin = new
        failed = 0
        for fn in (self._apply_theme, self._apply_screen_styles, self._apply_dash_styles):
            try:
                fn()
            except Exception:  # noqa: BLE001
                failed += 1

        def remap(v: object) -> str | None:
            return cmap.get(str(v).lower()) if isinstance(v, str) and v.startswith("#") else None

        stack: list[tk.Misc] = [self]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if isinstance(w, ttk.Widget):
                if isinstance(w, ttk.Treeview):
                    failed += self._recolor_tags(w, cmap, ("foreground", "background"))
                continue
            for opt in self._COLOR_OPTS:
                try:
                    nv = remap(w.cget(opt))
                except tk.TclError:
                    continue
                if nv:
                    try:
                        w.configure(**{opt: nv})
                    except tk.TclError:
                        failed += 1
            if isinstance(w, tk.Text):
                failed += self._recolor_tags(w, cmap, ("foreground", "background", "selectbackground"))
            elif isinstance(w, tk.Canvas):
                for item in w.find_all():
                    for opt in ("fill", "outline", "activefill"):
                        try:
                            nv = remap(w.itemcget(item, opt))
                        except tk.TclError:
                            continue
                        if nv:
                            w.itemconfigure(item, **{opt: nv})
        for opt, tok in (("*TCombobox*Listbox.background", D_INPUT), ("*TCombobox*Listbox.foreground", D_TEXT),
                         ("*TCombobox*Listbox.selectBackground", D_NAV_SEL),
                         ("*TCombobox*Listbox.selectForeground", D_ACCENT)):
            self.option_add(opt, tok)
        if hasattr(self, "_theme_note_var"):
            self._theme_note_var.set(self.THEME_HELP if not failed else "Restart the control panel to finish applying the theme.")
        self.update_idletasks()
        return failed

    @staticmethod
    def _recolor_tags(w: tk.Misc, cmap: dict, opts: tuple[str, ...]) -> int:
        try:
            tags = w.tk.splitlist(w.tk.call(str(w), "tag", "names"))
        except tk.TclError:
            return 0
        for tag in tags:
            for opt in opts:
                try:
                    v = str(w.tk.call(str(w), "tag", "configure", tag, "-" + opt)[-1] if isinstance(w, tk.Text) else
                            w.tk.call(str(w), "tag", "configure", tag, "-" + opt))
                except tk.TclError:
                    continue
                nv = cmap.get(v.lower()) if v.startswith("#") else None
                if nv:
                    try:
                        w.tk.call(str(w), "tag", "configure", tag, "-" + opt, nv)
                    except tk.TclError:
                        return 1
        return 0

    def _settings_discard(self) -> None:
        try:
            self.set_library_dir.set(str(aeonimus_handoff.load_config().library))
        except Exception:  # noqa: BLE001
            pass
        self._sync_settings_page_vars()
        for sp in self._settings_spins.values():
            sp.configure(style="Screen.TSpinbox")
        self._settings_dirty_update()


    def _run_search(self) -> None:
        q = self.search_var.get().strip()
        self.search_tree.delete(*self.search_tree.get_children())
        self._search_hits = {}
        self._search_buttons()
        if not q:
            return
        hits = transcript_search.search(q)
        for h in hits:
            t = int(h["start"])
            iid = self.search_tree.insert("", "end", values=(h["stem"], f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}", h["context"]))
            self._search_hits[iid] = h
        videos = len({h["stem"] for h in hits})
        self.search_status_var.set(f"{len(hits)} matches in {videos} videos" if hits
                                   else f'0 matches for "{q}" (check the spelling, or Rebuild index)')
        self._search_buttons()

    def _selected_hit(self) -> dict | None:
        sel = self.search_tree.selection()
        return self._search_hits.get(sel[0]) if sel else None

    def _search_open_video(self) -> None:
        h = self._selected_hit()
        if not h:
            return
        video = h.get("video_path")
        if not video or not os.path.isfile(video):
            d = self._search_dirs()
            found = transcript_search.find_video(h["stem"], [d["completed_videos"], self._watch_dir(), ROOT / "watch_incoming_staging"])
            video = str(found) if found else None
            if video:
                transcript_search.set_video_path(h["stem"], video)
        if not video:
            if messagebox.askyesno("Video not found", f"No video file was found for:\n{h['stem']}\n\nOpen the transcript instead?"):
                self._search_open_transcript()
            return
        used = transcript_search.open_media_at(video, h["start"])
        self.search_status_var.set(f"opened in {used} at {int(h['start'])} s")

    def _search_open_transcript(self) -> None:
        h = self._selected_hit()
        if not h:
            return
        for p in (h.get("txt_path"), h.get("json_path")):
            if p and os.path.isfile(p):
                open_path(Path(p))
                return
        messagebox.showinfo("Transcript", "The transcript file could not be found.")

    def _search_rebuild(self) -> None:
        self.search_status_var.set("rebuilding index ...")
        d = self._search_dirs()

        def work() -> None:
            try:
                r = transcript_search.backfill(d["completed_json"], d["completed_transcripts"],
                                               [d["completed_videos"], self._watch_dir(), ROOT / "watch_incoming_staging"])
                n, segs = transcript_search.stats()
                msg = f"rebuilt: {r['indexed']} transcripts, {segs} segments" + (f", {r['failed']} failed" if r["failed"] else "")
            except Exception as exc:
                msg = f"rebuild failed: {exc}"
            self.after(0, lambda: (self.search_status_var.set(msg), self.search_index_var.set(
                f"index: {n} transcripts, {segs} segments" if not msg.startswith("rebuild failed") else "")))

        threading.Thread(target=work, daemon=True).start()

    def _review_dirs(self) -> dict:
        return {k: str(v) for k, v in self._search_dirs().items()}

    def _review_refresh_list(self) -> None:
        try:
            items = transcript_review.list_transcripts(self._review_dirs()["completed_json"])
        except Exception as exc:
            self.review_status_var.set(f"could not list transcripts: {exc}")
            return
        items.sort(key=lambda t: (-(t["flagged"] - t["reviewed"]), t["stem"].lower()))
        self._review_index = {}
        labels = []
        for t in items:
            label = f"{t['stem']}   [{t['flagged'] - t['reviewed']} to review / {t['flagged']} flagged, {t['edited']} edited]"
            self._review_index[label] = t
            labels.append(label)
        self.review_combo["values"] = labels
        cur_stem = (self._review_index_prev.get(self.review_stem_var.get()) or {}).get("stem") if hasattr(self, "_review_index_prev") else None
        match = [l for l, t in self._review_index.items() if t["stem"] == cur_stem]
        self.review_stem_var.set(match[0] if match else (labels[0] if labels else ""))
        self._review_index_prev = dict(self._review_index)
        total = sum(t["flagged"] - t["reviewed"] for t in items)
        self.review_status_var.set(f"{len(items)} transcripts, {total} spots to review")
        if not getattr(self, "_review_keep_rows", False):
            self._review_load_flags()

    def _review_current(self) -> dict | None:
        return self._review_index.get(self.review_stem_var.get())

    def _review_load_flags(self, keep_index: int | None = None) -> None:
        tree = self.review_tree
        tree.delete(*tree.get_children())
        self._review_items = {}
        t = self._review_current()
        self._review_editor_state(None)
        if not t:
            self._review_set_warning([])
            self.review_progress_var.set("")
            return
        try:
            cov = transcript_review.coverage_items(t["json_path"])
        except Exception:  # noqa: BLE001
            cov = []
        rows = [dict(w, index=None, text=w.get("text", ""), original_text=None, reviewed=False) for w in cov]
        flagged = transcript_review.flagged_items(t["json_path"])
        rows += flagged
        rows.sort(key=lambda r: float(r.get("start") or 0))
        for it in rows:
            s = int(float(it["start"] or 0))
            stamp = f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
            if it.get("kind") in ("gap", "long_block"):
                rng = f"{coverage_check.mmss(it['start'])}\u2013{coverage_check.mmss(it['end'])}"
                state = "\u26a0 MISSED AUDIO" if it["kind"] == "gap" else "\u26a0 STRETCHED"
                why = f"long gap {rng}" if it["kind"] == "gap" else f"one line stretched over {rng}"
                it["reason"] = why
                iid = tree.insert("", "end", values=(stamp, state, why, it.get("text") or ""), tags=("warn",))
            else:
                state = "edited" if it["original_text"] is not None else ("OK" if it["reviewed"] else "to review")
                tag = "st_accent" if state == "edited" else ("st_success" if state == "OK" else "")
                iid = tree.insert("", "end", values=(stamp, state, it["reason"][:80], it["text"]),
                                  tags=(tag,) if tag else ())
                if keep_index is not None and it["index"] == keep_index:
                    tree.selection_set(iid)
                    tree.see(iid)
            self._review_items[iid] = it
        done = sum(1 for it in flagged if it["reviewed"] or it["original_text"] is not None)
        self.review_progress_var.set(f"{done} of {len(flagged)} reviewed" if flagged else "")
        self._review_set_warning(cov)

    def _review_set_warning(self, cov: list) -> None:
        if cov:
            first = cov[0]
            self.review_warn_title.set(coverage_check.describe(first))
            more = f" \u00b7 {len(cov) - 1} more spot{'s' if len(cov) != 2 else ''}" if len(cov) > 1 else ""
            self.review_warn_detail.set(("long gap" if first.get("kind") == "gap" else "one line stretched over this span")
                                        + more + " \u00b7 try the music profile in Settings")
        else:
            self.review_warn_title.set("")
            self.review_warn_detail.set("")
        shown = self._r["warn"].winfo_manager() != ""
        if shown != bool(cov):
            self._layout_review(compact=bool(self._dash_compact))

    def _review_selected(self) -> dict | None:
        sel = self.review_tree.selection()
        return self._review_items.get(sel[0]) if sel else None

    def _review_select(self) -> None:
        it = self._review_selected()
        self._review_editor_state(it)
        if not it:
            return
        self.review_text.delete("1.0", "end")
        self.review_text.insert("1.0", it["text"])
        orig = it["original_text"]
        self.review_orig_var.set(str(orig).strip() if orig is not None else str(it["text"]).strip() or "\u2014")
        self.review_why_var.set(it["reason"])

    def _review_play(self) -> None:
        it, t = self._review_selected(), self._review_current()
        if not it or not t:
            return
        d = self._review_dirs()
        folders = [d.get("completed_videos"), d.get("completed_audio"), str(self._watch_dir()), str(ROOT / "watch_incoming_staging"),
                   str(ROOT / "skipped_duplicates"), str(ROOT / "completed_videos"), str(ROOT / "completed_audio")]
        video = transcript_review.find_media(t["stem"], folders)
        if not video:
            messagebox.showinfo("Play snippet", f"No video/audio file was found for:\n{t['stem']}\n\nLooked in the search index and:\n"
                                + "\n".join(f for f in folders if f))
            return
        used = transcript_review.play_snippet(str(video), it["start"], it["end"])
        self.review_status_var.set(f"playing {it['start']:.0f}-{it['end']:.0f} s in {used}")

    def _review_apply(self, action) -> None:
        it, t = self._review_selected(), self._review_current()
        if not it or not t or it.get("index") is None:
            messagebox.showinfo("Review", "Select a flagged segment first.")
            return
        try:
            action(t["stem"], self._review_dirs(), it["index"])
        except Exception as exc:
            messagebox.showerror("Review", f"Could not save: {exc}")
            return
        self._review_keep_rows = True
        try:
            self._review_refresh_list()  # updates the "to review / edited" counts
        finally:
            self._review_keep_rows = False
        self._review_load_flags(keep_index=it["index"])
        self._review_select()
        self.review_status_var.set("saved; .txt, .csv, subtitles and search index updated")

    def _review_save(self) -> None:
        text = self.review_text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("Review", "The corrected text is empty.")
            return
        self._review_apply(lambda stem, d, i: transcript_review.save_correction(stem, d, i, text))

    def _review_mark_ok(self) -> None:
        self._review_apply(transcript_review.mark_reviewed)

    def _review_revert(self) -> None:
        self._review_apply(transcript_review.revert)

    def _open_unified_log(self) -> None:
        try:
            open_path(support_tools.build_unified_log(self._watch_dir()))
        except Exception as exc:
            messagebox.showerror("Unified log", str(exc))

    def _copy_for_support(self) -> None:
        try:
            status = json.loads((self._watch_dir() / "watch_status.json").read_text(encoding="utf-8"))
        except Exception:
            status = None
        text = support_tools.support_report(self._watch_dir(), load_settings(ROOT), status, (status or {}).get("health"))
        self.clipboard_clear()
        self.clipboard_append(text)
        out = support_tools.LOG_DIR / "support_report.txt"
        try:
            out.parent.mkdir(exist_ok=True)
            out.write_text(text, encoding="utf-8")
        except OSError:
            pass
        messagebox.showinfo("Copy for support", f"Support report copied to the clipboard (API keys hidden).\nAlso saved to {out}")

    def _open_all_settings(self) -> None:
        from watcher_settings import DEFAULT_SETTINGS
        win = tk.Toplevel(self)
        win.title("All settings")
        win.configure(bg=BG_MAIN)
        win.geometry("860x720")
        canvas = tk.Canvas(win, bg=BG_MAIN, highlightthickness=0)
        sb = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=BG_MAIN)
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        bar = tk.Frame(win, bg=BG_MAIN)
        bar.pack(side="bottom", fill="x", padx=16, pady=10)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        current = load_settings(ROOT)
        widgets: dict[str, tk.Variable] = {}
        sections: dict[str, list[str]] = {}
        for key in DEFAULT_SETTINGS:
            sections.setdefault(support_tools.describe(key)[0], []).append(key)
        r = 0
        for sec in [s for s in support_tools.SECTION_ORDER if s in sections]:
            tk.Label(body, text=sec, fg=ACCENT, bg=BG_MAIN, font=(FONT_MONO, 12, "bold")).grid(row=r, column=0, sticky="w", padx=16, pady=(14, 4))
            r += 1
            for key in sections[sec]:
                _sec, label, help_text = support_tools.describe(key)
                default = DEFAULT_SETTINGS[key]
                tk.Label(body, text=label, fg=FG, bg=BG_MAIN, anchor="w").grid(row=r, column=0, sticky="w", padx=(28, 10), pady=3)
                if isinstance(default, bool):
                    var = tk.BooleanVar(value=bool(current.get(key, default)))
                    ttk.Checkbutton(body, variable=var).grid(row=r, column=1, sticky="w")
                else:
                    var = tk.StringVar(value=str(current.get(key, default)))
                    tk.Entry(body, textvariable=var, width=44, bg=FIELD_BG, fg=FG, insertbackground=FG,
                             show="*" if support_tools.is_secret(key) else "").grid(row=r, column=1, sticky="w")
                tk.Label(body, text=help_text, fg=FG_DIM, bg=BG_MAIN, anchor="w", wraplength=300, justify="left").grid(row=r, column=2, sticky="w", padx=10)
                widgets[key] = var
                r += 1

        def save_all() -> None:
            out, bad = {}, []
            for key, var in widgets.items():
                try:
                    out[key] = support_tools.coerce(var.get(), DEFAULT_SETTINGS[key])
                except (TypeError, ValueError):
                    bad.append(support_tools.describe(key)[1])
            if bad:
                messagebox.showerror("All settings", "Please fix these values (numbers expected):\n" + "\n".join(bad), parent=win)
                return
            save_settings(ROOT, out)
            self._settings = load_settings(ROOT)
            self._sync_settings_page_vars()
            messagebox.showinfo("All settings", "Saved. The watcher picks most changes up on the next file; folder changes after a restart.", parent=win)

        ttk.Button(bar, text="Save all", style="Accent.TButton", command=save_all).pack(side="right")
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right", padx=(0, 10))
        tk.Label(bar, text=f"Stored in {settings_path(ROOT).name}", fg=FG_DIM, bg=BG_MAIN).pack(side="left")
        self._all_settings_win = win

    def _settings_row(
        self,
        grid: tk.Frame,
        row: int,
        label: str,
        var: tk.Variable,
        *,
        spin: bool = False,
        minv: float = 1,
        maxv: float = 100,
        intv: bool = False,
    ) -> None:
        tk.Label(grid, text=label, fg=FG_DIM, bg=BG_CARD, anchor="w").grid(
            row=row, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        if spin:
            tk.Spinbox(
                grid,
                textvariable=var,
                from_=minv,
                to=maxv,
                increment=1 if intv else 1.0,
                width=10,
                bg=BG_LOG,
                fg=FG,
                buttonbackground=BG_CARD,
                highlightthickness=1,
                highlightbackground=CARD_HILITE,
            ).grid(row=row, column=1, sticky="w", pady=6)

    def _start_watcher(self) -> None:
        self._settings = load_settings(ROOT)
        scan = bool(self._settings.get("start_scan_existing", True))
        args = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(START_SCRIPT),
            "-WatchDir",
            str(self._watch_dir()),
        ]
        if str(self._settings.get("completed_video_dir") or self._settings.get("completed_dir") or "").strip():
            args += ["-CompletedDir", str(self._completed_dir())]
        if scan:
            args.append("-ScanExisting")
        subprocess.Popen(args, cwd=str(ROOT), shell=False)

    def _stop_watcher(self) -> None:
        self._settings = load_settings(ROOT)
        subprocess.Popen(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(STOP_SCRIPT),
                "-WatchDir",
                str(self._watch_dir()),
            ],
            cwd=str(ROOT),
            shell=False,
        )

    def _watch_dir(self) -> Path:
        return resolved_watch_dir(ROOT, self._settings)

    def _completed_dir(self) -> Path:
        return resolved_completed_dir(ROOT, self._settings)

    def _transcript_dir(self) -> Path:
        return resolved_completed_transcript_dir(ROOT, self._settings)

    def _csv_dir(self) -> Path:
        return resolved_completed_csv_dir(ROOT, self._settings)

    def _json_dir(self) -> Path:
        return resolved_completed_json_dir(ROOT, self._settings)

    def _status_path(self) -> Path:
        return self._watch_dir() / "watch_status.json"

    def _progress_files(self) -> list[Path]:
        return sorted(
            self._watch_dir().glob(".watch_progress_*.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )

    @staticmethod
    def _read_json_file(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _find_reconcile_candidate(self) -> dict | None:
        for path in self._progress_files():
            data = self._read_json_file(path)
            if data.get("status") != "completed":
                continue
            src_path = str(data.get("input") or "").strip()
            stem = str(data.get("stem") or "").strip()
            if not src_path or not stem:
                continue
            src = Path(src_path)
            if not src.is_file():
                continue
            txt = self._transcript_dir() / f"{stem}.txt"
            csv = self._csv_dir() / f"{stem}.csv"
            jsn = self._json_dir() / f"{stem}.json"
            if not (txt.is_file() and csv.is_file() and jsn.is_file()):
                continue
            dest = self._completed_dir() / f"{stem}{src.suffix}"
            return {
                "progress_file": path,
                "src": src,
                "stem": stem,
                "txt": txt,
                "csv": csv,
                "json": jsn,
                "dest": dest,
            }
        return None

    def _reconcile_finished_job(self) -> None:
        status = self._read_status()
        pid = int(status.get("pid") or 0)
        if bool(status.get("running")) and process_alive(pid):
            messagebox.showinfo(
                "Reconcile finished job",
                "Stop the watcher first. Reconcile is only available while the watcher is off.",
            )
            return

        candidate = self._find_reconcile_candidate()
        if not candidate:
            messagebox.showinfo(
                "Reconcile finished job",
                "No completed progress file with matching transcript outputs was found in the watch folder.",
            )
            return

        src = candidate["src"]
        dest = candidate["dest"]
        moved_video = False
        if dest.exists():
            # The source may already have been moved manually; still reconcile the stuck status.
            pass
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            moved_video = True

        sp = self._status_path()
        if sp.exists():
            data = self._read_json_file(sp)
            already_recorded = str(data.get("last_completed_video") or "") == str(dest)
            data["running"] = False
            data["status"] = "stopped"
            data["phase"] = "off"
            data["pid"] = None
            data["current_file"] = None
            data["last_worker_pid"] = None
            data["active_jobs"] = []
            data["queue_count"] = max(0, int(data.get("queue_count") or 0) - 1)
            if not already_recorded:
                data["processed_count"] = int(data.get("processed_count") or 0) + 1
                data["completed_count"] = int(data.get("completed_count") or 0) + 1
            data["last_error"] = None
            data["last_event"] = "manual_recovery_completed" if moved_video else "manual_recovery_reconciled"
            data["last_completed_video"] = str(dest)
            atomic_write_json(sp, data)

        self.refresh_once()
        messagebox.showinfo(
            "Reconcile finished job",
            (
                f"Recovered completed outputs and moved the source video to:\n{dest}"
                if moved_video
                else f"Recovered completed outputs and reconciled the stuck watcher state for:\n{dest}"
            ),
        )

    # ------------------------------------------------ Aeonimus handoff (easy button)

    def _build_easy_import(self, parent: tk.Misc, *, compact: bool = False, dash: bool = False) -> tk.Frame:
        if dash:
            card, body = self._dcard(parent, border=D_ACCENT)
            row = tk.Frame(body, bg=D_SURFACE)
            row.pack(fill="x")
            title = self._dlabel(row, "From the URL Lister", font=F_SECTION)
            title.configure(width=24)
            title.pack(side="left")
            ttk.Button(row, text="Import from URL Lister", style="Dash.Primary.TButton", command=self._easy_import).pack(
                side="left", padx=(8, 0))
            ttk.Button(row, text="Open handoff folder", style="Dash.Secondary.TButton",
                       command=self._open_handoff_folder).pack(side="left", padx=(8, 0))
            lbl = tk.Label(body, textvariable=self.easy_status_var, fg=D_MUTED, bg=D_SURFACE, font=F_BODY,
                           anchor="w", justify="left", wraplength=700)
            lbl.pack(fill="x", pady=(8, 0))
            self._autowrap(lbl)
            self._easy_status_labels.append(lbl)
            return card
        card = tk.Frame(parent, bg=BG_CARD, highlightthickness=1, highlightbackground=ACCENT)
        body = tk.Frame(card, bg=BG_CARD)
        body.pack(fill="x", padx=18, pady=10 if compact else 16)
        row = tk.Frame(body, bg=BG_CARD)
        row.pack(fill="x")
        self._headline(row, "From the URL Lister", size=13 if compact else 16, fg=ACCENT).pack(side="left")
        ttk.Button(row, text="Import from URL Lister", style="Accent.TButton", command=self._easy_import).pack(
            side="left", padx=(14, 0), ipadx=10, ipady=3
        )
        ttk.Button(row, text="Open handoff folder", command=self._open_handoff_folder).pack(side="left", padx=(8, 0))
        lbl = tk.Label(body, textvariable=self.easy_status_var, fg=FG_DIM, bg=BG_CARD, font=(FONT_BODY, 9),
                       anchor="w", justify="left", wraplength=700)
        lbl.pack(fill="x", pady=(4, 0))
        self._autowrap(lbl)
        self._easy_status_labels.append(lbl)
        return card

    def _library_transcripts_dir(self) -> Path:
        try:
            cfg = aeonimus_handoff.load_config()
        except Exception:  # noqa: BLE001
            cfg = aeonimus_handoff.HandoffConfig(aeonimus_handoff.default_inbox(), Path(self.set_library_dir.get()))
        return cfg.library_dir("transcripts")

    def _open_library_transcripts(self) -> None:
        d = self._library_transcripts_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        open_path(d)

    def _build_latest_transcripts(self, parent: tk.Frame) -> None:
        """Latest transcripts card body: newest 5 from the library, double-click opens, > shows in folder."""
        tv = ttk.Treeview(parent, style="Latest.Treeview", show="tree", columns=("time", "chev"), height=5,
                          selectmode="browse")
        tv.column("#0", width=180, stretch=True, anchor="w")
        tv.column("time", width=96, stretch=False, anchor="e")
        tv.column("chev", width=20, stretch=False, anchor="center")
        tv.tag_configure("hover", background=D_RAISED)
        tv.tag_configure("empty", foreground=D_MUTED)
        tv.tag_configure("warn", background=D_WARNING_SURFACE, foreground=D_WARNING)
        tv.pack(fill="x")
        self.latest_list = tv
        tv.bind("<Double-1>", lambda _e: self._open_latest_selected())
        tv.bind("<ButtonRelease-1>", self._latest_click)
        tv.bind("<Motion>", self._latest_hover)
        tv.bind("<Leave>", lambda _e: [tv.item(i, tags=()) for i in tv.get_children() if "hover" in tv.item(i, "tags")])
        menu = tk.Menu(tv, tearoff=False)
        menu.add_command(label="Open", command=self._open_latest_selected)
        menu.add_command(label="Show in folder", command=self._show_latest_in_folder)
        menu.add_command(label="Open transcripts folder", command=self._open_library_transcripts)

        def popup(e: tk.Event) -> None:
            row = tv.identify_row(e.y)
            if row:
                tv.selection_set(row)
            menu.tk_popup(e.x_root, e.y_root)

        tv.bind("<Button-3>", popup)
        self._latest_paths: list[Path] = []
        self._latest_sig: tuple = ()
        self._latest_checked = 0.0
        self._refresh_latest_transcripts(force=True)

    def _latest_hover(self, e: tk.Event) -> None:
        tv = self.latest_list
        row = tv.identify_row(e.y)
        for i in tv.get_children():
            tags = tv.item(i, "tags")
            if i == row and "empty" not in tags and "warn" not in tags and "hover" not in tags:
                tv.item(i, tags=("hover",))
            elif i != row and "hover" in tags:
                tv.item(i, tags=())

    def _latest_click(self, e: tk.Event) -> None:
        tv = self.latest_list
        if tv.identify_column(e.x) == "#2" and tv.identify_row(e.y):
            tv.selection_set(tv.identify_row(e.y))
            self._show_latest_in_folder()

    def _refresh_latest_transcripts(self, force: bool = False) -> None:
        tv = getattr(self, "latest_list", None)
        if tv is None:
            return
        now = datetime.now().timestamp()
        if not force and now - self._latest_checked < 5.0:
            return
        self._latest_checked = now
        paths = aeonimus_handoff.latest_transcripts(self._library_transcripts_dir(), limit=5)
        sig = tuple((str(p), aeonimus_handoff.file_mtime(p)) for p in paths)
        if sig == self._latest_sig and not force:
            return
        self._latest_sig = sig
        self._latest_paths = paths
        self._update_coverage_warning(paths)
        try:
            tv.delete(*tv.get_children())
            if not paths:
                tv.insert("", "end", iid="empty", text="No finished transcripts in the library yet.", values=("", ""),
                          tags=("empty",))
            for i, p in enumerate(paths):
                ts = aeonimus_handoff.file_mtime(p)
                when = datetime.fromtimestamp(ts).strftime("%b %d %H:%M") if ts else ""
                warned = str(p) in getattr(self, "_latest_warned", set())
                tv.insert("", "end", iid=str(i), text=("\u26a0 " if warned else "") + p.name, values=(when, "\u203a"),
                          tags=("warn",) if warned else ())
        except tk.TclError:
            pass

    @staticmethod
    def _profile_label(raw) -> str:
        p = str(raw or "auto").strip().lower()
        return "speech" if p == "robust" else (p if p in ("auto", "speech", "music", "legacy") else "auto")

    def _update_coverage_warning(self, paths: list[Path]) -> None:
        """'Possibly missed audio at mm:ss-mm:ss' for the newest transcripts that have coverage warnings."""
        lab = getattr(self, "_coverage_label", None)
        if lab is None:
            return
        lines = []
        self._latest_warned = set()
        for p in paths:
            try:
                data = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            warns = coverage_check.warnings_from_json_data(data)
            if warns:
                self._latest_warned.add(str(p))
                spans = ", ".join(coverage_check.describe(w).replace("Possibly missed audio at ", "") for w in warns[:3])
                more = f" (+{len(warns) - 3} more)" if len(warns) > 3 else ""
                lines.append(f"\u26a0 {p.stem[:48]}: Possibly missed audio at {spans}{more}")
        self.coverage_warning_var.set("\n".join(lines[:3]))
        try:
            if lines:
                lab.pack(fill="x", pady=(6, 0))
            else:
                lab.pack_forget()
        except tk.TclError:
            pass

    def _youtube_captions(self, link) -> dict | None:
        """Runs on the download thread: captions first when the setting is on, else None (download + Whisper)."""
        s = self._settings or {}
        if not s.get("prefer_youtube_captions", True):
            return None
        dirs = {k: str(v) for k, v in resolved_output_dirs(ROOT, s).items()}
        try:
            block = float(s.get("coverage_max_block_s") or 30)
            gap = float(s.get("coverage_max_gap_s") or 60)
        except (TypeError, ValueError):
            block, gap = 30.0, 60.0
        return youtube_captions.try_captions(
            link.url, link.id, dirs, lang=str(s.get("youtube_transcript_lang") or "en"),
            max_block=block, max_gap=gap, ydl_opts=youtube_intake._ydl_base_opts(), log=self.youtube._log)

    def _selected_latest(self) -> Path | None:
        if not getattr(self, "_latest_paths", None):
            return None
        sel = self.latest_list.selection()
        try:
            idx = int(sel[0]) if sel else 0
        except ValueError:
            return None
        return self._latest_paths[idx] if idx < len(self._latest_paths) else None

    def _open_latest_selected(self) -> None:
        p = self._selected_latest()
        if p is not None and p.exists():
            open_path(p)

    def _show_latest_in_folder(self) -> None:
        p = self._selected_latest()
        if p is None or not p.exists():
            self._open_library_transcripts()
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            open_path(p.parent)

    def _easy_status(self, text: str, color: str = FG_DIM) -> None:
        self.easy_status_var.set(text)
        for lbl in self._easy_status_labels:
            try:
                lbl.configure(fg=color)
            except tk.TclError:
                pass

    def _handoff_config(self) -> "aeonimus_handoff.HandoffConfig":
        cfg = aeonimus_handoff.load_config()
        aeonimus_handoff.ensure_folders(cfg)
        return cfg

    def _open_handoff_folder(self) -> None:
        try:
            cfg = self._handoff_config()
        except OSError:
            messagebox.showerror("Handoff folder", "The Aeonimus Library folder couldn't be created. Check the Library location in Settings.")
            return
        open_path(cfg.library)

    def _watcher_is_running(self) -> bool:
        data = self._read_status()
        return bool(data.get("running", False)) and process_alive(int(data.get("pid") or 0))

    def _easy_import(self) -> None:
        """Easy button: move new link lists from the inbox, import the newest new one, start transcribing."""
        try:
            cfg = self._handoff_config()
            ledger = aeonimus_handoff.ImportLedger()
            pick = aeonimus_handoff.pick_link_list(cfg, ledger)
        except OSError:
            self._easy_status("The Aeonimus Library folder couldn't be opened. Check the Library location in Settings.", ACCENT_RED)
            return
        if pick.path is None:
            self._easy_status(
                "No link lists yet. In the URL Lister, export a CSV (with \"Save exports to the Aeonimus handoff folder\" on), then click again.",
                ACCENT_RED,
            )
            return
        if not pick.is_new:
            again = messagebox.askyesno(
                "Import from URL Lister",
                f"There's no new link list since last time.\n\nImport the newest one again?\n{pick.path.name}",
                parent=self,
            )
            if not again:
                self._easy_status("Nothing new to import. Export a new CSV from the URL Lister, then click again.")
                return
        try:
            result = youtube_intake.read_links_file(pick.path)
        except (youtube_intake.LinkError, OSError) as exc:
            msg = str(exc) if isinstance(exc, youtube_intake.LinkError) else "That file couldn't be opened."
            self._easy_status(f"{pick.path.name}: {msg}", ACCENT_RED)
            return
        n = len(result.links)
        if not n:
            ledger.mark(pick.path)
            self._easy_status(f"{pick.path.name} has no video, playlist or Suno song links to transcribe.", ACCENT_RED)
            return
        dest, known = self._youtube_dirs()
        added, repeats = self.youtube.add(result.links, dest, known)
        ledger.mark(pick.path)
        started = False
        if not self._watcher_is_running():
            try:
                self._start_watcher()
                started = True
            except OSError:
                self._easy_status(f"Imported {pick.path.name}, but the watcher couldn't start. Press Start watcher.", ACCENT_RED)
                return
        lead = f"Imported {pick.path.name}: {n} link{'s' if n != 1 else ''}."
        detail = youtube_intake.describe_intake(result, added, repeats)
        tail = " Started the watcher; transcripts appear as each download finishes." if started else " The watcher is running; transcripts appear as each download finishes."
        self._easy_status(f"{lead} {detail}{tail}", ACCENT_GREEN)

    def _apply_library_location(self) -> None:
        new_raw = self.set_library_dir.get().strip().strip('"')
        if not new_raw:
            messagebox.showerror("Library location", "Choose a folder for the Aeonimus Library first.", parent=self)
            return
        new = Path(os.path.expandvars(new_raw))
        try:
            old = aeonimus_handoff.load_config().library
        except Exception:  # noqa: BLE001
            old = aeonimus_handoff.default_library()
        same = os.path.normcase(str(old.resolve())) == os.path.normcase(str(new.resolve()))
        try:
            cfg = aeonimus_handoff.save_config(library=new)
            aeonimus_handoff.ensure_folders(cfg)
        except OSError:
            messagebox.showerror("Library location", f"Couldn't use that folder:\n{new}\n\nPick a folder you can write to.", parent=self)
            return
        self.set_library_dir.set(str(cfg.library))
        if same:
            messagebox.showinfo("Library location", "Saved. The library location hasn't changed.", parent=self)
            return
        msg = f"Saved. Every Aeonimus app now uses:\n{cfg.library}"
        if aeonimus_handoff.library_has_files(old) and messagebox.askyesno(
            "Library location",
            f"Move the existing library files to the new place?\n\nFrom: {old}\nTo: {cfg.library}\n\nNothing is overwritten; files with the same name get a number added.",
            parent=self,
        ):
            moved, failed = aeonimus_handoff.move_library(old, cfg.library)
            msg += f"\n\nMoved {moved} file{'s' if moved != 1 else ''}."
            if failed:
                msg += f" {failed} couldn't be moved (maybe open in another app) and stayed in the old folder."
        messagebox.showinfo("Library location", msg, parent=self)

    def _browse_library_location(self) -> None:
        from tkinter import filedialog

        start = self.set_library_dir.get().strip() or str(aeonimus_handoff.default_library())
        chosen = filedialog.askdirectory(parent=self, title="Choose the Aeonimus Library folder",
                                         initialdir=start if Path(start).exists() else str(Path.home()))
        if chosen:
            self.set_library_dir.set(str(Path(chosen)))  # applied by Save changes (was "Use this location")

    def _toggle_watcher_power(self) -> None:
        self._settings = load_settings(ROOT)
        data = self._read_status()
        running_flag = bool(data.get("running", False))
        pid = int(data.get("pid") or 0)
        alive = running_flag and process_alive(pid)
        if running_flag and alive:
            self._stop_watcher()
        else:
            self._start_watcher()

    def _maybe_auto_handoff(self, data: dict) -> None:
        """Automatic handoff popups are disabled; routing is manual from the control panel."""
        completed = data.get("last_completed_video")
        self._last_handoff_completed_path = str(completed) if completed else None
        self._handoff_initialized = True

    def _clear_last_error(self) -> None:
        sp = self._status_path()
        if not sp.exists():
            return
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            data["last_error"] = None
            atomic_write_json(sp, data)
        except (json.JSONDecodeError, OSError):
            pass
        self.refresh_once()

    def _open_settings(self) -> None:
        self._settings = load_settings(ROOT)
        win = tk.Toplevel(self)
        win.title("Handoff & advanced settings")
        win.geometry("560x900")
        win.minsize(520, 640)
        win.transient(self)
        win.configure(bg=BG_MAIN)

        pad = ttk.Frame(win, padding=12)
        pad.pack(fill="both", expand=True)

        hb = tk.DoubleVar(value=float(self._settings["heartbeat_seconds"]))
        stall = tk.DoubleVar(value=float(self._settings["stall_timeout_seconds"]))
        jobs = tk.IntVar(value=int(self._settings["max_concurrent_jobs"]))
        profile = tk.StringVar(value=str(self._settings.get("log_profile", "standard")))
        scan = tk.BooleanVar(value=bool(self._settings.get("start_scan_existing", True)))
        ask = tk.BooleanVar(value=bool(self._settings.get("post_transcript_always_ask", False)))
        dest = tk.StringVar(value=str(self._settings.get("default_post_transcript_destination", "notebooklm")))
        prov = tk.StringVar(value=str(self._settings.get("default_llm_provider", "chatgpt")))
        lm_url = tk.StringVar(value=str(self._settings.get("lm_studio_base_url", "")))
        lm_model = tk.StringVar(value=str(self._settings.get("lm_studio_model", "")))
        handoff_api = tk.BooleanVar(value=bool(self._settings.get("handoff_try_api_first", False)))
        wdir = tk.StringVar(value=str(self._settings.get("watch_dir", "watch_incoming")))
        cdir = tk.StringVar(
            value=str(self._settings.get("completed_video_dir") or self._settings.get("completed_dir", ""))
        )
        sys_prompt = tk.Text(
            pad,
            height=4,
            width=52,
            bg=BG_LOG,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=CARD_HILITE,
            font=("Segoe UI", 10),
        )
        sys_prompt.insert("1.0", str(self._settings.get("handoff_llm_system_prompt", "")))
        key_openai = tk.StringVar(value=str(self._settings.get("openai_api_key", "")))
        key_anthropic = tk.StringVar(value=str(self._settings.get("anthropic_api_key", "")))
        key_google = tk.StringVar(value=str(self._settings.get("google_api_key", "")))
        key_xai = tk.StringVar(value=str(self._settings.get("xai_api_key", "")))

        r = 0
        ttk.Label(pad, text="Heartbeat interval (seconds)").grid(row=r, column=0, sticky="w")
        ttk.Spinbox(pad, from_=1, to=120, textvariable=hb, width=8).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="Stall timeout (seconds)").grid(row=r, column=0, sticky="w")
        ttk.Spinbox(pad, from_=60, to=86400, textvariable=stall, width=8).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="Max concurrent jobs").grid(row=r, column=0, sticky="w")
        ttk.Spinbox(pad, from_=1, to=10, textvariable=jobs, width=8).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="Log profile").grid(row=r, column=0, sticky="w")
        ttk.Combobox(
            pad,
            textvariable=profile,
            values=("standard", "tuning", "debug"),
            state="readonly",
            width=18,
        ).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Checkbutton(pad, text="Start with scan-existing", variable=scan).grid(
            row=r, column=0, columnspan=2, sticky="w"
        )
        r += 1
        ttk.Checkbutton(pad, text="Always ask for post-transcript destination", variable=ask).grid(
            row=r, column=0, columnspan=2, sticky="w"
        )
        r += 1
        ttk.Label(pad, text="Default handoff destination").grid(row=r, column=0, sticky="w")
        ttk.Combobox(
            pad,
            textvariable=dest,
            values=("notebooklm", "llm"),
            state="readonly",
            width=18,
        ).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="Default LLM provider").grid(row=r, column=0, sticky="w")
        ttk.Combobox(
            pad,
            textvariable=prov,
            values=("chatgpt", "gemini", "claude", "grok", "lmstudio"),
            state="readonly",
            width=18,
        ).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="LM Studio base URL").grid(row=r, column=0, sticky="w")
        ttk.Entry(pad, textvariable=lm_url, width=32).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="LM Studio model id").grid(row=r, column=0, sticky="w")
        ttk.Entry(pad, textvariable=lm_model, width=32).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="Watch directory").grid(row=r, column=0, sticky="w")
        ttk.Entry(pad, textvariable=wdir, width=32).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="Completed video directory").grid(row=r, column=0, sticky="w")
        ttk.Entry(pad, textvariable=cdir, width=32).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Checkbutton(pad, text="Try LLM API first (uses keys below, else env vars)", variable=handoff_api).grid(
            row=r, column=0, columnspan=2, sticky="w"
        )
        r += 1
        ttk.Label(pad, text="API handoff system prompt").grid(row=r, column=0, sticky="nw")
        sys_prompt.grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="OpenAI API key").grid(row=r, column=0, sticky="w")
        ttk.Entry(pad, textvariable=key_openai, width=32, show="*").grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="Anthropic API key").grid(row=r, column=0, sticky="w")
        ttk.Entry(pad, textvariable=key_anthropic, width=32, show="*").grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="Google AI (Gemini) key").grid(row=r, column=0, sticky="w")
        ttk.Entry(pad, textvariable=key_google, width=32, show="*").grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(pad, text="xAI (Grok) API key").grid(row=r, column=0, sticky="w")
        ttk.Entry(pad, textvariable=key_xai, width=32, show="*").grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(
            pad,
            text="Keys are stored only in watcher_settings.json on this machine.",
            foreground="#666",
        ).grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1

        def save() -> None:
            prompt_text = sys_prompt.get("1.0", "end-1c").strip()
            save_settings(
                ROOT,
                {
                    "heartbeat_seconds": float(hb.get()),
                    "stall_timeout_seconds": float(stall.get()),
                    "max_concurrent_jobs": int(jobs.get()),
                    "log_profile": profile.get(),
                    "start_scan_existing": bool(scan.get()),
                    "post_transcript_always_ask": bool(ask.get()),
                    "default_post_transcript_destination": dest.get(),
                    "default_llm_provider": prov.get(),
                    "lm_studio_base_url": lm_url.get().strip(),
                    "lm_studio_model": lm_model.get().strip(),
                    "watch_dir": wdir.get().strip(),
                    "completed_video_dir": cdir.get().strip(),
                    "completed_dir": cdir.get().strip(),
                    "handoff_try_api_first": bool(handoff_api.get()),
                    "handoff_llm_system_prompt": prompt_text
                    or "Summarize the transcript for an operator: key points, action items, and notable quotes.",
                    "openai_api_key": key_openai.get().strip(),
                    "anthropic_api_key": key_anthropic.get().strip(),
                    "google_api_key": key_google.get().strip(),
                    "xai_api_key": key_xai.get().strip(),
                },
            )
            self._settings = load_settings(ROOT)
            self._sync_settings_page_vars()
            win.destroy()

        bf = ttk.Frame(pad)
        bf.grid(row=r, column=0, columnspan=2, pady=(16, 0))
        ttk.Button(bf, text="Save", command=save).pack(side="left")
        ttk.Button(bf, text="Cancel", command=win.destroy).pack(side="left", padx=(8, 0))
        ttk.Label(
            pad,
            text=f"Settings file: {settings_path(ROOT)}",
            foreground=FG_DIM,
        ).grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _sync_settings_page_vars(self) -> None:
        s = load_settings(ROOT)
        self.set_hb.set(float(s["heartbeat_seconds"]))
        self.set_stall.set(float(s["stall_timeout_seconds"]))
        self.set_jobs.set(int(s["max_concurrent_jobs"]))
        self.set_profile.set(str(s.get("log_profile", "standard")))
        self.set_scan.set(bool(s.get("start_scan_existing", True)))
        self.set_watch_dir.set(str(s.get("watch_dir", "watch_incoming")))
        self.set_completed_dir.set(str(s.get("completed_video_dir") or s.get("completed_dir", "")))
        self.set_handoff_api.set(bool(s.get("handoff_try_api_first", False)))
        self.set_whisper_gpu.set(str(s.get("whisper_gpu_model", "large-v3")))
        self.set_whisper_cpu.set(str(s.get("whisper_cpu_model", "medium")))
        self.set_whisper_profile.set(self._profile_label(s.get("whisper_decode_profile", "auto")))
        self.set_prefer_captions.set(bool(s.get("prefer_youtube_captions", True)))
        self.set_whisper_language.set(str(s.get("whisper_language", "auto")))
        self.set_asr_engine.set(str(s.get("asr_engine", "whisper")))
        self.set_asr_crosscheck.set(bool(s.get("asr_crosscheck", False)))
        self.set_whisper_batched.set(bool(s.get("whisper_batched", False)))
        self.set_skip_duplicates.set(bool(s.get("skip_duplicates", True)))
        self.set_completed_audio_dir.set(str(s.get("completed_audio_dir", "completed_audio")))
        if hasattr(self, "set_theme"):
            self.set_theme.set(themes.normalize(s.get("theme")))
        if hasattr(self, "set_cov_block"):
            self.set_cov_block.set(str(s.get("coverage_max_block_s", 30)))
            self.set_cov_gap.set(str(s.get("coverage_max_gap_s", 60)))
        self._settings = s
        if hasattr(self, "_sb"):
            self._settings_mark_clean()
            self._refresh_handoff_flags()

    def _open_handoff(self) -> None:
        data = self._read_status()
        dest_path = data.get("last_completed_video")
        if not dest_path:
            messagebox.showinfo(
                "Handoff",
                "No completed video path in status yet. Finish a job first, or open the videos folder manually.",
            )
            return
        video = Path(str(dest_path))
        stem = video.stem
        txt = self._transcript_dir() / f"{stem}.txt"
        self._settings = load_settings(ROOT)
        if self._settings.get("post_transcript_always_ask", False):
            self._handoff_dialog(video, txt)
        else:
            self._run_handoff(
                str(self._settings.get("default_post_transcript_destination", "notebooklm")),
                str(self._settings.get("default_llm_provider", "chatgpt")),
                video,
                txt,
                include_video_path=True,
            )

    def _handoff_dialog(self, video: Path, txt: Path) -> None:
        win = tk.Toplevel(self)
        win.title("Post-transcript handoff")
        win.geometry("480x420")
        win.transient(self)

        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        dest = tk.StringVar(value=str(self._settings.get("default_post_transcript_destination", "notebooklm")))
        prov = tk.StringVar(value=str(self._settings.get("default_llm_provider", "chatgpt")))
        inc_video = tk.BooleanVar(value=True)

        ttk.Label(f, text="Send transcript to:").pack(anchor="w")
        ttk.Radiobutton(f, text="NotebookLM (clipboard + open web)", variable=dest, value="notebooklm").pack(
            anchor="w"
        )
        ttk.Radiobutton(f, text="LLM (instant — clipboard + provider site)", variable=dest, value="llm").pack(
            anchor="w"
        )
        ttk.Label(
            f,
            text=(
                "NotebookLM: paste/upload is manual in the browser; large videos may need separate upload. "
                "Staged bundle: watch folder / staged_for_notebooklm / last_handoff.txt"
            ),
            wraplength=440,
        ).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(
            f,
            text="Include local video path line in clipboard bundle",
            variable=inc_video,
        ).pack(anchor="w", pady=(8, 0))
        ttk.Label(f, text="LLM provider:").pack(anchor="w", pady=(8, 0))
        ttk.Combobox(
            f,
            textvariable=prov,
            values=("chatgpt", "gemini", "claude", "grok", "lmstudio"),
            state="readonly",
            width=20,
        ).pack(anchor="w")

        def go() -> None:
            self._run_handoff(
                dest.get(),
                prov.get(),
                video,
                txt,
                include_video_path=bool(inc_video.get()),
            )
            win.destroy()

        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=(16, 0))
        ttk.Button(bf, text="Run handoff", command=go).pack(side="left")
        ttk.Button(
            bf,
            text="Open staged folder",
            command=lambda: handoff_open_folder(self._watch_dir() / "staged_for_notebooklm"),
        ).pack(side="left", padx=(8, 0))

    def _run_handoff(
        self,
        destination: str,
        provider: str,
        video: Path,
        txt: Path,
        *,
        include_video_path: bool = True,
    ) -> None:
        title = video.stem
        bundle = build_handoff_bundle(
            video if video.exists() else None,
            txt if txt.exists() else None,
            title,
            include_video_path=include_video_path,
        )
        if destination == "notebooklm":
            handoff_notebooklm(bundle, ROOT, self._watch_dir())
            messagebox.showinfo(
                "NotebookLM",
                "Handoff copied to clipboard. NotebookLM opened; see staged_for_notebooklm/last_handoff.txt.",
            )
        else:
            self._settings = load_settings(ROOT)
            ok, out = handoff_try_llm_api(provider, bundle, self._settings)
            if ok and out:
                write_api_handoff_artifact(self._watch_dir(), title, out)
                preview = out if len(out) <= 2000 else out[:2000] + "\n…"
                messagebox.showinfo(
                    "LLM API",
                    "Summary written to staged_for_notebooklm/last_api_summary.txt\n\n" + preview,
                )
                return
            if not ok and out:
                messagebox.showwarning("LLM API", f"API handoff skipped ({out}). Falling back to browser.")
            handoff_llm_browser(provider, bundle)
            messagebox.showinfo("LLM", f"Handoff copied to clipboard; opened {provider}.")

    def refresh_once(self) -> None:
        self._refresh_latest_transcripts()
        self._settings = load_settings(ROOT)
        data = self._read_status()
        self._apply_status(data)
        self._load_log_tail()
        self._load_live_transcribe_tail()
        self._load_resource_tail()
        self._refresh_queue_tree(data)
        self._maybe_auto_handoff(data)

    def refresh(self) -> None:
        self.refresh_once()
        delay_ms = 1000 if self.auto_refresh_var.get() or self._current_nav in {"logs", "monitor", "live_transcribe"} else 5000
        self._refresh_job = self.after(delay_ms, self.refresh)

    def _read_status(self) -> dict:
        sp = self._status_path()
        if not sp.exists():
            return {}
        try:
            return json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _is_watch_media_file(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.name.startswith(".watch_progress_"):
            return False
        lower_name = path.name.lower()
        if any(lower_name.endswith(suffix) for suffix in IGNORE_SUFFIXES):
            return False
        return path.suffix.lower() in MEDIA_EXTENSIONS

    def _watch_media_files(self) -> list[Path]:
        watch_dir = self._watch_dir()
        try:
            files = [p for p in watch_dir.iterdir() if self._is_watch_media_file(p)]
        except OSError:
            return []
        return sorted(files, key=lambda p: p.name.lower())

    def _derive_active_jobs(self, data: dict) -> tuple[list[dict], int]:
        jobs = data.get("active_jobs") or []
        derived: list[dict] = []
        stale_count = 0
        seen_paths: set[str] = set()
        for job in jobs:
            path = str(job.get("path") or "").strip()
            worker_pid = int(job.get("worker_pid") or 0)
            if not path or path in seen_paths:
                stale_count += 1
                continue
            if not Path(path).is_file() or not process_alive(worker_pid):
                stale_count += 1
                continue
            seen_paths.add(path)
            derived.append(
                {
                    "path": path,
                    "worker_pid": worker_pid,
                    "phase": str(job.get("phase") or "transcribing"),
                    "last_segment_end": float(job.get("last_segment_end") or 0.0),
                    "duration": job.get("duration"),
                    "engine": job.get("engine"),
                    "started_at_epoch": job.get("started_at_epoch"),
                }
            )
        return derived, stale_count

    def _queue_selected(self) -> str | None:
        sel = self.queue_tree.selection()
        return sel[0] if sel else None

    def _queue_move(self, delta: int) -> None:
        path = self._queue_selected()
        if not path or path not in self._queue_order:
            messagebox.showinfo("Queue", "Select a queued (not running) file to move.")
            return
        queue_control.move(self._watch_dir(), list(self._queue_order), path, delta)
        self._queue_order = queue_control.ordered(list(self._queue_order), queue_control.load(self._watch_dir())["order"])
        self._render_queue_rows(self._last_queue_data or {})

    def _queue_skip(self) -> None:
        path = self._queue_selected()
        if not path:
            return
        if path not in self._queue_order:
            if messagebox.askyesno("Skip running file", f"Stop the transcription that is running now and skip it?\n"
                                   f"(The file stays in the watch folder.)\n\n{Path(path).name}"):
                queue_control.request_skip(self._watch_dir(), path)
            return
        if messagebox.askyesno("Skip file", f"Remove from the queue (the file stays in the watch folder)?\n\n{Path(path).name}"):
            queue_control.request_skip(self._watch_dir(), path)

    def _queue_toggle_pause(self) -> None:
        self._queue_paused = not self._queue_paused
        queue_control.set_paused(self._watch_dir(), self._queue_paused)
        self.queue_pause_text.set("Resume queue" if self._queue_paused else "Pause queue")

    def _refresh_queue_tree(self, data: dict) -> None:
        self._last_queue_data = data
        active_jobs, _stale_jobs = self._derive_active_jobs(data)
        self._render_queue_rows(data, active_jobs)
        tree = self.queue_tree
        queue_count = len(self._watch_media_files())
        active_count = len(active_jobs)
        processed_count = int(data.get("processed_count") or 0)
        recent_rate = self._recent_processing_rate()
        self.queue_meta_var.set(
            f"Queue depth: {queue_count} · "
            f"Max concurrent: {int(data.get('max_concurrent_jobs') or 1)} · "
            f"Active jobs: {active_count}"
        )
        self.queue_active_var.set(str(active_count))
        self.queue_depth_var.set(str(queue_count))
        self.queue_throughput_var.set(f"{recent_rate:.2f} items/min" if recent_rate > 0 else f"{processed_count} total")
        self._load_recent_queue_activity()

    def _render_queue_rows(self, data: dict, active_jobs: list | None = None) -> None:
        import time as _time
        tree = self.queue_tree
        if active_jobs is None:
            active_jobs, _ = self._derive_active_jobs(data)
        keep = self._queue_selected()
        for i in tree.get_children():
            tree.delete(i)
        control = queue_control.load(self._watch_dir())
        self._queue_paused = bool(control["paused"])
        self.queue_pause_text.set("Resume queue" if self._queue_paused else "Pause queue")
        now = _time.time()
        first = None
        active_paths = set()
        for j in active_jobs:
            p = str(j.get("path", ""))
            active_paths.add(p)
            frac, eta = queue_control.job_progress(j, now)
            bar = ("#" * int((frac or 0) * 12)).ljust(12, ".")
            prog = f"{bar} {frac * 100:.0f}%" if frac is not None else f"{float(j.get('last_segment_end') or 0):.0f}s"
            tree.insert("", tk.END, iid=p or None, values=(Path(p).name if p else "-", str(j.get("phase", "")),
                        str(j.get("engine") or "-"), prog, queue_control.format_eta(eta)), tags=("st_success",))
            if first is None:
                first = (j, frac, eta)
        queued = [q for q in (data.get("queued_order") or []) if q not in active_paths]
        self._queue_order = queue_control.ordered(queued, control["order"])
        for n, q in enumerate(self._queue_order, 1):
            state = "paused" if self._queue_paused else f"queued #{n}"
            if not tree.exists(q):
                tree.insert("", tk.END, iid=q, values=(Path(q).name, state, "-", "-", "-"),
                            tags=("st_muted",) if self._queue_paused else ("st_accent",))
        if keep and tree.exists(keep):
            tree.selection_set(keep)
        if first:
            j, frac, eta = first
            dur = float(j.get("duration") or 0)
            seg = float(j.get("last_segment_end") or 0)
            self.live_name_var.set(Path(str(j.get("path", ""))).name)
            self._set_live_progress(frac)
            if hasattr(self, "lt_chip"):
                self._set_chip(self.lt_chip, "success", str(j.get("phase") or "decoding").upper()[:16])
            pos = f"{queue_control.format_eta(seg)} / {queue_control.format_eta(dur)}" if dur else f"{seg:.0f}s"
            self.live_detail_var.set(f"{j.get('engine') or 'engine ?'}  |  {pos}  |  ETA {queue_control.format_eta(eta)}")
        else:
            self.live_name_var.set("Queue paused" if self._queue_paused and self._queue_order else "Nothing transcribing right now")
            self._set_live_progress(None)
            if hasattr(self, "lt_chip"):
                self._set_chip(self.lt_chip, "muted", "PAUSED" if self._queue_paused else "IDLE")
            self.live_detail_var.set(f"{len(self._queue_order)} file(s) waiting" if self._queue_order else "")

    def _set_live_progress(self, frac) -> None:
        for bar in getattr(self, "_live_bars", []):
            bar["maximum"] = 100
            bar["value"] = (frac or 0) * 100
        self.live_pct_var.set(f"{(frac or 0) * 100:.0f}%" if frac is not None else "")

    def _apply_status(self, data: dict) -> None:
        running_flag = bool(data.get("running", False))
        pid = int(data.get("pid") or 0)
        alive = running_flag and process_alive(pid)
        stale_running = running_flag and not alive

        phase = str(data.get("phase") or data.get("status") or "unknown")
        status = str(data.get("status", "unknown"))
        stalled = bool(data.get("stalled"))
        err = data.get("last_error")
        friendly_err = format_watcher_error(err)
        last_event = str(data.get("last_event") or "")
        active_jobs, stale_jobs = self._derive_active_jobs(data)
        worker_pid = int(data.get("last_worker_pid") or 0)
        worker_alive = process_alive(worker_pid)
        if not worker_alive and active_jobs:
            worker_pid = int(active_jobs[-1].get("worker_pid") or 0)
            worker_alive = process_alive(worker_pid)
        heartbeat_age = age_label(data.get("last_heartbeat"))
        progress_age = age_label(data.get("last_progress_at"))
        queue_count_live = len(self._watch_media_files())
        max_jobs = int(data.get("max_concurrent_jobs", 1) or 1)
        over_concurrency = len(active_jobs) > max_jobs
        status_processing_but_no_live_jobs = status == "processing" and not active_jobs

        if stale_running:
            self.summary_var.set("OFF (stale status — PID not running)")
            self.phase_var.set("off")
            self.detail_var.set("Start the watcher or refresh after a clean stop.")
            self.node_status_var.set("SYSTEM: UNKNOWN")
            self.queue_health_var.set("STALE")
        elif not running_flag or status == "stopped":
            self.summary_var.set("OFF — watcher stopped")
            self.phase_var.set(str(data.get("phase") or "off"))
            self.health_var.set(str(data.get("health") or "-"))
            self.detail_var.set("Use Start on Dashboard or Queue to launch.")
            self.node_status_var.set("SYSTEM: STOPPED")
            self.queue_health_var.set("STANDBY")
        else:
            self.summary_var.set("ON — watcher running")
            self.phase_var.set("transcribing" if active_jobs else phase)
            self.health_var.set(str(data.get("health") or "-"))
            detail = f"Status: {status}"
            if stalled:
                detail += " · stalled flag set"
            if status_processing_but_no_live_jobs:
                detail += " · no live worker detected"
            elif status == "processing" and worker_pid and not worker_alive:
                detail += " · worker process is gone"
            if stale_jobs:
                detail += f" · dropped {stale_jobs} stale job entr{'y' if stale_jobs == 1 else 'ies'} from view"
            if over_concurrency:
                detail += f" · concurrency mismatch ({len(active_jobs)} running, limit {max_jobs})"
            elif max_jobs > 1:
                detail += f" · concurrency={max_jobs} (GPU contention risk — open Monitor)"
            self.detail_var.set(detail)
            self.node_status_var.set("SYSTEM: RUNNING")
            self.queue_health_var.set(
                "FAULT"
                if err or stalled or status_processing_but_no_live_jobs or over_concurrency or (status == "processing" and worker_pid and not worker_alive)
                else "ACTIVE"
            )

        stall_sec = data.get("stall_timeout_seconds")
        prog_at = data.get("last_progress_at")
        if stall_sec and prog_at and status == "processing":
            self.detail_var.set(
                f"{self.detail_var.get()} · stall timeout {stall_sec}s (no progress kills worker)"
            )

        stopped_view = stale_running or (not running_flag) or status == "stopped"
        if stopped_view:
            self.file_var.set("—")
            self.heartbeat_var.set("stopped")
            self.progress_var.set("—")
            self.segment_var.set("—")
            self.pid_var.set("—")
            self.worker_pid_var.set("—")
        else:
            current_file = str(data.get("current_file") or "").strip()
            if active_jobs:
                current_file = str(active_jobs[-1].get("path") or current_file or "—")
            self.file_var.set(current_file or "—")
            self.heartbeat_var.set(f"{str(data.get('last_heartbeat') or '—')}  ({heartbeat_age})")
            self.progress_var.set(f"{str(data.get('last_progress_at') or '—')}  ({progress_age})")
            self.segment_var.set(f"{float(data.get('last_segment_end', 0.0) or 0.0):.2f}s")
            self.pid_var.set(str(data.get("pid") or "—"))
            worker_label = str(data.get("last_worker_pid") or "—")
            if worker_pid:
                worker_label += "  (alive)" if worker_alive else "  (not running)"
            self.worker_pid_var.set(worker_label)
        self.error_var.set(friendly_err)

        if active_jobs:
            parts = []
            for j in active_jobs:
                parts.append(
                    f'{Path(str(j.get("path", ""))).name} (pid {j.get("worker_pid")}, {j.get("phase")})'
                )
            self.active_jobs_var.set("; ".join(parts))
        else:
            self.active_jobs_var.set("—")

        queue_count = queue_count_live
        completed_count = int(data.get("completed_count", 0) or 0)
        failed_count = int(data.get("failed_count", 0) or 0)
        processed_count = int(data.get("processed_count", 0) or 0)

        self.queue_var.set(queue_count)
        self.completed_var.set(completed_count)
        self.failed_var.set(failed_count)
        self.processed_var.set(processed_count)

        max_count = max(1, queue_count + processed_count, completed_count + failed_count)
        self.queue_bar["maximum"] = max_count
        self.queue_bar["value"] = queue_count
        self.completed_bar["maximum"] = max_count
        self.completed_bar["value"] = completed_count
        self.failed_bar["maximum"] = max_count
        self.failed_bar["value"] = failed_count
        self.processed_bar["maximum"] = max_count
        self.processed_bar["value"] = processed_count
        self._color_counts()

        can_stop = bool(running_flag and alive)
        can_start = not can_stop or stale_running
        self._set_dashboard_state(running=can_stop, stale=bool(stale_running))
        if hasattr(self, "q_stop_btn"):
            self.q_stop_btn.state(["!disabled"] if can_stop else ["disabled"])
            self.q_start_btn.state(["!disabled"] if can_start else ["disabled"])
            self.q_start_btn.configure(style="Dash.Secondary.TButton" if can_stop else "Dash.Primary.TButton")
        self.power_btn.state(["!disabled"] if (can_start or can_stop) else ["disabled"])

        recent_failure = last_event.startswith("failed:") or last_event.startswith("stalled:")
        show_banner = stale_running or stalled or (bool(err) and (running_flag or recent_failure))
        msg_parts: list[str] = []
        if stale_running:
            msg_parts.append(
                "Status file says running but the process is gone — use Start or refresh after a clean stop."
            )
        if status == "processing" and worker_pid and not worker_alive:
            msg_parts.append("Watcher is alive but the transcription worker process is no longer running.")
        if stalled:
            msg_parts.append("Last job stalled (worker killed after progress timeout).")
        if err and (running_flag or recent_failure):
            msg_parts.append(f"Error: {friendly_err}")
        if show_banner:
            bg = ROW_ERROR if (err or stalled) else ROW_WARN
            self.banner_frame.configure(bg=bg)
            self.banner_label.configure(text=" · ".join(msg_parts), fg="#fff", bg=bg)
            if not self.banner_frame.winfo_ismapped():
                first = self._dash_outer.pack_slaves()
                if first:
                    self.banner_frame.pack(in_=self._dash_outer, fill="x", pady=(0, 10), before=first[0])
                else:
                    self.banner_frame.pack(in_=self._dash_outer, fill="x", pady=(0, 10))
        elif self.banner_frame.winfo_ismapped():
            self.banner_frame.pack_forget()

        prof = str(data.get("log_profile", "standard"))
        self.monitor_hint_var.set(
            f"Log profile: {prof} — JSONL samples in {self._watch_dir() / 'watch_resource.jsonl'} while jobs run."
        )
        if self.auto_refresh_var.get():
            self.log_status_var.set("Live updates on")
        elif self._current_nav in {"logs", "monitor", "live_transcribe"}:
            self.log_status_var.set("Live on this page")
        else:
            self.log_status_var.set("Paused outside logs")
        self.logs_hint_var.set(
            f"Streaming tail from {self._watch_dir() / 'watch_events.log'} — newest lines are shown first."
        )
        self.live_transcribe_hint_var.set(
            f"Streaming tail from {self._watch_dir() / 'whisper_live.log'} — newest transcript segments are shown first."
        )
        provider = str(self._settings.get("default_llm_provider", "notebooklm")).strip() or "notebooklm"
        self.handoff_destination_var.set(provider.replace("_", " ").title())
        self.handoff_format_var.set("Raw text + JSON")
        self._refresh_handoff_flags()

    def _load_log_tail(self, lines: int = 80) -> None:
        raw = self._read_log_lines()
        stick_to_top = True
        try:
            first, _last = self.log_text.yview()
            stick_to_top = first <= 0.02
        except tk.TclError:
            pass
        if raw is None:
            content = "No log file yet."
        else:
            filtered = self._filter_log_lines(raw)
            view = filtered[-lines:]
            view.reverse()
            content = "\n".join(view) if view else "No log lines match the current filter."
        self._set_text(self.log_text, content)
        self._color_log_lines(self.log_text)
        if stick_to_top:
            try:
                self.log_text.yview_moveto(0.0)
            except tk.TclError:
                pass

    def _load_resource_tail(self, lines: int = 40) -> None:
        res = self._watch_dir() / "watch_resource.jsonl"
        latest: dict | None = None
        if not res.exists():
            latest = sample_snapshot(str(self._settings.get("log_profile", "standard")))
            content = "No resource samples yet (set log profile to tuning or debug in Settings).\n\nLive local snapshot:\n"
            content += json.dumps(latest, indent=2, ensure_ascii=False)
        else:
            try:
                raw = res.read_text(encoding="utf-8", errors="replace").splitlines()
                if raw:
                    content = "\n".join(raw[-lines:])
                    latest = json.loads(raw[-1])
                else:
                    content = "Empty."
            except Exception as exc:
                content = f"Failed to read resource log: {exc}"
        if latest:
            self._apply_resource_snapshot(latest)
        self._set_text(self.resource_text, content)

    def _load_live_transcribe_tail(self, lines: int = 120) -> None:
        log_file = self._watch_dir() / "whisper_live.log"
        stick_to_top = True
        try:
            first, _last = self.live_transcribe_text.yview()
            stick_to_top = first <= 0.02
        except tk.TclError:
            pass
        if not log_file.exists():
            content = "No live transcribe stream yet."
        else:
            try:
                raw = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception as exc:
                content = f"Failed to read whisper_live.log: {exc}"
            else:
                segment_lines = [
                    line
                    for line in raw
                    if line.startswith("Segment [")
                    or "Whisper attempt " in line
                    or "No unwanted segments found" in line
                    or "returned no segments" in line
                    or "all_whisper_attempts_failed" in line
                ]
                view = segment_lines[-lines:]
                view.reverse()
                content = "\n".join(view) if view else "No transcript segments have been emitted yet."
        self._set_text(self.live_transcribe_text, content)
        try:
            self.live_transcribe_text.tag_remove("latest", "1.0", "end")
            self.live_transcribe_text.tag_add("latest", "1.0", "1.end")
        except tk.TclError:
            pass
        if stick_to_top:
            try:
                self.live_transcribe_text.yview_moveto(0.0)
            except tk.TclError:
                pass

    def _read_log_lines(self) -> list[str] | None:
        log_file = self._watch_dir() / "watch_events.log"
        if not log_file.exists():
            return None
        try:
            return log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return []

    def _filter_log_lines(self, lines: list[str]) -> list[str]:
        needle = self.log_filter_var.get().strip().lower()
        if not needle:
            return lines
        return [line for line in lines if needle in line.lower()]

    def _recent_processing_rate(self, minutes: int = 10) -> float:
        lines = self._read_log_lines() or []
        cutoff = datetime.now() - timedelta(minutes=minutes)
        processed = 0
        for line in reversed(lines[-300:]):
            if " completed:" not in line and " failed:" not in line and " skipped:" not in line:
                continue
            ts = self._parse_watch_log_time(line)
            if ts is None or ts < cutoff:
                continue
            processed += 1
        return processed / float(minutes)

    def _load_recent_queue_activity(self, limit: int = 8) -> None:
        lines = self._read_log_lines() or []
        interesting: list[str] = []
        for line in reversed(lines):
            lower = line.lower()
            if any(token in lower for token in (" completed:", " failed:", " skipped:", " processing:", " queued:")):
                interesting.append(line)
            if len(interesting) >= limit:
                break
        if not interesting:
            content = "No recent queue activity yet."
        else:
            content = "\n".join(reversed(interesting))
        self._set_text(self.queue_recent_text, content)

    def _parse_watch_log_time(self, line: str) -> datetime | None:
        try:
            return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def _apply_resource_snapshot(self, snap: dict) -> None:
        cpu = float(snap.get("cpu_percent") or 0.0)
        mem = float(snap.get("mem_percent") or 0.0)
        mem_used = snap.get("mem_used_mb")
        self.monitor_cpu_var.set(f"CPU {cpu:.0f}%")
        if mem_used is not None:
            self.monitor_mem_var.set(f"MEM {mem:.0f}% · {float(mem_used) / 1024:.1f} GB")
        else:
            self.monitor_mem_var.set(f"MEM {mem:.0f}%")

        gpu = snap.get("gpu") or {}
        gpu_util = gpu.get("gpu_util")
        gpu_used = gpu.get("gpu_mem_used_mb")
        gpu_total = gpu.get("gpu_mem_total_mb")
        if gpu_util is not None:
            if gpu_used is not None and gpu_total:
                self.monitor_gpu_var.set(
                    f"GPU {float(gpu_util):.0f}% · {float(gpu_used) / 1024:.1f}/{float(gpu_total) / 1024:.1f} GB"
                )
            else:
                self.monitor_gpu_var.set(f"GPU {float(gpu_util):.0f}%")
        else:
            self.monitor_gpu_var.set("GPU unavailable")

        self.monitor_disk_var.set(
            f"{snap.get('ts', 'live')} · threads {snap.get('python_threads', 'n/a')} · log profile {self._settings.get('log_profile', 'standard')}"
        )

    @staticmethod
    def _set_text(widget: tk.Text, content: str) -> None:
        try:
            current = widget.get("1.0", "end-1c")
        except tk.TclError:
            current = ""
        if current == content:
            return
        stick_to_bottom = True
        try:
            _first, last = widget.yview()
            stick_to_bottom = last >= 0.98
        except tk.TclError:
            pass
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content)
        widget.configure(state="disabled")
        if stick_to_bottom:
            widget.see("end")


def main() -> None:
    sock = acquire_single_instance_socket()
    if sock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            APP_NAME,
            f"{APP_NAME} is already open. Check the taskbar.",
        )
        sys.exit(0)
    app = ControlPanel(sock)
    app.mainloop()


if __name__ == "__main__":
    main()
