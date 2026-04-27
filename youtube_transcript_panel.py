"""Desktop UI: export YouTube captions to WebVTT (playlist or single video)."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from watcher_settings import load_settings, save_settings
from youtube_transcript_export import run_playlist_export, run_single_export

ROOT = Path(__file__).resolve().parent
FG = "#e8eef7"


class CaptionExportApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube caption export")
        self.geometry("720x560")
        self.configure(bg="#0a0d14")
        self._q: queue.Queue = queue.Queue()
        self._settings = load_settings(ROOT)
        self._busy = False

        f = ttk.Frame(self, padding=10)
        f.pack(fill=tk.BOTH, expand=True)
        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass

        ttk.Label(f, text="Playlist URL or ID").grid(row=0, column=0, sticky="w")
        self.var_playlist = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_playlist, width=72).grid(
            row=1, column=0, columnspan=2, sticky="ew"
        )

        ttk.Label(f, text="Single video URL or ID").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.var_video = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_video, width=72).grid(
            row=3, column=0, columnspan=2, sticky="ew"
        )

        ttk.Label(f, text="Caption language (e.g. en)").grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.var_lang = tk.StringVar(value=str(self._settings.get("youtube_transcript_lang") or "en"))
        ttk.Entry(f, textvariable=self.var_lang, width=8).grid(row=4, column=1, sticky="w")

        self.var_conc = tk.IntVar(value=int(self._settings.get("youtube_transcript_concurrency") or 2))
        ttk.Label(f, text="Concurrency").grid(row=5, column=0, sticky="w")
        ttk.Spinbox(f, from_=1, to=8, textvariable=self.var_conc, width=6).grid(row=5, column=1, sticky="w")

        self.var_txt = tk.BooleanVar(value=bool(self._settings.get("youtube_transcript_also_save_txt")))
        ttk.Checkbutton(f, text="Also save plain .txt", variable=self.var_txt).grid(
            row=6, column=0, columnspan=2, sticky="w"
        )

        self.var_out = tk.StringVar(
            value=str(self._settings.get("youtube_transcript_export_dir") or "youtube_captions_exports")
        )
        ttk.Label(f, text="Export folder (relative to repo or absolute)").grid(
            row=7, column=0, sticky="w", pady=(10, 0)
        )
        fr = ttk.Frame(f)
        fr.grid(row=8, column=0, columnspan=2, sticky="ew")
        ttk.Entry(fr, textvariable=self.var_out, width=55).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(fr, text="Browse…", command=self._browse).pack(side=tk.LEFT, padx=6)

        bf = ttk.Frame(f)
        bf.grid(row=9, column=0, columnspan=2, pady=14)
        ttk.Button(bf, text="Get YouTube Transcript — Playlist", command=self._start_playlist).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(bf, text="Get YouTube Transcript — Single", command=self._start_single).pack(
            side=tk.LEFT, padx=4
        )

        self.var_prog = tk.StringVar(value="—")
        ttk.Label(f, textvariable=self.var_prog, font=("Segoe UI", 10, "bold")).grid(
            row=10, column=0, columnspan=2, sticky="w"
        )

        self.log = scrolledtext.ScrolledText(f, height=16, bg="#0d1117", fg=FG, insertbackground=FG)
        self.log.grid(row=11, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        f.rowconfigure(11, weight=1)
        f.columnconfigure(0, weight=1)

        self.after(200, self._drain_queue)

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=str(ROOT))
        if d:
            self.var_out.set(d)

    def _log(self, msg: str) -> None:
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def _sync_settings_from_ui(self) -> None:
        self._settings["youtube_transcript_lang"] = self.var_lang.get().strip() or "en"
        self._settings["youtube_transcript_concurrency"] = max(1, min(8, int(self.var_conc.get() or 2)))
        self._settings["youtube_transcript_also_save_txt"] = bool(self.var_txt.get())
        self._settings["youtube_transcript_export_dir"] = (
            self.var_out.get().strip() or "youtube_captions_exports"
        )
        save_settings(
            ROOT,
            {
                "youtube_transcript_lang": self._settings["youtube_transcript_lang"],
                "youtube_transcript_concurrency": self._settings["youtube_transcript_concurrency"],
                "youtube_transcript_also_save_txt": self._settings["youtube_transcript_also_save_txt"],
                "youtube_transcript_export_dir": self._settings["youtube_transcript_export_dir"],
            },
        )

    def _start_playlist(self) -> None:
        if self._busy:
            return
        ref = self.var_playlist.get().strip()
        if not ref:
            messagebox.showwarning("Playlist", "Enter a playlist URL or ID.")
            return
        self._sync_settings_from_ui()
        self._settings = load_settings(ROOT)
        self._busy = True
        self.var_prog.set("0 of …")
        threading.Thread(target=self._worker_playlist, args=(ref,), daemon=True).start()

    def _start_single(self) -> None:
        if self._busy:
            return
        ref = self.var_video.get().strip()
        if not ref:
            messagebox.showwarning("Video", "Enter a video URL or ID.")
            return
        self._sync_settings_from_ui()
        self._settings = load_settings(ROOT)
        self._busy = True
        self.var_prog.set("0 of 1")
        threading.Thread(target=self._worker_single, args=(ref,), daemon=True).start()

    def _worker_playlist(self, ref: str) -> None:
        try:

            def log(m: str) -> None:
                self._q.put(("log", m))

            def prog(cur: int, tot: int, _x: str) -> None:
                self._q.put(("prog", cur, tot))

            s = run_playlist_export(ROOT, self._settings, ref, log, prog)
            self._q.put(("done", s))
        except Exception as e:
            self._q.put(("err", str(e)))

    def _worker_single(self, ref: str) -> None:
        try:

            def log(m: str) -> None:
                self._q.put(("log", m))

            def prog(cur: int, tot: int, _x: str) -> None:
                self._q.put(("prog", cur, tot))

            s = run_single_export(ROOT, self._settings, ref, log, prog)
            self._q.put(("done", s))
        except Exception as e:
            self._q.put(("err", str(e)))

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self._q.get_nowait()
                if item[0] == "log":
                    self._log(item[1])
                elif item[0] == "prog":
                    _, cur, tot = item
                    self.var_prog.set(f"{cur} of {tot}")
                elif item[0] == "err":
                    self._busy = False
                    self.var_prog.set("—")
                    messagebox.showerror("Export failed", item[1])
                elif item[0] == "done":
                    summary = item[1]
                    self._busy = False
                    self.var_prog.set("—")
                    dest = summary.dest
                    msg = f"Saved {summary.ok} file(s); {summary.failed} skipped/failed."
                    if dest:
                        msg += f"\n\nFolder:\n{dest}"
                    messagebox.showinfo("Complete", msg)
        except queue.Empty:
            pass
        self.after(200, self._drain_queue)


def main() -> None:
    CaptionExportApp().mainloop()


if __name__ == "__main__":
    main()
