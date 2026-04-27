"""Desktop control panel for the transcription folder watcher (dark dashboard shell)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import psutil

from post_transcript_handoff import (
    build_handoff_bundle,
    handoff_llm_browser,
    handoff_notebooklm,
    handoff_open_folder,
    handoff_try_llm_api,
    write_api_handoff_artifact,
)
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

BG_MAIN = "#0a0d14"
BG_SIDE = "#0f1419"
BG_CARD = "#121820"
BG_HEAD = "#07090e"
ACCENT = "#47c1ff"
ACCENT_GREEN = "#22c55e"
FG = "#e8eef7"
FG_DIM = "#8b9bb4"
BG_LOG = "#0d1117"
WARN_BG = "#5c1010"

NAV_KEYS = ("dashboard", "queue", "monitor", "logs", "handoff", "settings")


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


class ControlPanel(tk.Tk):
    def __init__(self, lock_sock: socket.socket) -> None:
        super().__init__()
        self.lock_sock = lock_sock
        self.title("Pipeline Watcher")
        self.geometry("1100x780")
        self.minsize(1000, 700)
        self.configure(bg=BG_MAIN)

        self._settings = load_settings(ROOT)
        self._current_nav = "dashboard"
        self._nav_buttons: dict[str, tk.Label] = {}

        self.summary_var = tk.StringVar(value="Starting…")
        self.phase_var = tk.StringVar(value="—")
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

        self.set_hb = tk.DoubleVar(value=float(self._settings["heartbeat_seconds"]))
        self.set_stall = tk.DoubleVar(value=float(self._settings["stall_timeout_seconds"]))
        self.set_jobs = tk.IntVar(value=int(self._settings["max_concurrent_jobs"]))
        self.set_profile = tk.StringVar(value=str(self._settings.get("log_profile", "standard")))
        self.set_scan = tk.BooleanVar(value=bool(self._settings.get("start_scan_existing", True)))
        self.set_watch_dir = tk.StringVar(value=str(self._settings.get("watch_dir", "watch_incoming")))
        self.set_completed_dir = tk.StringVar(value=str(self._settings.get("completed_dir", "")))
        self.set_handoff_api = tk.BooleanVar(value=bool(self._settings.get("handoff_try_api_first", False)))

        self._handoff_initialized = False
        self._last_handoff_completed_path: str | None = None

        self._apply_theme()
        self._set_window_icon()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh()

    def _on_close(self) -> None:
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
        style.configure(".", background=BG_MAIN, foreground=FG, fieldbackground=BG_CARD)
        style.configure("TFrame", background=BG_MAIN)
        style.configure("Card.TFrame", background=BG_CARD)
        style.configure("TLabel", background=BG_MAIN, foreground=FG)
        style.configure("Card.TLabel", background=BG_CARD, foreground=FG)
        style.configure("Dim.TLabel", background=BG_MAIN, foreground=FG_DIM)
        style.configure("TButton", background="#1a2836", foreground=FG)
        style.map("TButton", background=[("active", "#243447")])
        style.configure("Accent.TButton", background="#0d3d52", foreground=ACCENT)
        style.configure("Danger.TButton", background="#4a1515", foreground="#ffcccc")
        style.configure(
            "Treeview",
            background=BG_LOG,
            foreground=FG,
            fieldbackground=BG_LOG,
            rowheight=24,
        )
        style.configure("Treeview.Heading", background="#1a2836", foreground=ACCENT)
        style.map("Treeview", background=[("selected", "#1e3a5f")])

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=BG_MAIN)
        root.pack(fill="both", expand=True)

        head = tk.Frame(root, bg=BG_HEAD, height=52)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(
            head,
            text="Pipeline Watcher",
            font=("Segoe UI", 15, "bold"),
            fg=ACCENT,
            bg=BG_HEAD,
        ).pack(side="left", padx=(16, 8), pady=10)
        tk.Label(
            head,
            text="Watch folder · Whisper · Handoff",
            font=("Segoe UI", 10),
            fg=FG_DIM,
            bg=BG_HEAD,
        ).pack(side="left", pady=10)
        tk.Label(
            head,
            textvariable=self.node_status_var,
            font=("Segoe UI", 10, "bold"),
            fg=ACCENT_GREEN,
            bg=BG_HEAD,
        ).pack(side="right", padx=16, pady=10)

        body = tk.Frame(root, bg=BG_MAIN)
        body.pack(fill="both", expand=True)

        side = tk.Frame(body, bg=BG_SIDE, width=220)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        tk.Label(
            side,
            text="CORE ENGINE",
            font=("Segoe UI", 9, "bold"),
            fg=FG_DIM,
            bg=BG_SIDE,
        ).pack(anchor="w", padx=14, pady=(18, 4))
        tk.Label(
            side,
            text="v-pipeline-01",
            font=("Segoe UI", 9),
            fg=ACCENT,
            bg=BG_SIDE,
        ).pack(anchor="w", padx=14, pady=(0, 12))

        nav_items = [
            ("Dashboard", "dashboard"),
            ("Queue", "queue"),
            ("Monitor", "monitor"),
            ("Logs", "logs"),
            ("Handoff", "handoff"),
            ("Settings", "settings"),
        ]
        for text, key in nav_items:
            lbl = tk.Label(
                side,
                text=f"  {text.upper()}",
                font=("Segoe UI", 10),
                fg=FG,
                bg=BG_SIDE,
                anchor="w",
                cursor="hand2",
                pady=8,
                padx=4,
            )
            lbl.pack(fill="x", padx=8, pady=1)
            lbl.bind("<Button-1>", lambda _e, k=key: self._show_page(k))
            self._nav_buttons[key] = lbl

        tk.Label(
            side,
            text="",
            bg=BG_SIDE,
        ).pack(fill="both", expand=True)
        doc = tk.Label(
            side,
            text="  DOCUMENTATION",
            font=("Segoe UI", 9),
            fg=FG_DIM,
            bg=BG_SIDE,
            anchor="w",
            cursor="hand2",
            pady=10,
        )
        doc.pack(fill="x", padx=8, pady=(0, 12))
        doc.bind("<Button-1>", lambda _e: open_path(ROOT / "readme.md"))

        self.content_host = tk.Frame(body, bg=BG_MAIN)
        self.content_host.pack(side="right", fill="both", expand=True, padx=(0, 14), pady=12)

        self.pages: dict[str, tk.Frame] = {}
        for key in NAV_KEYS:
            f = tk.Frame(self.content_host, bg=BG_MAIN)
            f.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = f
        self.content_host.grid_rowconfigure(0, weight=1)
        self.content_host.grid_columnconfigure(0, weight=1)

        self._build_page_dashboard(self.pages["dashboard"])
        self._build_page_queue(self.pages["queue"])
        self._build_page_monitor(self.pages["monitor"])
        self._build_page_logs(self.pages["logs"])
        self._build_page_handoff(self.pages["handoff"])
        self._build_page_settings(self.pages["settings"])

        self._show_page("dashboard")

    def _highlight_nav(self, key: str) -> None:
        for k, lbl in self._nav_buttons.items():
            bg = "#152028" if k == key else BG_SIDE
            lbl.configure(bg=bg)
        self._current_nav = key

    def _show_page(self, key: str) -> None:
        if key not in self.pages:
            return
        for f in self.pages.values():
            f.grid_remove()
        self.pages[key].grid()
        self._highlight_nav(key)

    def _card(self, parent: tk.Misc, *, expand: bool = False, **pack_kw) -> tk.Frame:
        f = tk.Frame(parent, bg=BG_CARD, highlightthickness=1, highlightbackground="#1e2a38")
        f.pack(fill="both", expand=expand, **pack_kw)
        return f

    def _build_page_dashboard(self, parent: tk.Frame) -> None:
        outer = tk.Frame(parent, bg=BG_MAIN)
        outer.pack(fill="both", expand=True)

        self.banner_frame = tk.Frame(outer, bg=WARN_BG)
        self.banner_label = tk.Label(
            self.banner_frame,
            text="",
            anchor="w",
            justify="left",
            wraplength=900,
            padx=12,
            pady=10,
            fg="#fff",
            bg=WARN_BG,
            font=("Segoe UI", 10),
        )
        self.banner_label.pack(fill="x")

        ctrl = self._card(outer, pady=(0, 10))
        cf = tk.Frame(ctrl, bg=BG_CARD)
        cf.pack(fill="x", padx=14, pady=14)
        tk.Label(
            cf,
            text="Watcher control",
            font=("Segoe UI", 12, "bold"),
            fg=ACCENT,
            bg=BG_CARD,
        ).pack(anchor="w")
        row = tk.Frame(cf, bg=BG_CARD)
        row.pack(fill="x", pady=(10, 0))
        self.power_btn = ttk.Button(row, text="Start watcher", command=self._toggle_watcher_power)
        self.power_btn.pack(side="left")
        ttk.Button(row, text="Refresh now", command=self.refresh_once).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(row, text="Auto-refresh (1s)", variable=self.auto_refresh_var).pack(side="left", padx=(16, 0))
        tk.Label(
            cf,
            text=(
                "Stop ends the watcher process tree immediately (workers included). "
                "Partial transcripts may remain under generated_*; re-runs start from scratch."
            ),
            fg=FG_DIM,
            bg=BG_CARD,
            wraplength=920,
            justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(10, 0))

        status = self._card(outer, pady=(0, 10))
        sf = tk.Frame(status, bg=BG_CARD)
        sf.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            sf,
            textvariable=self.summary_var,
            font=("Segoe UI", 13, "bold"),
            fg=FG,
            bg=BG_CARD,
        ).pack(anchor="w")
        ph = tk.Frame(sf, bg=BG_CARD)
        ph.pack(fill="x", pady=(8, 0))
        tk.Label(ph, text="Phase:", fg=FG_DIM, bg=BG_CARD, width=10, anchor="w").pack(side="left")
        tk.Label(ph, textvariable=self.phase_var, font=("Segoe UI", 11, "bold"), fg=ACCENT, bg=BG_CARD).pack(
            side="left"
        )
        tk.Label(sf, textvariable=self.detail_var, fg=FG_DIM, bg=BG_CARD, wraplength=920, justify="left").pack(
            anchor="w", pady=(6, 0)
        )
        grid = tk.Frame(sf, bg=BG_CARD)
        grid.pack(fill="x", pady=(10, 0))
        self._row_dark(grid, 0, "Current file:", self.file_var)
        self._row_dark(grid, 1, "Watcher PID:", self.pid_var)
        self._row_dark(grid, 2, "Worker PID:", self.worker_pid_var)
        self._row_dark(grid, 3, "Heartbeat:", self.heartbeat_var)
        self._row_dark(grid, 4, "Last progress:", self.progress_var)
        self._row_dark(grid, 5, "Segment end:", self.segment_var)
        self._row_dark(grid, 6, "Active jobs:", self.active_jobs_var)
        self._row_dark(grid, 7, "Last error:", self.error_var)

        act = self._card(outer, pady=(0, 10))
        af = tk.Frame(act, bg=BG_CARD)
        af.pack(fill="x", padx=14, pady=12)
        tk.Label(af, text="Quick actions", fg=ACCENT, bg=BG_CARD, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ar = tk.Frame(af, bg=BG_CARD)
        ar.pack(fill="x", pady=(8, 0))
        ttk.Button(ar, text="Open watch folder", command=lambda: open_path(self._watch_dir())).pack(side="left")
        ttk.Button(ar, text="Open Completed", command=lambda: open_path(self._completed_dir())).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            ar,
            text="Transcript texts",
            command=lambda: open_path(ROOT / "generated_transcript_combined_texts"),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            ar,
            text="Metadata",
            command=lambda: open_path(ROOT / "generated_transcript_metadata_tables"),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(ar, text="Clear last error", command=self._clear_last_error).pack(side="left", padx=(16, 0))

        gauges = self._card(outer, pady=(0, 0))
        gf = tk.Frame(gauges, bg=BG_CARD)
        gf.pack(fill="x", padx=14, pady=12)
        tk.Label(gf, text="Counters", fg=ACCENT, bg=BG_CARD, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        gg = tk.Frame(gf, bg=BG_CARD)
        gg.pack(fill="x", pady=(8, 0))
        self.queue_bar = self._gauge_dark(gg, 0, "Queue", self.queue_var)
        self.completed_bar = self._gauge_dark(gg, 1, "Completed", self.completed_var)
        self.failed_bar = self._gauge_dark(gg, 2, "Failed", self.failed_var)
        self.processed_bar = self._gauge_dark(gg, 3, "Processed", self.processed_var)

    def _row_dark(self, parent: tk.Frame, row: int, label: str, var: tk.StringVar) -> None:
        tk.Label(parent, text=label, fg=FG_DIM, bg=BG_CARD, width=14, anchor="w").grid(
            row=row, column=0, sticky="w", pady=2
        )
        tk.Label(parent, textvariable=var, fg=FG, bg=BG_CARD, anchor="w").grid(
            row=row, column=1, sticky="w", pady=2
        )

    def _gauge_dark(self, parent: tk.Frame, row: int, name: str, var: tk.IntVar) -> ttk.Progressbar:
        tk.Label(parent, text=name, fg=FG_DIM, bg=BG_CARD, width=12, anchor="w").grid(
            row=row, column=0, sticky="w", pady=4
        )
        bar = ttk.Progressbar(parent, orient="horizontal", mode="determinate", maximum=100)
        bar.grid(row=row, column=1, sticky="ew", pady=4, padx=(0, 8))
        parent.columnconfigure(1, weight=1)
        tk.Label(parent, textvariable=var, fg=ACCENT, bg=BG_CARD, width=6, anchor="e").grid(
            row=row, column=2, sticky="e", pady=4
        )
        return bar

    def _build_page_queue(self, parent: tk.Frame) -> None:
        card = self._card(parent, expand=True, pady=0)
        f = tk.Frame(card, bg=BG_CARD)
        f.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            f,
            text="Queue management",
            font=("Segoe UI", 14, "bold"),
            fg=ACCENT,
            bg=BG_CARD,
        ).pack(anchor="w")
        tk.Label(
            f,
            text="Global processing pipeline and media ingestion (watch folder).",
            fg=FG_DIM,
            bg=BG_CARD,
        ).pack(anchor="w", pady=(4, 10))
        tk.Label(f, textvariable=self.queue_meta_var, fg=FG, bg=BG_CARD, font=("Segoe UI", 10)).pack(
            anchor="w", pady=(0, 8)
        )

        cols = ("file", "phase", "pid", "seg")
        self.queue_tree = ttk.Treeview(f, columns=cols, show="headings", height=12)
        self.queue_tree.heading("file", text="Source file")
        self.queue_tree.heading("phase", text="Phase")
        self.queue_tree.heading("pid", text="Worker PID")
        self.queue_tree.heading("seg", text="Segment (s)")
        self.queue_tree.column("file", width=360)
        self.queue_tree.column("phase", width=120)
        self.queue_tree.column("pid", width=100)
        self.queue_tree.column("seg", width=100)
        self.queue_tree.pack(fill="both", expand=True)

        br = tk.Frame(f, bg=BG_CARD)
        br.pack(fill="x", pady=(12, 0))
        ttk.Button(br, text="Start watcher (scan existing)", command=self._start_watcher).pack(side="left")
        ttk.Button(br, text="Open watch folder", command=lambda: open_path(self._watch_dir())).pack(
            side="left", padx=(8, 0)
        )

    def _build_page_monitor(self, parent: tk.Frame) -> None:
        card = self._card(parent, expand=True, pady=0)
        f = tk.Frame(card, bg=BG_CARD)
        f.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            f,
            text="Live telemetry",
            font=("Segoe UI", 14, "bold"),
            fg=ACCENT,
            bg=BG_CARD,
        ).pack(anchor="w")
        tk.Label(
            f,
            textvariable=self.monitor_hint_var,
            fg=FG_DIM,
            bg=BG_CARD,
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(6, 10))
        self.resource_text = tk.Text(
            f,
            wrap="none",
            height=24,
            bg=BG_LOG,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#1e2a38",
            font=("Consolas", 10),
        )
        self.resource_text.pack(fill="both", expand=True)

    def _build_page_logs(self, parent: tk.Frame) -> None:
        card = self._card(parent, expand=True, pady=0)
        f = tk.Frame(card, bg=BG_CARD)
        f.pack(fill="both", expand=True, padx=14, pady=14)
        hr = tk.Frame(f, bg=BG_CARD)
        hr.pack(fill="x")
        tk.Label(
            hr,
            text="System logs",
            font=("Segoe UI", 14, "bold"),
            fg=ACCENT,
            bg=BG_CARD,
        ).pack(side="left")
        ttk.Button(hr, text="Refresh", command=self.refresh_once).pack(side="right")
        self.log_text = tk.Text(
            f,
            wrap="none",
            height=28,
            bg=BG_LOG,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#1e2a38",
            font=("Consolas", 10),
        )
        self.log_text.pack(fill="both", expand=True, pady=(10, 0))

    def _build_page_handoff(self, parent: tk.Frame) -> None:
        card = self._card(parent, expand=True, pady=0)
        f = tk.Frame(card, bg=BG_CARD)
        f.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            f,
            text="Post-transcript routing",
            font=("Segoe UI", 14, "bold"),
            fg=ACCENT,
            bg=BG_CARD,
        ).pack(anchor="w")
        tk.Label(
            f,
            text=(
                "After a job completes, send the transcript to NotebookLM for deeper synthesis, "
                "or to an LLM (ChatGPT, Gemini, Claude, Grok, LM Studio) via clipboard + browser. "
                "NotebookLM has no public upload API—use clipboard, staged text, or manual file upload in the browser."
            ),
            fg=FG_DIM,
            bg=BG_CARD,
            wraplength=920,
            justify="left",
        ).pack(anchor="w", pady=(8, 16))
        ttk.Button(f, text="Send last completed transcript…", command=self._open_handoff).pack(anchor="w")
        ttk.Button(f, text="Open Completed folder", command=lambda: open_path(self._completed_dir())).pack(
            anchor="w", pady=(10, 0)
        )
        ttk.Button(
            f,
            text="Open NotebookLM staging",
            command=lambda: handoff_open_folder(self._watch_dir() / "staged_for_notebooklm"),
        ).pack(anchor="w", pady=(8, 0))

    def _build_page_settings(self, parent: tk.Frame) -> None:
        card = self._card(parent, expand=True, pady=0)
        f = tk.Frame(card, bg=BG_CARD)
        f.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            f,
            text="System control matrix",
            font=("Segoe UI", 14, "bold"),
            fg=ACCENT,
            bg=BG_CARD,
        ).pack(anchor="w")
        tk.Label(
            f,
            text="Global execution parameters (saved to watcher_settings.json).",
            fg=FG_DIM,
            bg=BG_CARD,
        ).pack(anchor="w", pady=(4, 14))

        gf = tk.Frame(f, bg=BG_CARD)
        gf.pack(fill="x")
        r = 0
        self._settings_row(gf, r, "Heartbeat (s)", self.set_hb, spin=True, maxv=120)
        r += 1
        self._settings_row(gf, r, "Stall timeout (s)", self.set_stall, spin=True, maxv=86400, minv=60)
        r += 1
        self._settings_row(gf, r, "Max concurrent jobs", self.set_jobs, spin=True, maxv=10, minv=1, intv=True)
        r += 1
        tk.Label(gf, text="Log profile", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(
            row=r, column=0, sticky="w", pady=6
        )
        ttk.Combobox(
            gf,
            textvariable=self.set_profile,
            values=("standard", "tuning", "debug"),
            state="readonly",
            width=18,
        ).grid(row=r, column=1, sticky="w", pady=6)
        r += 1
        tk.Label(gf, text="Start with scan-existing", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(
            row=r, column=0, sticky="w", pady=6
        )
        ttk.Checkbutton(gf, variable=self.set_scan).grid(row=r, column=1, sticky="w", pady=6)
        r += 1
        tk.Label(gf, text="Watch directory", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(
            row=r, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        tk.Entry(
            gf,
            textvariable=self.set_watch_dir,
            width=42,
            bg=BG_LOG,
            fg=FG,
            insertbackground=FG,
            highlightthickness=1,
            highlightbackground="#1e2a38",
        ).grid(row=r, column=1, sticky="w", pady=6)
        r += 1
        tk.Label(
            gf,
            text="Completed directory (optional)",
            fg=FG_DIM,
            bg=BG_CARD,
            anchor="w",
        ).grid(row=r, column=0, sticky="w", pady=6, padx=(0, 12))
        tk.Entry(
            gf,
            textvariable=self.set_completed_dir,
            width=42,
            bg=BG_LOG,
            fg=FG,
            insertbackground=FG,
            highlightthickness=1,
            highlightbackground="#1e2a38",
        ).grid(row=r, column=1, sticky="w", pady=6)
        r += 1
        tk.Label(gf, text="LLM: try API before browser", fg=FG_DIM, bg=BG_CARD, anchor="w").grid(
            row=r, column=0, sticky="w", pady=6, padx=(0, 12)
        )
        ttk.Checkbutton(gf, variable=self.set_handoff_api).grid(row=r, column=1, sticky="w", pady=6)
        r += 1

        br = tk.Frame(f, bg=BG_CARD)
        br.pack(fill="x", pady=(20, 0))

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
                    "completed_dir": self.set_completed_dir.get().strip(),
                    "handoff_try_api_first": bool(self.set_handoff_api.get()),
                },
            )
            self._settings = load_settings(ROOT)
            messagebox.showinfo("Settings", "Saved.")

        ttk.Button(br, text="Save changes", command=save_quick, style="Accent.TButton").pack(side="left")
        ttk.Button(br, text="Handoff & LLM defaults…", command=self._open_settings).pack(side="left", padx=(10, 0))
        tk.Label(
            f,
            text=f"File: {settings_path(ROOT)}",
            fg=FG_DIM,
            bg=BG_CARD,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(16, 0))

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
        if str(self._settings.get("completed_dir") or "").strip():
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

    def _status_path(self) -> Path:
        return self._watch_dir() / "watch_status.json"

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
        """On new completion, open handoff dialog or run defaults (see post_transcript_always_ask)."""
        self._settings = load_settings(ROOT)
        completed = data.get("last_completed_video")
        cstr = str(completed) if completed else None
        last_ev = str(data.get("last_event") or "")

        if not self._handoff_initialized:
            self._handoff_initialized = True
            self._last_handoff_completed_path = cstr
            return

        if not cstr or not last_ev.startswith("completed:"):
            return
        if cstr == self._last_handoff_completed_path:
            return
        self._last_handoff_completed_path = cstr
        self.after(300, self._open_handoff)

    def _clear_last_error(self) -> None:
        sp = self._status_path()
        if not sp.exists():
            return
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            data["last_error"] = None
            sp.write_text(json.dumps(data, indent=2), encoding="utf-8")
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
        ask = tk.BooleanVar(value=bool(self._settings.get("post_transcript_always_ask", True)))
        dest = tk.StringVar(value=str(self._settings.get("default_post_transcript_destination", "notebooklm")))
        prov = tk.StringVar(value=str(self._settings.get("default_llm_provider", "chatgpt")))
        lm_url = tk.StringVar(value=str(self._settings.get("lm_studio_base_url", "")))
        lm_model = tk.StringVar(value=str(self._settings.get("lm_studio_model", "")))
        handoff_api = tk.BooleanVar(value=bool(self._settings.get("handoff_try_api_first", False)))
        wdir = tk.StringVar(value=str(self._settings.get("watch_dir", "watch_incoming")))
        cdir = tk.StringVar(value=str(self._settings.get("completed_dir", "")))
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
        ttk.Label(pad, text="Completed directory").grid(row=r, column=0, sticky="w")
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
            foreground="#555",
        ).grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _sync_settings_page_vars(self) -> None:
        s = load_settings(ROOT)
        self.set_hb.set(float(s["heartbeat_seconds"]))
        self.set_stall.set(float(s["stall_timeout_seconds"]))
        self.set_jobs.set(int(s["max_concurrent_jobs"]))
        self.set_profile.set(str(s.get("log_profile", "standard")))
        self.set_scan.set(bool(s.get("start_scan_existing", True)))
        self.set_watch_dir.set(str(s.get("watch_dir", "watch_incoming")))
        self.set_completed_dir.set(str(s.get("completed_dir", "")))
        self.set_handoff_api.set(bool(s.get("handoff_try_api_first", False)))

    def _open_handoff(self) -> None:
        data = self._read_status()
        dest_path = data.get("last_completed_video")
        if not dest_path:
            messagebox.showinfo(
                "Handoff",
                "No completed video path in status yet. Finish a job first, or open Completed manually.",
            )
            return
        video = Path(str(dest_path))
        stem = video.stem
        completed_dir = video.parent
        txt = completed_dir / f"{stem}.txt"
        self._settings = load_settings(ROOT)
        if self._settings.get("post_transcript_always_ask", True):
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
        self._settings = load_settings(ROOT)
        data = self._read_status()
        self._apply_status(data)
        self._load_log_tail()
        self._load_resource_tail()
        self._refresh_queue_tree(data)
        self._maybe_auto_handoff(data)

    def refresh(self) -> None:
        self.refresh_once()
        delay_ms = 1000 if self.auto_refresh_var.get() else 10_000
        self.after(delay_ms, self.refresh)

    def _read_status(self) -> dict:
        sp = self._status_path()
        if not sp.exists():
            return {}
        try:
            return json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _refresh_queue_tree(self, data: dict) -> None:
        tree = self.queue_tree
        for i in tree.get_children():
            tree.delete(i)
        for j in data.get("active_jobs") or []:
            p = str(j.get("path", ""))
            tree.insert(
                "",
                tk.END,
                values=(
                    Path(p).name if p else "—",
                    str(j.get("phase", "")),
                    str(j.get("worker_pid", "")),
                    f'{float(j.get("last_segment_end") or 0):.1f}',
                ),
            )
        self.queue_meta_var.set(
            f"Queue depth: {int(data.get('queue_count') or 0)} · "
            f"Max concurrent: {int(data.get('max_concurrent_jobs') or 1)} · "
            f"Active jobs: {len(data.get('active_jobs') or [])}"
        )

    def _apply_status(self, data: dict) -> None:
        running_flag = bool(data.get("running", False))
        pid = int(data.get("pid") or 0)
        alive = running_flag and process_alive(pid)
        stale_running = running_flag and not alive

        phase = str(data.get("phase") or data.get("status") or "unknown")
        status = str(data.get("status", "unknown"))
        stalled = bool(data.get("stalled"))
        err = data.get("last_error")

        if stale_running:
            self.summary_var.set("OFF (stale status — PID not running)")
            self.phase_var.set("off")
            self.detail_var.set("Start the watcher or refresh after a clean stop.")
            self.node_status_var.set("SYSTEM: UNKNOWN")
        elif not running_flag or status == "stopped":
            self.summary_var.set("OFF — watcher stopped")
            self.phase_var.set(str(data.get("phase") or "off"))
            self.detail_var.set("Use Start on Dashboard or Queue to launch.")
            self.node_status_var.set("SYSTEM: STOPPED")
        else:
            self.summary_var.set("ON — watcher running")
            self.phase_var.set(phase)
            detail = f"Status: {status}"
            if stalled:
                detail += " · stalled flag set"
            mj = int(data.get("max_concurrent_jobs", 1) or 1)
            if mj > 1:
                detail += f" · concurrency={mj} (GPU contention risk — open Monitor)"
            self.detail_var.set(detail)
            self.node_status_var.set("SYSTEM: RUNNING")

        stall_sec = data.get("stall_timeout_seconds")
        prog_at = data.get("last_progress_at")
        if stall_sec and prog_at and status == "processing":
            self.detail_var.set(
                f"{self.detail_var.get()} · stall timeout {stall_sec}s (no progress kills worker)"
            )

        self.file_var.set(str(data.get("current_file") or "—"))
        self.heartbeat_var.set(str(data.get("last_heartbeat") or "—"))
        self.progress_var.set(str(data.get("last_progress_at") or "—"))
        self.segment_var.set(f"{float(data.get('last_segment_end', 0.0) or 0.0):.2f}s")
        self.pid_var.set(str(data.get("pid") or "—"))
        self.worker_pid_var.set(str(data.get("last_worker_pid") or "—"))
        self.error_var.set(str(err) if err else "—")

        jobs = data.get("active_jobs") or []
        if jobs:
            parts = []
            for j in jobs:
                parts.append(
                    f'{Path(str(j.get("path", ""))).name} (pid {j.get("worker_pid")}, {j.get("phase")})'
                )
            self.active_jobs_var.set("; ".join(parts))
        else:
            self.active_jobs_var.set("—")

        queue_count = int(data.get("queue_count", 0) or 0)
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

        can_stop = bool(running_flag and alive)
        can_start = not can_stop or stale_running
        if can_stop:
            self.power_btn.configure(text="Stop watcher", style="Danger.TButton")
        else:
            self.power_btn.configure(text="Start watcher", style="Accent.TButton")
        self.power_btn.state(["!disabled"] if (can_start or can_stop) else ["disabled"])

        show_banner = bool(err) or stalled or stale_running
        msg_parts: list[str] = []
        if stale_running:
            msg_parts.append(
                "Status file says running but the process is gone — use Start or refresh after a clean stop."
            )
        if stalled:
            msg_parts.append("Last job stalled (worker killed after progress timeout).")
        if err:
            msg_parts.append(f"Error: {err}")
        if show_banner:
            bg = "#5c1010" if (err or stalled) else "#5c4a10"
            self.banner_frame.configure(bg=bg)
            self.banner_label.configure(text=" · ".join(msg_parts), fg="#fff", bg=bg)
            if not self.banner_frame.winfo_ismapped():
                self.banner_frame.pack(fill="x", pady=(0, 10))
        elif self.banner_frame.winfo_ismapped():
            self.banner_frame.pack_forget()

        prof = str(data.get("log_profile", "standard"))
        self.monitor_hint_var.set(
            f"Log profile: {prof} — JSONL samples in {self._watch_dir() / 'watch_resource.jsonl'} while jobs run."
        )

    def _load_log_tail(self, lines: int = 80) -> None:
        log_file = self._watch_dir() / "watch_events.log"
        if not log_file.exists():
            content = "No log file yet."
        else:
            try:
                raw = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                content = "\n".join(raw[-lines:]) if raw else "Log is empty."
            except Exception as exc:
                content = f"Failed to read log: {exc}"
        self._set_text(self.log_text, content)

    def _load_resource_tail(self, lines: int = 40) -> None:
        res = self._watch_dir() / "watch_resource.jsonl"
        if not res.exists():
            content = "No resource samples yet (set log profile to tuning or debug in Settings)."
        else:
            try:
                raw = res.read_text(encoding="utf-8", errors="replace").splitlines()
                content = "\n".join(raw[-lines:]) if raw else "Empty."
            except Exception as exc:
                content = f"Failed to read resource log: {exc}"
        self._set_text(self.resource_text, content)

    @staticmethod
    def _set_text(widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content)
        widget.configure(state="disabled")


def main() -> None:
    sock = acquire_single_instance_socket()
    if sock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Control panel",
            "Another control panel instance is already running.",
        )
        sys.exit(0)
    app = ControlPanel(sock)
    app.mainloop()


if __name__ == "__main__":
    main()
