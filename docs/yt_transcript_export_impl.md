# Implementation bundle (materialize with `python scripts/materialize_youtube_export_bundle.py`)

This file exists temporarily to materialize `youtube_transcript_export.py` when Plan mode blocks direct `.py` writes. Delete after merge or keep for reference.

<!-- FILE: youtube_transcript_export.py -->

```python
"""Export YouTube captions to WebVTT using Data API v3 + youtube-transcript-api."""

from __future__ import annotations

import os
import re
import time
import random
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable
from urllib.parse import parse_qs, urlparse

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int, str], None]

_VIDEO_ID_RE = re.compile(r"^[\w-]{11}$")
_PLAYLIST_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{10,}$")


def extract_video_id(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if _VIDEO_ID_RE.fullmatch(s):
        return s
    try:
        u = urlparse(s if "://" in s else "https://" + s)
    except Exception:
        return None
    host = (u.netloc or "").lower().replace("www.", "")
    path = (u.path or "").strip("/")
    q = parse_qs(u.query)
    if host == "youtu.be" and path:
        vid = path.split("/")[0]
        return vid if _VIDEO_ID_RE.fullmatch(vid) else None
    if "youtube.com" in host:
        if path.startswith("shorts/"):
            parts = path.split("/")
            vid = parts[1].split("?")[0] if len(parts) > 1 else ""
            return vid if vid and _VIDEO_ID_RE.fullmatch(vid) else None
        v = q.get("v", [None])[0]
        if v and _VIDEO_ID_RE.fullmatch(v):
            return v
    return None


def extract_playlist_id(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if _PLAYLIST_ID_RE.fullmatch(s) and len(s) >= 13:
        return s
    try:
        u = urlparse(s if "://" in s else "https://" + s)
    except Exception:
        return None
    q = parse_qs(u.query)
    lst = q.get("list", [None])[0]
    if lst and _PLAYLIST_ID_RE.fullmatch(lst):
        return lst
    return None


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "video"
    return name[:max_len].rstrip(" .")


def format_vtt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    whole = int(s)
    ms = int(round((s - whole) * 1000))
    if ms >= 1000:
        ms = 0
        whole += 1
        if whole >= 60:
            whole = 0
            m += 1
            if m >= 60:
                m = 0
                h += 1
    return f"{h:02d}:{m:02d}:{whole:02d}.{ms:03d}"


def escape_vtt_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").strip()


def segments_to_vtt(segments: list[dict], *, note: str | None = None) -> str:
    lines: list[str] = ["WEBVTT", ""]
    if note:
        lines.append(f"NOTE {escape_vtt_text(note)}")
        lines.append("")
    for seg in segments:
        start = float(seg["start"])
        dur = float(seg.get("duration") or 0)
        if dur <= 0:
            dur = 0.5
        end = start + dur
        body = escape_vtt_text(str(seg.get("text") or ""))
        if not body:
            continue
        lines.append(f"{format_vtt_timestamp(start)} --> {format_vtt_timestamp(end)}")
        for part in body.split("\n"):
            lines.append(part)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def resolve_api_key(settings: dict | None) -> str:
    k = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if k:
        return k
    if settings:
        sk = str(settings.get("youtube_api_key") or "").strip()
        if sk:
            return sk
    return ""


def _sleep_jitter() -> None:
    time.sleep(random.uniform(0.08, 0.22))


def _youtube_service(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def list_playlist_video_ids(api_key: str, playlist_id: str, log: LogFn) -> list[str]:
    yt = _youtube_service(api_key)
    out: list[str] = []
    page_token: str | None = None
    while True:
        resp = None
        for attempt in range(4):
            try:
                resp = (
                    yt.playlistItems()
                    .list(
                        part="contentDetails",
                        playlistId=playlist_id,
                        maxResults=50,
                        pageToken=page_token,
                    )
                    .execute()
                )
                break
            except HttpError as e:
                if e.resp.status in (403, 429) and attempt < 3:
                    wait_s = (2**attempt) + random.uniform(0, 0.5)
                    log(f"playlistItems retry in {wait_s:.1f}s …")
                    time.sleep(wait_s)
                else:
                    raise
        assert resp is not None
        for item in resp.get("items") or []:
            vid = (item.get("contentDetails") or {}).get("videoId")
            if vid:
                out.append(vid)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        _sleep_jitter()
    return out


def fetch_video_titles(api_key: str, video_ids: list[str], log: LogFn) -> dict[str, str]:
    if not video_ids:
        return {}
    yt = _youtube_service(api_key)
    titles: dict[str, str] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        resp = None
        for attempt in range(4):
            try:
                resp = yt.videos().list(part="snippet", id=",".join(chunk)).execute()
                break
            except HttpError as e:
                if e.resp.status in (403, 429) and attempt < 3:
                    wait_s = (2**attempt) + random.uniform(0, 0.5)
                    log(f"videos.list retry in {wait_s:.1f}s …")
                    time.sleep(wait_s)
                else:
                    raise
        assert resp is not None
        for item in resp.get("items") or []:
            vid = item.get("id")
            sn = item.get("snippet") or {}
            title = str(sn.get("title") or vid or "video")
            if vid:
                titles[vid] = title
        _sleep_jitter()
    return titles


def fetch_transcript_segments(video_id: str, lang: str) -> list[dict]:
    langs = [lang.strip()] if (lang or "").strip() else ["en"]
    if "en" not in langs:
        langs.append("en")
    return YouTubeTranscriptApi.get_transcript(video_id, languages=langs)


def _try_transcript(video_id: str, lang: str) -> list[dict]:
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return fetch_transcript_segments(video_id, lang)
        except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable):
            raise
        except Exception as e:
            last_err = e
            time.sleep((2**attempt) * 0.4 + random.uniform(0, 0.3))
    if last_err:
        raise last_err
    return []


def write_export_for_video(
    video_id: str,
    title: str,
    dest_dir: Path,
    lang: str,
    also_txt: bool,
    log: LogFn,
) -> bool:
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = sanitize_filename(f"{title}_{video_id}")
    vtt_path = dest_dir / f"{base}.vtt"
    try:
        segments = _try_transcript(video_id, lang)
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as e:
        log(f"skip {video_id}: {e}")
        return False
    except Exception as e:
        log(f"skip {video_id}: {type(e).__name__}: {e}")
        return False
    note = f"{title} | {video_id}"
    vtt_path.write_text(segments_to_vtt(segments, note=note), encoding="utf-8")
    if also_txt:
        plain = " ".join(str(s.get("text") or "").strip() for s in segments if s.get("text"))
        (dest_dir / f"{base}.txt").write_text(plain, encoding="utf-8")
    log(f"ok {video_id} -> {vtt_path.name}")
    return True


@dataclass
class ExportSummary:
    ok: int = 0
    failed: int = 0
    dest: Path | None = None


@dataclass
class ExportJob:
    video_ids: list[str]
    titles: dict[str, str]
    dest_dir: Path
    lang: str
    also_txt: bool
    concurrency: int


def export_videos_parallel(
    job: ExportJob,
    progress: ProgressFn,
    log: LogFn,
) -> ExportSummary:
    total = len(job.video_ids)
    summary = ExportSummary(dest=job.dest_dir)
    if total == 0:
        return summary
    lock = Lock()

    def one(vid: str) -> tuple[str, bool]:
        title = job.titles.get(vid, vid)
        _sleep_jitter()
        ok = write_export_for_video(vid, title, job.dest_dir, job.lang, job.also_txt, log)
        return vid, ok

    concurrency = max(1, min(job.concurrency, 8, total))
    done_count = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(one, vid): vid for vid in job.video_ids}
        pending = set(futures.keys())
        while pending:
            done_set, pending = wait(pending, return_when=FIRST_COMPLETED)
            for f in done_set:
                try:
                    _, ok = f.result()
                    if ok:
                        summary.ok += 1
                    else:
                        summary.failed += 1
                except Exception as e:
                    summary.failed += 1
                    log(f"worker error: {e}")
                with lock:
                    done_count += 1
                    progress(done_count, total, "")
    return summary


def run_single_export(
    repo_root: Path,
    settings: dict,
    video_ref: str,
    log: LogFn,
    progress: ProgressFn,
) -> ExportSummary:
    api_key = resolve_api_key(settings)
    if not api_key:
        raise ValueError("Set YOUTUBE_API_KEY or youtube_api_key in watcher_settings.json")
    vid = extract_video_id(video_ref)
    if not vid:
        raise ValueError("Could not parse video URL or ID")
    dest_parent = resolved_export_dir(repo_root, settings)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest_dir = dest_parent / f"single_{vid}_{stamp}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    lang = str(settings.get("youtube_transcript_lang") or "en").strip() or "en"
    also_txt = bool(settings.get("youtube_transcript_also_save_txt"))
    conc = int(settings.get("youtube_transcript_concurrency") or 2)
    titles = fetch_video_titles(api_key, [vid], log)
    job = ExportJob([vid], titles, dest_dir, lang, also_txt, max(1, conc))
    progress(0, 1, "start")
    s = export_videos_parallel(job, progress, log)
    progress(1, 1, "done")
    s.dest = dest_dir
    return s


def run_playlist_export(
    repo_root: Path,
    settings: dict,
    playlist_ref: str,
    log: LogFn,
    progress: ProgressFn,
) -> ExportSummary:
    api_key = resolve_api_key(settings)
    if not api_key:
        raise ValueError("Set YOUTUBE_API_KEY or youtube_api_key in watcher_settings.json")
    pl = extract_playlist_id(playlist_ref)
    if not pl:
        raise ValueError("Could not parse playlist URL or ID")
    log(f"Listing playlist {pl} …")
    ids = list_playlist_video_ids(api_key, pl, log)
    if not ids:
        log("No videos in playlist (or playlist private / invalid).")
        return ExportSummary(dest=None)
    log(f"Fetching titles for {len(ids)} videos …")
    titles = fetch_video_titles(api_key, ids, log)
    dest_parent = resolved_export_dir(repo_root, settings)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest_dir = dest_parent / f"playlist_{pl}_{stamp}"
    lang = str(settings.get("youtube_transcript_lang") or "en").strip() or "en"
    also_txt = bool(settings.get("youtube_transcript_also_save_txt"))
    conc = int(settings.get("youtube_transcript_concurrency") or 2)
    job = ExportJob(ids, titles, dest_dir, lang, also_txt, max(1, conc))
    progress(0, len(ids), "start")
    s = export_videos_parallel(job, progress, log)
    s.dest = job.dest_dir
    return s


def resolved_export_dir(repo_root: Path, settings: dict) -> Path:
    raw = str(settings.get("youtube_transcript_export_dir") or "").strip()
    name = raw or "youtube_captions_exports"
    p = Path(name)
    out = p.resolve() if p.is_absolute() else (repo_root / p).resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out
```

<!-- FILE: scripts/patch_youtube_export_settings.py -->

```python
"""One-off: extend watcher_settings defaults and requirements.txt for YouTube caption export."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ws = ROOT / "watcher_settings.py"
    t = ws.read_text(encoding="utf-8")
    if "youtube_transcript_export_dir" in t:
        print("watcher_settings.py already extended; skipping DEFAULT_SETTINGS edit")
    else:
        _patch_watcher_defaults(ws, t)
    _patch_requirements(ROOT)


def _patch_watcher_defaults(ws: Path, t: str) -> None:
    needle_lf = '    "xai_api_key": "",\n}'
    needle_crlf = '    "xai_api_key": "",\r\n}'
    repl_lf = (
        '    "xai_api_key": "",\n'
        '    "youtube_api_key": "",\n'
        '    "youtube_transcript_export_dir": "youtube_captions_exports",\n'
        '    "youtube_transcript_lang": "en",\n'
        '    "youtube_transcript_concurrency": 2,\n'
        '    "youtube_transcript_also_save_txt": False,\n'
        "}"
    )
    repl_crlf = repl_lf.replace("\n", "\r\n")
    if needle_crlf in t:
        t = t.replace(needle_crlf, repl_crlf, 1)
    elif needle_lf in t:
        t = t.replace(needle_lf, repl_lf, 1)
    else:
        raise SystemExit("watcher_settings.py pattern not found; already patched?")
    ws.write_text(t, encoding="utf-8")
    print("Patched watcher_settings.py DEFAULT_SETTINGS")


def _patch_requirements(ROOT: Path) -> None:
    req = ROOT / "requirements.txt"
    r = req.read_text(encoding="utf-8").strip() + "\n"
    for pkg in ("google-api-python-client", "youtube-transcript-api"):
        if pkg not in r:
            r += pkg + "\n"
    req.write_text(r, encoding="utf-8")
    print("Patched requirements.txt")


if __name__ == "__main__":
    main()
```

<!-- FILE: scripts/materialize_youtube_export_bundle.py -->

```python
"""Materialize python files from the yt_transcript_export_impl bundle (FILE markers + fenced code)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "yt_transcript_export_impl.md"


def main() -> None:
    text = MD.read_text(encoding="utf-8")
    tick = chr(96)
    fence_py = tick * 3 + "python"
    fence_end = tick * 3
    pat = re.compile(
        r"<!-- FILE: ([^\n]+) -->\s*" + fence_py + r"\r?\n(.*?)" + fence_end,
        re.DOTALL,
    )
    for m in pat.finditer(text):
        rel = m.group(1).strip().replace("/", Path.sep)
        out = ROOT / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        body = m.group(2).rstrip() + "\n"
        out.write_text(body, encoding="utf-8")
        print("wrote", out.relative_to(ROOT), file=sys.stderr)
    print("done", file=sys.stderr)


if __name__ == "__main__":
    main()
```

<!-- FILE: youtube_transcript_panel.py -->

```python
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
        self.var_prog.set("0 of ?")
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
```

<!-- FILE: scripts/yt_export_transcripts.py -->

```python
"""CLI: export YouTube captions to WebVTT."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from watcher_settings import load_settings  # noqa: E402
from youtube_transcript_export import run_playlist_export, run_single_export  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Export YouTube captions as WebVTT.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--playlist", metavar="URL_OR_ID", help="Playlist URL or ID")
    g.add_argument("--video", metavar="URL_OR_ID", help="Single video URL or ID")
    args = p.parse_args()
    settings = load_settings(ROOT)

    def log(m: str) -> None:
        print(m, flush=True)

    def prog(cur: int, tot: int, _x: str) -> None:
        print(f"\rprogress {cur}/{tot}", end="", flush=True)

    if args.playlist:
        s = run_playlist_export(ROOT, settings, args.playlist, log, prog)
    else:
        s = run_single_export(ROOT, settings, args.video, log, prog)
    print(f"\nDone: ok={s.ok} failed={s.failed} dest={s.dest}")


if __name__ == "__main__":
    main()
```

<!-- FILE: tests/test_youtube_transcript_export.py -->

```python
import unittest

from youtube_transcript_export import (
    escape_vtt_text,
    extract_playlist_id,
    extract_video_id,
    sanitize_filename,
    segments_to_vtt,
)


class ParseTest(unittest.TestCase):
    def test_video_watch(self) -> None:
        self.assertEqual(
            extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_video_short(self) -> None:
        self.assertEqual(extract_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_playlist(self) -> None:
        u = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        self.assertEqual(extract_playlist_id(u), "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")


class VttTest(unittest.TestCase):
    def test_escape(self) -> None:
        self.assertEqual(escape_vtt_text("a & b < c"), "a &amp; b &lt; c")

    def test_segments_to_vtt(self) -> None:
        segs = [{"text": "Hi", "start": 1.0, "duration": 2.0}]
        v = segments_to_vtt(segs, note="t")
        self.assertIn("WEBVTT", v)
        self.assertIn("00:00:01.000 --> 00:00:03.000", v)
        self.assertIn("Hi", v)

    def test_sanitize(self) -> None:
        self.assertNotIn(":", sanitize_filename('a:b"c'))


if __name__ == "__main__":
    unittest.main()
```
