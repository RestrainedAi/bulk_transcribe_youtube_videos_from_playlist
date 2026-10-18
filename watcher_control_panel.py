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
BG_MAIN    = "#121415"   # surface / background
BG_SIDE    = "#1a1c1d"   # surface-container-low (sidebar)
BG_CARD    = "#1e2021"   # surface-container (cards)
BG_HEAD    = "#0c0e0f"   # surface-container-lowest (topbar / ink areas)
BG_SOFT    = "#1a1c1d"   # alias for sidebar areas
TOPBAR_BG  = "#0c0e0f"   # surface-container-lowest
SIDEBAR_BG = "#1a1c1d"   # surface-container-low
SURFACE_INK = "#0c0e0f"  # deepest surface — log backgrounds
FIELD_BG   = "#333536"   # surface-container-highest (inputs)

ACCENT       = "#8dcdff"  # primary blue
ACCENT_GREEN = "#3ce36a"  # tertiary / success green
ACCENT_MINT  = "#3ce36a"  # alias
ACCENT_RED   = "#ffb4ab"  # error
ACCENT_GOLD  = "#afcbd8"  # secondary (replaces gold — no gold in design system)

FG      = "#e2e2e3"   # on-surface
FG_DIM  = "#bdc8d3"   # on-surface-variant

BG_LOG     = "#0c0e0f"   # surface-container-lowest (log pane)
WARN_BG    = "#93000a"   # error-container
CARD_BORDER = "#3e4851"  # outline-variant
OUTLINE_SOFT = "#3e4851" # outline-variant
BG_HIGH    = "#282a2b"   # surface-container-high
OUTLINE    = "#88929c"   # outline
PRIMARY_FIXED = "#cae6ff"
ON_PRIMARY_FIXED_VARIANT = "#004b70"
INVERSE_PRIMARY = "#006493"
SECONDARY_CONTAINER = "#334d58"
ON_ERROR_CONTAINER = "#ffdad6"

FONT_HEADLINE = "Bahnschrift"
FONT_BODY = "Segoe UI Variable"
FONT_BODY_FALLBACK = "Segoe UI"
FONT_MONO = "Cascadia Mono"

# Dashboard redesign tokens (Figma "Workbench tokens", Dark) - px sizes via negative Tk font sizes
D_BG_DEEP = "#0c0e0f"
D_BG = "#121415"
D_SURFACE = "#1e2021"
D_SURFACE_2 = "#1a1c1d"
D_RAISED = "#282a2b"
D_INPUT = "#333536"
D_NAV_SEL = "#07152a"
D_BORDER = "#3e4851"
D_ACCENT = "#8dcdff"
D_PRIMARY = "#004b70"
D_SUCCESS = "#3ce36a"
D_SUCCESS_SURFACE = "#0f2a19"
D_DANGER = "#93000a"
D_DANGER_TEXT = "#ffb4ab"
D_DANGER_SURFACE = "#2a1717"
D_TEXT = "#e2e2e3"
D_MUTED = "#bdc8d3"
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
            background=[("active", "#b3261e"), ("pressed", "#b3261e")],
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

        self._build_page_dashboard(self.pages["dashboard"].inner)
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
        st.map("Dash.Primary.TButton", background=[("disabled", D_SURFACE_2), ("active", "#005a86")],
               foreground=[("disabled", D_MUTED)])
        st.configure("Dash.Secondary.TButton", background=D_RAISED, foreground=D_TEXT, bordercolor=D_BORDER,
                     lightcolor=D_RAISED, darkcolor=D_RAISED, focuscolor=D_RAISED, **base)
        st.map("Dash.Secondary.TButton", background=[("disabled", D_SURFACE_2), ("active", D_INPUT)],
               foreground=[("disabled", D_MUTED)])
        st.configure("Dash.Danger.TButton", background=D_DANGER, foreground=D_DANGER_TEXT, bordercolor=D_DANGER,
                     lightcolor=D_DANGER, darkcolor=D_DANGER, focuscolor=D_DANGER, **base)
        st.map("Dash.Danger.TButton", background=[("disabled", D_SURFACE_2), ("active", "#ad0010")],
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
        self._coverage_label = tk.Label(lf, textvariable=self.coverage_warning_var, fg="#f2b84b", bg=D_SURFACE,
                                        anchor="w", justify="left", wraplength=420)

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

    def _build_page_queue(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG_MAIN)
        wrap.pack(fill="both", expand=True)

        top = tk.Frame(wrap, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))
        self._section_title(top, "QUEUE / INGESTION", "Queue Management", "Global processing pipeline and media ingestion control.")
        top_actions = tk.Frame(top, bg=BG_MAIN)
        top_actions.pack(anchor="e", pady=(10, 0))
        ttk.Button(top_actions, text="Stop watcher", command=self._stop_watcher).pack(side="left")
        ttk.Button(top_actions, text="New task", style="Accent.TButton", command=lambda: open_path(self._watch_dir())).pack(
            side="left", padx=(12, 0)
        )

        ingest = self._card(wrap, pady=(0, 14))
        ingest_body = tk.Frame(ingest, bg=BG_CARD)
        ingest_body.pack(fill="both", expand=True, padx=20, pady=22)
        self._headline(ingest_body, "Ingest New Media", size=18).pack(anchor="center")
        self._body_label(
            ingest_body,
            "Drop source media into the watch folder to initialize the pipeline. Existing files can also be scanned on startup.",
            size=11,
            fg=FG_DIM,
            wraplength=760,
            justify="center",
        ).pack(anchor="center", pady=(8, 14))
        ingest_btns = tk.Frame(ingest_body, bg=BG_CARD)
        ingest_btns.pack()
        ttk.Button(ingest_btns, text="Browse files", style="Accent.TButton", command=lambda: open_path(self._watch_dir())).pack(
            side="left"
        )
        ttk.Button(ingest_btns, text="Start watcher (scan existing)", command=self._start_watcher).pack(
            side="left", padx=(14, 0)
        )

        self._build_easy_import(wrap).pack(fill="x", pady=(0, 14))
        youtube_intake.build_panel(
            wrap, self.youtube, theme=self._youtube_theme(), get_dirs=self._youtube_dirs
        ).pack(fill="x", pady=(0, 14))

        stats = tk.Frame(wrap, bg=BG_MAIN)
        stats.pack(fill="x", pady=(0, 14))
        for col in range(4):
            stats.columnconfigure(col, weight=1)
        self._metric_tile(stats, "Active tasks", self.queue_active_var, color=ACCENT).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._metric_tile(stats, "Queue depth", self.queue_depth_var, color=FG).grid(row=0, column=1, sticky="ew", padx=4)
        self._metric_tile(stats, "Throughput", self.queue_throughput_var, color=ACCENT_MINT).grid(row=0, column=2, sticky="ew", padx=4)
        self._metric_tile(stats, "System health", self.queue_health_var, color=ACCENT_MINT).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        active = self._card(wrap, expand=True, pady=(0, 14))
        af = tk.Frame(active, bg=BG_CARD)
        af.pack(fill="both", expand=True, padx=18, pady=18)
        hdr = tk.Frame(af, bg=BG_CARD)
        hdr.pack(fill="x")
        self._headline(hdr, "Active Queue", size=16, fg=ACCENT).pack(side="left")
        self._body_label(hdr, textvariable=self.queue_meta_var, fg=FG_DIM, size=10).pack(side="left", padx=(14, 0))
        live = tk.Frame(af, bg=BG_CARD)
        live.pack(fill="x", pady=(12, 0))
        self.live_name_var = tk.StringVar(value="Nothing transcribing right now")
        self.live_detail_var = tk.StringVar(value="")
        self._body_label(live, textvariable=self.live_name_var, fg=FG, size=11).pack(anchor="w")
        self.live_bar = ttk.Progressbar(live, orient="horizontal", mode="determinate", maximum=100)
        self.live_bar.pack(fill="x", pady=(6, 4))
        self._body_label(live, textvariable=self.live_detail_var, fg=FG_DIM, size=10).pack(anchor="w")
        cols = ("file", "state", "engine", "progress", "eta")
        self.queue_tree = ttk.Treeview(af, columns=cols, show="headings", height=10, selectmode="browse")
        self.queue_tree.heading("file", text="Source file")
        self.queue_tree.heading("state", text="State")
        self.queue_tree.heading("engine", text="Engine")
        self.queue_tree.heading("progress", text="Progress")
        self.queue_tree.heading("eta", text="ETA")
        self.queue_tree.column("file", width=400)
        self.queue_tree.column("state", width=120)
        self.queue_tree.column("engine", width=170)
        self.queue_tree.column("progress", width=170)
        self.queue_tree.column("eta", width=80, anchor="e")
        self.queue_tree.pack(fill="both", expand=True, pady=(10, 0))
        qbtns = tk.Frame(af, bg=BG_CARD)
        qbtns.pack(fill="x", pady=(10, 0))
        ttk.Button(qbtns, text="Move up", command=lambda: self._queue_move(-1)).pack(side="left")
        ttk.Button(qbtns, text="Move down", command=lambda: self._queue_move(1)).pack(side="left", padx=(8, 0))
        ttk.Button(qbtns, text="Skip selected", command=self._queue_skip).pack(side="left", padx=(8, 0))
        self.queue_pause_text = tk.StringVar(value="Pause queue")
        self._body_label(qbtns, text="Pause queue = finish the current file, start no new ones (watcher stays on).", fg=FG_DIM, size=9).pack(side="right", padx=(0, 10))
        ttk.Button(qbtns, textvariable=self.queue_pause_text, command=self._queue_toggle_pause).pack(side="right")
        self._queue_paused = False
        self._queue_order: list[str] = []
        self._last_queue_data: dict | None = None

        recent = self._card(wrap)
        rf = tk.Frame(recent, bg=BG_CARD)
        rf.pack(fill="both", expand=True, padx=18, pady=18)
        row = tk.Frame(rf, bg=BG_CARD)
        row.pack(fill="x")
        self._headline(row, "Recently Completed", size=16, fg=FG).pack(side="left")
        ttk.Button(row, text="Open videos", command=lambda: open_path(self._completed_dir())).pack(side="right")
        self.queue_recent_text = tk.Text(
            rf,
            height=7,
            bg=BG_LOG,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
            font=(FONT_MONO, 10),
        )
        self.queue_recent_text.pack(fill="both", expand=True, pady=(12, 0))

    def _build_page_monitor(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG_MAIN)
        wrap.pack(fill="both", expand=True)
        top = tk.Frame(wrap, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))
        self._section_title(top, "LIVE STREAMING", "System Performance", "Real-time telemetry and resource distribution analytics.")
        status = tk.Frame(top, bg=BG_MAIN)
        status.pack(anchor="e", pady=(8, 0))
        self._body_label(status, textvariable=self.monitor_cpu_var, mono=True, fg=ACCENT, size=13, bold=True).pack(side="left", padx=(0, 12))
        self._body_label(status, textvariable=self.monitor_mem_var, mono=True, fg=FG, size=13, bold=True).pack(side="left", padx=(0, 12))
        self._body_label(status, textvariable=self.monitor_gpu_var, mono=True, fg=ACCENT_MINT, size=13, bold=True).pack(side="left")

        grid = tk.Frame(wrap, bg=BG_MAIN)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=2)
        grid.columnconfigure(1, weight=1)
        grid.rowconfigure(0, weight=1)
        grid.rowconfigure(1, weight=1)

        cpu_card = self._card(grid, autopack=False)
        cpu_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        cf = tk.Frame(cpu_card, bg=BG_CARD)
        cf.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(cf, "Processing Unit", size=16, fg=ACCENT).pack(anchor="w")
        self._body_label(cf, textvariable=self.monitor_hint_var, fg=FG_DIM, size=10, wraplength=660).pack(anchor="w", pady=(8, 14))
        self.resource_text = tk.Text(
            cf,
            wrap="none",
            height=18,
            bg=SURFACE_INK,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
            font=(FONT_MONO, 10),
        )
        text_wrap = tk.Frame(cf, bg=BG_CARD)
        text_wrap.pack(fill="both", expand=True)
        self.resource_text.master = text_wrap
        self.resource_text.pack_forget()
        self.resource_text = tk.Text(
            text_wrap,
            wrap="none",
            height=18,
            bg=SURFACE_INK,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
            font=(FONT_MONO, 10),
        )
        self._attach_scrollbars(self.resource_text, text_wrap)

        mem_card = self._card(grid, autopack=False)
        mem_card.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        mf = tk.Frame(mem_card, bg=BG_CARD)
        mf.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(mf, "Memory Utilization", size=16, fg=FG).pack(anchor="w")
        self._body_label(mf, textvariable=self.monitor_mem_var, mono=True, fg=ACCENT, size=28, bold=True).pack(anchor="w", pady=(18, 4))
        self._body_label(mf, "Latest memory / process snapshot from telemetry stream.", fg=FG_DIM, size=10, wraplength=260).pack(anchor="w")
        self._body_label(mf, textvariable=self.monitor_disk_var, mono=True, fg=FG_DIM, size=10).pack(anchor="w", pady=(18, 0))

        disk_card = self._card(grid, autopack=False)
        disk_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        df = tk.Frame(disk_card, bg=BG_CARD)
        df.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(df, "Disk / Telemetry Stream", size=16, fg=FG).pack(anchor="w")
        self._body_label(df, "Telemetry rolls into the watcher resource log while jobs are active. Use this panel to watch trend lines and spot contention early.", fg=FG_DIM, size=10, wraplength=660).pack(anchor="w", pady=(8, 12))
        self._body_label(df, textvariable=self.monitor_disk_var, mono=True, fg=ACCENT, size=10).pack(anchor="w")

        proc_card = self._card(grid, autopack=False)
        proc_card.grid(row=1, column=1, sticky="nsew")
        pf = tk.Frame(proc_card, bg=BG_CARD)
        pf.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(pf, "Live Health", size=16, fg=FG).pack(anchor="w")
        self._body_label(pf, textvariable=self.queue_health_var, mono=True, fg=ACCENT_MINT, size=22, bold=True).pack(anchor="w", pady=(14, 6))
        self._body_label(pf, textvariable=self.monitor_gpu_var, mono=True, fg=FG, size=10).pack(anchor="w")
        self._body_label(pf, textvariable=self.monitor_cpu_var, mono=True, fg=FG, size=10).pack(anchor="w", pady=(6, 0))

    def _build_page_logs(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG_MAIN)
        wrap.pack(fill="both", expand=True)
        top = tk.Frame(wrap, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))
        self._section_title(top, "SYSTEM / LIVE LOGS", "System Logs", "Real-time execution stream for the active watcher node.")
        actions = tk.Frame(top, bg=BG_MAIN)
        actions.pack(anchor="e", pady=(8, 0))
        ttk.Button(actions, text="Export logs", command=lambda: open_path(self._watch_dir() / "watch_events.log")).pack(side="left")
        ttk.Button(actions, text="Unified log", command=self._open_unified_log).pack(side="left", padx=(12, 0))
        ttk.Button(actions, text="Copy for support", command=self._copy_for_support).pack(side="left", padx=(12, 0))
        ttk.Button(actions, text="Refresh", style="Accent.TButton", command=self.refresh_once).pack(side="left", padx=(12, 0))

        filter_row = tk.Frame(wrap, bg=BG_MAIN)
        filter_row.pack(fill="x", pady=(0, 10))
        self._body_label(filter_row, textvariable=self.log_status_var, fg=ACCENT_MINT, mono=True, size=10, bold=True).pack(side="right")
        tk.Entry(
            filter_row,
            textvariable=self.log_filter_var,
            width=36,
            bg=FIELD_BG,
            fg=FG,
            insertbackground=FG,
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
        ).pack(side="right", padx=(0, 14))
        self._body_label(filter_row, "Filter log content", fg=FG_DIM, size=10).pack(side="right", padx=(0, 8))

        card = self._card(wrap, expand=True)
        f = tk.Frame(card, bg=BG_CARD)
        f.pack(fill="both", expand=True, padx=0, pady=0)
        self._body_label(f, textvariable=self.logs_hint_var, fg=FG_DIM, size=10, wraplength=1000).pack(anchor="w", padx=18, pady=(14, 8))
        log_shell = tk.Frame(f, bg=BG_CARD)
        log_shell.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.log_text = tk.Text(
            log_shell,
            wrap="none",
            height=28,
            bg=SURFACE_INK,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
            font=(FONT_MONO, 10),
        )
        self._attach_scrollbars(self.log_text, log_shell)

    def _build_page_live_transcribe(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG_MAIN)
        wrap.pack(fill="both", expand=True)
        top = tk.Frame(wrap, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))
        self._section_title(
            top,
            "SYSTEM / LIVE TRANSCRIBE",
            "Live Transcribe",
            "Real-time segment stream from Whisper while the current job is being decoded.",
        )
        actions = tk.Frame(top, bg=BG_MAIN)
        actions.pack(anchor="e", pady=(8, 0))
        ttk.Button(
            actions,
            text="Open raw stream",
            command=lambda: open_path(self._watch_dir() / "whisper_live.log"),
        ).pack(side="left")
        ttk.Button(actions, text="Refresh", style="Accent.TButton", command=self.refresh_once).pack(
            side="left", padx=(12, 0)
        )

        card = self._card(wrap, expand=True)
        f = tk.Frame(card, bg=BG_CARD)
        f.pack(fill="both", expand=True, padx=0, pady=0)
        self._body_label(
            f,
            textvariable=self.live_transcribe_hint_var,
            fg=FG_DIM,
            size=10,
            wraplength=1000,
        ).pack(anchor="w", padx=18, pady=(14, 8))
        shell = tk.Frame(f, bg=BG_CARD)
        shell.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.live_transcribe_text = tk.Text(
            shell,
            wrap="none",
            height=28,
            bg=SURFACE_INK,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
            font=(FONT_MONO, 10),
        )
        self._attach_scrollbars(self.live_transcribe_text, shell)

    def _build_page_handoff(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG_MAIN)
        wrap.pack(fill="both", expand=True)
        top = tk.Frame(wrap, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))
        self._section_title(
            top,
            "PIPELINE_MODULE_04",
            "Post-Transcript Routing",
            "Configure automated downstream delivery for processed Whisper transcription data.",
        )

        grid = tk.Frame(wrap, bg=BG_MAIN)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=2)
        grid.columnconfigure(1, weight=1)

        primary = self._card(grid, autopack=False)
        primary.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 12))
        pf = tk.Frame(primary, bg=BG_CARD)
        pf.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(pf, "Primary Destination", size=18, fg=FG).pack(anchor="w")
        self._body_label(pf, textvariable=self.handoff_destination_var, mono=True, fg=ACCENT, size=10, bold=True).pack(anchor="e")
        btns = tk.Frame(pf, bg=BG_CARD)
        btns.pack(fill="x", pady=(12, 12))
        ttk.Button(btns, text="Send last completed transcript", style="Accent.TButton", command=self._open_handoff).pack(side="left")
        ttk.Button(btns, text="Open NotebookLM staging", command=lambda: handoff_open_folder(self._watch_dir() / "staged_for_notebooklm")).pack(side="left", padx=(12, 0))
        ttk.Button(btns, text="Open videos folder", command=lambda: open_path(self._completed_dir())).pack(side="left", padx=(12, 0))
        diag = tk.Text(
            pf,
            height=7,
            bg=SURFACE_INK,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
            font=(FONT_MONO, 10),
        )
        diag.pack(fill="both", expand=True)
        self._set_text(
            diag,
            "\n".join(
                [
                    "ENDPOINT_DIAGNOSTICS",
                    f"watch_dir: {self._watch_dir()}",
                    f"staging_dir: {self._watch_dir() / 'staged_for_notebooklm'}",
                    "notebooklm: manual browser upload / clipboard workflow",
                    f"default_provider: {self._settings.get('default_llm_provider', 'chatgpt')}",
                ]
            ),
        )

        side = self._card(grid, autopack=False)
        side.grid(row=0, column=1, sticky="nsew", pady=(0, 12))
        sf = tk.Frame(side, bg=BG_CARD)
        sf.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(sf, "Default Formatting", size=18, fg=FG).pack(anchor="w")
        self._body_label(sf, textvariable=self.handoff_format_var, mono=True, fg=ACCENT_MINT, size=10, bold=True).pack(anchor="w", pady=(10, 0))
        self._body_label(sf, "Raw text, structured JSON, and summary flows all remain available through the routing dialog.", fg=FG_DIM, size=10, wraplength=280).pack(anchor="w", pady=(8, 14))
        ttk.Button(sf, text="Open transcripts", command=lambda: open_path(self._transcript_dir())).pack(anchor="w")
        ttk.Button(sf, text="Open JSON", command=lambda: open_path(self._json_dir())).pack(anchor="w", pady=(10, 0))

        bottom = self._card(wrap)
        bottom.pack(fill="x")
        bf = tk.Frame(bottom, bg=BG_CARD)
        bf.pack(fill="x", padx=18, pady=18)
        self._body_label(bf, "SYSTEM_FLAGS", mono=True, fg=FG_DIM, size=10, bold=True).pack(anchor="w")
        flag_row = tk.Frame(bf, bg=BG_CARD)
        flag_row.pack(fill="x", pady=(12, 0))
        for text in ("APPEND_TIMESTAMPS", "AUTO_RETRY_ON_FAIL", "NOTIFY_MOBILE_APP", "STRIP_SPEAKER_IDS"):
            tk.Label(
                flag_row,
                text=text,
                bg=FIELD_BG,
                fg=ACCENT if text == "AUTO_RETRY_ON_FAIL" else FG,
                font=(FONT_MONO, 10, "bold"),
                padx=12,
                pady=8,
                highlightthickness=1,
                highlightbackground=CARD_BORDER,
            ).pack(side="left", padx=(0, 10))

    # ---------- Search ----------
    def _search_dirs(self) -> dict:
        return resolved_output_dirs(ROOT, self._settings)

    def _build_page_search(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG_MAIN)
        wrap.pack(fill="both", expand=True)
        top = tk.Frame(wrap, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))
        self._section_title(top, "TRANSCRIPTS / SEARCH", "Search Transcripts",
                            "Find a word or \"exact phrase\" in every transcript. Double-click a hit to play the video at that moment.")
        row = tk.Frame(wrap, bg=BG_MAIN)
        row.pack(fill="x", pady=(0, 10))
        self.search_var = tk.StringVar()
        self.search_status_var = tk.StringVar(value="")
        ent = tk.Entry(row, textvariable=self.search_var, width=50, bg=FIELD_BG, fg=FG, insertbackground=FG,
                       highlightthickness=1, highlightbackground=CARD_BORDER, font=(FONT_MONO, 11))
        ent.pack(side="left", ipady=4)
        ent.bind("<Return>", lambda _e: self._run_search())
        ttk.Button(row, text="Search", style="Accent.TButton", command=self._run_search).pack(side="left", padx=(10, 0))
        ttk.Button(row, text="Play at time", command=self._search_open_video).pack(side="left", padx=(10, 0))
        ttk.Button(row, text="Open transcript", command=self._search_open_transcript).pack(side="left", padx=(10, 0))
        ttk.Button(row, text="Rebuild index", command=self._search_rebuild).pack(side="left", padx=(10, 0))
        self._body_label(row, textvariable=self.search_status_var, fg=ACCENT_MINT, mono=True, size=10).pack(side="right")
        card = self._card(wrap, expand=True)
        shell = tk.Frame(card, bg=BG_CARD)
        shell.pack(fill="both", expand=True, padx=18, pady=18)
        cols = ("video", "time", "context")
        self.search_tree = ttk.Treeview(shell, columns=cols, show="headings", height=20)
        for c, w, label in (("video", 320, "Video"), ("time", 80, "Time"), ("context", 760, "Context")):
            self.search_tree.heading(c, text=label)
            self.search_tree.column(c, width=w, anchor="w", stretch=(c == "context"))
        self.search_tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(shell, orient="vertical", command=self.search_tree.yview)
        sb.pack(side="right", fill="y")
        self.search_tree.configure(yscrollcommand=sb.set)
        self.search_tree.bind("<Double-1>", lambda _e: self._search_open_video())
        self._search_hits: dict[str, dict] = {}
        try:
            n, segs = transcript_search.stats()
            self.search_status_var.set(f"{n} transcripts, {segs} segments indexed")
        except Exception as exc:
            self.search_status_var.set(f"index unavailable: {exc}")

    def _run_search(self) -> None:
        q = self.search_var.get().strip()
        self.search_tree.delete(*self.search_tree.get_children())
        self._search_hits = {}
        if not q:
            return
        hits = transcript_search.search(q)
        for h in hits:
            t = int(h["start"])
            iid = self.search_tree.insert("", "end", values=(h["stem"], f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}", h["context"]))
            self._search_hits[iid] = h
        videos = len({h["stem"] for h in hits})
        self.search_status_var.set(f"{len(hits)} matches in {videos} videos" if hits else "no matches")

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
            self.after(0, lambda: self.search_status_var.set(msg))

        threading.Thread(target=work, daemon=True).start()

    def _build_page_review(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG_MAIN)
        wrap.pack(fill="both", expand=True)
        top = tk.Frame(wrap, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))
        self._section_title(top, "TRANSCRIPTS / REVIEW", "Review Flagged Spots",
                            "Check low-confidence segments: play the audio there, fix the text, save. The original text is kept.")
        row = tk.Frame(wrap, bg=BG_MAIN)
        row.pack(fill="x", pady=(0, 10))
        self.review_stem_var = tk.StringVar()
        self.review_status_var = tk.StringVar(value="")
        self.review_combo = ttk.Combobox(row, textvariable=self.review_stem_var, state="readonly", width=70)
        self.review_combo.pack(side="left")
        self.review_combo.bind("<<ComboboxSelected>>", lambda _e: self._review_load_flags())
        ttk.Button(row, text="Refresh list", command=self._review_refresh_list).pack(side="left", padx=(10, 0))
        self._body_label(row, textvariable=self.review_status_var, fg=ACCENT_MINT, mono=True, size=10).pack(side="right")
        card = self._card(wrap, expand=True)
        shell = tk.Frame(card, bg=BG_CARD)
        shell.pack(fill="both", expand=True, padx=18, pady=18)
        cols = ("time", "state", "reason", "text")
        self.review_tree = ttk.Treeview(shell, columns=cols, show="headings", height=11, selectmode="browse")
        for c, w, label in (("time", 80, "Time"), ("state", 90, "State"), ("reason", 260, "Why flagged"), ("text", 640, "Text")):
            self.review_tree.heading(c, text=label)
            self.review_tree.column(c, width=w, anchor="w", stretch=(c == "text"))
        self.review_tree.pack(fill="both", expand=True)
        self.review_tree.bind("<<TreeviewSelect>>", lambda _e: self._review_select())
        self.review_tree.bind("<Double-1>", lambda _e: self._review_play())
        self.review_orig_var = tk.StringVar(value="")
        self._body_label(shell, textvariable=self.review_orig_var, fg=FG_DIM, size=10).pack(anchor="w", pady=(10, 4))
        self.review_text = tk.Text(shell, height=4, wrap="word", bg=FIELD_BG, fg=FG, insertbackground=FG,
                                   relief="flat", highlightthickness=1, highlightbackground=CARD_BORDER, font=(FONT_MONO, 11))
        self.review_text.pack(fill="x")
        btns = tk.Frame(shell, bg=BG_CARD)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Play snippet", command=self._review_play).pack(side="left")
        ttk.Button(btns, text="Save correction", style="Accent.TButton", command=self._review_save).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Mark OK", command=self._review_mark_ok).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Revert to original", command=self._review_revert).pack(side="left", padx=(8, 0))
        self._review_items: dict[str, dict] = {}
        self._review_index: dict[str, dict] = {}
        self._review_refresh_list()

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
        if not t:
            return
        for it in transcript_review.flagged_items(t["json_path"]):
            s = int(it["start"])
            state = "edited" if it["original_text"] is not None else ("OK" if it["reviewed"] else "to review")
            iid = tree.insert("", "end", values=(f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}", state, it["reason"][:80], it["text"]))
            self._review_items[iid] = it
            if keep_index is not None and it["index"] == keep_index:
                tree.selection_set(iid)
                tree.see(iid)

    def _review_selected(self) -> dict | None:
        sel = self.review_tree.selection()
        return self._review_items.get(sel[0]) if sel else None

    def _review_select(self) -> None:
        it = self._review_selected()
        if not it:
            return
        self.review_text.delete("1.0", "end")
        self.review_text.insert("1.0", it["text"])
        orig = it["original_text"]
        self.review_orig_var.set(f"Original: {str(orig).strip()}" if orig is not None else f"Why flagged: {it['reason']}")

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
        if not it or not t:
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

    def _build_page_settings(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG_MAIN)
        wrap.pack(fill="both", expand=True)
        top = tk.Frame(wrap, bg=BG_MAIN)
        top.pack(fill="x", pady=(0, 14))
        self._section_title(top, "SYSTEM CONTROL MATRIX", "Settings", "Adjust global Whisper pipeline performance and storage parameters.")
        actions = tk.Frame(top, bg=BG_MAIN)
        actions.pack(anchor="e", pady=(8, 0))

        def save_quick() -> None:
            save_settings(
                ROOT,
                {
                    "heartbeat_seconds": float(self.set_hb.get()),
                    "stall_timeout_seconds": float(self.set_stall.get()),
                    "max_concurrent_jobs": int(self.set_jobs.get()),
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
                },
            )
            self._settings = load_settings(ROOT)
            messagebox.showinfo("Settings", "Saved.")

        ttk.Button(actions, text="All settings...", command=self._open_all_settings).pack(side="left", padx=(0, 12))
        ttk.Button(actions, text="Discard", command=self._sync_settings_page_vars).pack(side="left")
        ttk.Button(actions, text="Save changes", style="Accent.TButton", command=save_quick).pack(side="left", padx=(12, 0))

        grid = tk.Frame(wrap, bg=BG_MAIN)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=2)
        grid.columnconfigure(1, weight=1)

        left = self._card(grid, autopack=False)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 12))
        lf = tk.Frame(left, bg=BG_CARD)
        lf.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(lf, "Execution Control", size=18, fg=FG).pack(anchor="w")
        exec_grid = tk.Frame(lf, bg=BG_CARD)
        exec_grid.pack(fill="x", pady=(14, 0))
        self._settings_row(exec_grid, 0, "Max concurrent jobs", self.set_jobs, spin=True, maxv=10, minv=1, intv=True)
        self._settings_row(exec_grid, 1, "Heartbeat (s)", self.set_hb, spin=True, maxv=120)
        self._settings_row(exec_grid, 2, "Stall timeout (s)", self.set_stall, spin=True, maxv=86400, minv=60)
        tk.Label(exec_grid, text="Start with scan-existing", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=3, column=0, sticky="w", pady=8)
        ttk.Checkbutton(exec_grid, variable=self.set_scan).grid(row=3, column=1, sticky="w", pady=8)
        whisper_models = ("tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo", "distil-large-v3")
        tk.Label(exec_grid, text="GPU Whisper model", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=4, column=0, sticky="w", pady=8)
        ttk.Combobox(exec_grid, textvariable=self.set_whisper_gpu, values=whisper_models, state="readonly", width=18).grid(row=4, column=1, sticky="w", pady=8)
        tk.Label(exec_grid, text="CPU Whisper model", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=5, column=0, sticky="w", pady=8)
        ttk.Combobox(exec_grid, textvariable=self.set_whisper_cpu, values=whisper_models, state="readonly", width=18).grid(row=5, column=1, sticky="w", pady=8)
        tk.Label(exec_grid, text="Decode profile", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=6, column=0, sticky="w", pady=8)
        ttk.Combobox(exec_grid, textvariable=self.set_whisper_profile, values=("auto", "speech", "music", "legacy"), state="readonly", width=18).grid(row=6, column=1, sticky="w", pady=8)
        tk.Label(exec_grid, text="Language (auto or code)", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=7, column=0, sticky="w", pady=8)
        ttk.Combobox(exec_grid, textvariable=self.set_whisper_language, values=("auto", "en", "de", "es", "fr"), width=18).grid(row=7, column=1, sticky="w", pady=8)
        tk.Label(exec_grid, text="Transcription engine", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=8, column=0, sticky="w", pady=8)
        ttk.Combobox(exec_grid, textvariable=self.set_asr_engine, values=("whisper", "voxtral-mini-3b", "granite-2b"), state="readonly", width=18).grid(row=8, column=1, sticky="w", pady=8)
        tk.Label(exec_grid, text="Voxtral cross-check", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=9, column=0, sticky="w", pady=8)
        ttk.Checkbutton(exec_grid, variable=self.set_asr_crosscheck).grid(row=9, column=1, sticky="w", pady=8)
        tk.Label(exec_grid, text="Fast batched mode (about 4x faster)", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=10, column=0, sticky="w", pady=8)
        ttk.Checkbutton(exec_grid, variable=self.set_whisper_batched).grid(row=10, column=1, sticky="w", pady=8)
        tk.Label(exec_grid, text="Skip files already transcribed", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=11, column=0, sticky="w", pady=8)
        ttk.Checkbutton(exec_grid, variable=self.set_skip_duplicates).grid(row=11, column=1, sticky="w", pady=8)
        tk.Label(exec_grid, text="Use YouTube captions when available", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=12, column=0, sticky="w", pady=8)
        ttk.Checkbutton(exec_grid, variable=self.set_prefer_captions).grid(row=12, column=1, sticky="w", pady=8)

        right = self._card(grid, autopack=False)
        right.grid(row=0, column=1, sticky="nsew", pady=(0, 12))
        rf = tk.Frame(right, bg=BG_CARD)
        rf.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(rf, "Resource Logging", size=18, fg=FG).pack(anchor="w")
        tk.Label(rf, text="Log profile", fg=FG_DIM, bg=BG_CARD, anchor="w").pack(anchor="w", pady=(14, 6))
        ttk.Combobox(rf, textvariable=self.set_profile, values=("standard", "tuning", "debug"), state="readonly", width=20).pack(anchor="w")
        tk.Label(rf, text="LLM: try API before browser", fg=FG_DIM, bg=BG_CARD, anchor="w").pack(anchor="w", pady=(18, 6))
        ttk.Checkbutton(rf, variable=self.set_handoff_api).pack(anchor="w")
        self._body_label(rf, "Settings file", fg=FG_DIM, mono=True, size=10).pack(anchor="w", pady=(18, 4))
        self._body_label(rf, str(settings_path(ROOT)), fg=ACCENT, mono=True, size=10, wraplength=260).pack(anchor="w")

        paths = self._card(wrap)
        paths.pack(fill="x", pady=(0, 12))
        pf = tk.Frame(paths, bg=BG_CARD)
        pf.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(pf, "Path Configuration", size=18, fg=FG).pack(anchor="w")
        form = tk.Frame(pf, bg=BG_CARD)
        form.pack(fill="x", pady=(14, 0))
        tk.Label(form, text="Watch directory", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=0, column=0, sticky="w", pady=6, padx=(0, 12))
        tk.Entry(form, textvariable=self.set_watch_dir, width=56, bg=FIELD_BG, fg=FG, insertbackground=FG, highlightthickness=1, highlightbackground=CARD_BORDER).grid(row=0, column=1, sticky="ew", pady=6)
        tk.Label(form, text="Completed video directory", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=1, column=0, sticky="w", pady=6, padx=(0, 12))
        tk.Entry(form, textvariable=self.set_completed_dir, width=56, bg=FIELD_BG, fg=FG, insertbackground=FG, highlightthickness=1, highlightbackground=CARD_BORDER).grid(row=1, column=1, sticky="ew", pady=6)
        tk.Label(form, text="Completed audio directory", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=2, column=0, sticky="w", pady=6, padx=(0, 12))
        tk.Entry(form, textvariable=self.set_completed_audio_dir, width=56, bg=FIELD_BG, fg=FG, insertbackground=FG, highlightthickness=1, highlightbackground=CARD_BORDER).grid(row=2, column=1, sticky="ew", pady=6)
        form.columnconfigure(1, weight=1)

        lib = self._card(wrap)
        lib.pack(fill="x", pady=(0, 12))
        lbf = tk.Frame(lib, bg=BG_CARD)
        lbf.pack(fill="both", expand=True, padx=18, pady=18)
        self._headline(lbf, "Aeonimus Library", size=18, fg=FG).pack(anchor="w")
        self._body_label(
            lbf,
            "Shared with the other Aeonimus apps. Finished transcripts are saved to its transcripts folder, and the "
            "Import from URL Lister button moves new link lists here from the Downloads inbox. Changing it here changes it for every Aeonimus app.",
            fg=FG_DIM, size=10, wraplength=980,
        ).pack(anchor="w", pady=(6, 0))
        lform = tk.Frame(lbf, bg=BG_CARD)
        lform.pack(fill="x", pady=(12, 0))
        tk.Label(lform, text="Library location", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=0, column=0, sticky="w", pady=6, padx=(0, 12))
        tk.Entry(lform, textvariable=self.set_library_dir, width=56, bg=FIELD_BG, fg=FG, insertbackground=FG, highlightthickness=1, highlightbackground=CARD_BORDER).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(lform, text="Browse...", command=self._browse_library_location).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(lform, text="Use this location", command=self._apply_library_location).grid(row=0, column=3, padx=(8, 0))
        tk.Label(lform, text="Downloads inbox", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(row=1, column=0, sticky="w", pady=6, padx=(0, 12))
        tk.Label(lform, textvariable=self.handoff_inbox_var, fg=ACCENT, bg=BG_CARD, anchor="w", font=(FONT_MONO, 9)).grid(row=1, column=1, sticky="w", pady=6)
        lform.columnconfigure(1, weight=1)

        note = self._card(wrap)
        note.pack(fill="x")
        nf = tk.Frame(note, bg=BG_CARD)
        nf.pack(fill="x", padx=18, pady=18)
        self._body_label(nf, "CORE WARNING", fg=ACCENT_RED, mono=True, size=10, bold=True).pack(anchor="w")
        self._body_label(nf, "Changing max concurrent jobs during active transcription cycles can increase contention and stall risk across worker processes.", fg=FG, size=10, wraplength=980).pack(anchor="w", pady=(6, 0))

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
                highlightbackground="#1e2a38",
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
            if i == row and "empty" not in tags and "hover" not in tags:
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
                tv.insert("", "end", iid=str(i), text=p.name, values=(when, "\u203a"))
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
        for p in paths:
            try:
                data = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            warns = coverage_check.warnings_from_json_data(data)
            if warns:
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
            self.set_library_dir.set(str(Path(chosen)))
            self._apply_library_location()

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
            highlightbackground="#1e2a38",
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
                        str(j.get("engine") or "-"), prog, queue_control.format_eta(eta)))
            if first is None:
                first = (j, frac, eta)
        queued = [q for q in (data.get("queued_order") or []) if q not in active_paths]
        self._queue_order = queue_control.ordered(queued, control["order"])
        for n, q in enumerate(self._queue_order, 1):
            state = "paused" if self._queue_paused else f"queued #{n}"
            if not tree.exists(q):
                tree.insert("", tk.END, iid=q, values=(Path(q).name, state, "-", "-", "-"))
        if keep and tree.exists(keep):
            tree.selection_set(keep)
        if first:
            j, frac, eta = first
            dur = float(j.get("duration") or 0)
            seg = float(j.get("last_segment_end") or 0)
            self.live_name_var.set(Path(str(j.get("path", ""))).name)
            self.live_bar["value"] = (frac or 0) * 100
            pos = f"{queue_control.format_eta(seg)} / {queue_control.format_eta(dur)}" if dur else f"{seg:.0f}s"
            self.live_detail_var.set(f"{j.get('engine') or 'engine ?'}  |  {pos}  |  ETA {queue_control.format_eta(eta)}")
        else:
            self.live_name_var.set("Queue paused" if self._queue_paused and self._queue_order else "Nothing transcribing right now")
            self.live_bar["value"] = 0
            self.live_detail_var.set(f"{len(self._queue_order)} file(s) waiting" if self._queue_order else "")

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
            bg = "#5c1010" if (err or stalled) else "#5c4a10"
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
