"""YouTube link intake for Transcription Workbench.

Takes one YouTube link (video, Shorts, live replay or playlist) or a CSV/TXT
file of links, downloads the audio only with yt-dlp, and drops each file into
the watch folder so the normal watcher transcribes it.

Files are named "<title> [<video id>].<ext>" so every transcript can be traced
back to its YouTube video.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qs, urlparse

MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mpeg", ".mpg",
    ".wmv", ".flv", ".mp3", ".m4a", ".wma", ".flac", ".ogg", ".wav",
}

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,80}$")
# Anything that looks like a YouTube address inside free text or a CSV cell.
URL_IN_TEXT_RE = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)(?:/[^\s\"'<>,;|]*)?",
    re.IGNORECASE,
)
MAX_PLAYLIST_ITEMS = 1000
MAX_FILE_BYTES = 50 * 1024 * 1024


class LinkError(ValueError):
    """A link we can't use, with a plain-language reason."""


@dataclass(frozen=True)
class YouTubeLink:
    kind: str  # "video" or "playlist"
    id: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    @property
    def url(self) -> str:
        if self.kind == "playlist":
            return f"https://www.youtube.com/playlist?list={self.id}"
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass
class IntakeResult:
    links: list[YouTubeLink] = field(default_factory=list)
    duplicates: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (raw text, reason)


def _is_youtube_host(host: str) -> bool:
    host = host.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in ("youtube.com", "youtu.be", "youtube-nocookie.com"))


def _video(vid: str) -> YouTubeLink:
    vid = (vid or "").strip()
    if not VIDEO_ID_RE.match(vid):
        raise LinkError("That YouTube link has a broken video ID.")
    return YouTubeLink("video", vid)


def _playlist(pid: str) -> YouTubeLink:
    pid = (pid or "").strip()
    if pid.upper().startswith("RD"):
        raise LinkError("That's a YouTube Mix, which YouTube builds on the fly and can't be downloaded as a list. Paste the video link instead.")
    if pid.upper() in ("WL", "LL", "LM"):
        raise LinkError("That's a private list (Watch Later or Liked), which needs your YouTube sign-in. Make a public or unlisted playlist instead.")
    if not PLAYLIST_ID_RE.match(pid):
        raise LinkError("That YouTube playlist link has a broken playlist ID.")
    return YouTubeLink("playlist", pid)


def parse_youtube_url(text: str) -> YouTubeLink:
    """Turn one pasted link into a YouTubeLink, or raise LinkError with a plain reason."""
    raw = (text or "").strip().strip("<>\"' ")
    if not raw:
        raise LinkError("Paste a YouTube link first.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise LinkError("That doesn't look like a web link.") from exc
    host = (parsed.hostname or "").lower()
    if not host or not _is_youtube_host(host):
        raise LinkError("That isn't a YouTube link.")
    parts = [p for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)

    if host == "youtu.be" or host.endswith(".youtu.be"):
        if not parts:
            raise LinkError("That short youtu.be link is missing the video part.")
        return _video(parts[0])

    first = parts[0].lower() if parts else ""
    if first in ("watch", "watch_videos") or (not parts and "v" in query):
        if query.get("v"):
            return _video(query["v"][0])
        if query.get("list"):
            return _playlist(query["list"][0])
        raise LinkError("That YouTube link is missing the video part.")
    if first in ("shorts", "live", "embed", "v", "e") and len(parts) >= 2:
        return _video(parts[1])
    if first == "playlist":
        if query.get("list"):
            return _playlist(query["list"][0])
        raise LinkError("That playlist link is missing the playlist ID.")
    if first in ("post", "community") or (first.startswith("@") and len(parts) >= 2 and parts[1].lower() in ("community", "posts")):
        raise LinkError("That's a community post. Posts have no audio to transcribe.")
    if first.startswith("@") or first in ("channel", "c", "user"):
        raise LinkError("That's a channel link. Use the URL Lister to export the channel's video links to a CSV, then load the CSV here.")
    if query.get("list"):
        return _playlist(query["list"][0])
    raise LinkError("I couldn't find a video in that YouTube link.")


def extract_links(text: str) -> IntakeResult:
    """Find every YouTube link in pasted text or a whole CSV/TXT file, in order, without repeats."""
    result = IntakeResult()
    seen: set[str] = set()
    for match in URL_IN_TEXT_RE.finditer(text or ""):
        raw = match.group(0).rstrip(".)]}")
        try:
            link = parse_youtube_url(raw)
        except LinkError as exc:
            result.skipped.append((raw, str(exc)))
            continue
        if link.key in seen:
            result.duplicates += 1
            continue
        seen.add(link.key)
        result.links.append(link)
    return result


def read_links_file(path: str | Path) -> IntakeResult:
    """Read a CSV or TXT file and pull YouTube links out of any column."""
    p = Path(path)
    if not p.is_file():
        raise LinkError("That file couldn't be found.")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise LinkError("That file is over 50 MB, which is too big for a list of links.")
    data = p.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return extract_links(text)


def describe_intake(result: IntakeResult, added: int, already_queued: int) -> str:
    """One plain sentence about what happened when links were added."""
    bits = []
    if added:
        bits.append(f"Added {added} link{'s' if added != 1 else ''} to download.")
    else:
        bits.append("Nothing new to add.")
    repeats = result.duplicates + already_queued
    if repeats:
        bits.append(f"{repeats} repeat{'s were' if repeats != 1 else ' was'} left out.")
    if result.skipped:
        n = len(result.skipped)
        bits.append(f"{n} link{'s' if n != 1 else ''} skipped (not a video or playlist).")
    return " ".join(bits)


def find_existing(video_id: str, folders: Iterable[Path]) -> Path | None:
    """Return a file whose name carries [video_id] in any of the folders, if one exists."""
    tag = f"[{video_id}]"
    for folder in folders:
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    if tag in entry.name and entry.is_file():
                        return Path(entry.path)
        except OSError:
            continue
    return None


def friendly_download_error(exc: BaseException) -> str:
    text = str(exc)
    low = text.lower()
    rules = [
        ("private video", "This video is private."),
        ("members-only", "This video is for channel members only."),
        ("join this channel", "This video is for channel members only."),
        ("confirm your age", "This video is age-restricted and needs a signed-in YouTube account."),
        ("not a bot", "YouTube asked for a sign-in to prove this isn't a bot. Wait a while and try again."),
        ("live event will begin", "This live stream hasn't started yet."),
        ("premieres in", "This video hasn't premiered yet."),
        ("is not available in your country", "This video is blocked in your country."),
        ("copyright", "This video was taken down for copyright."),
        ("video unavailable", "This video is unavailable. It may have been removed."),
        ("this video has been removed", "This video has been removed."),
        ("playlist does not exist", "That playlist doesn't exist or is private."),
        ("getaddrinfo", "Couldn't reach YouTube. Check the internet connection."),
        ("timed out", "YouTube took too long to answer. Check the internet connection and try again."),
        ("http error 429", "YouTube is limiting downloads right now. Wait a while and try again."),
        ("requested format is not available", "YouTube didn't offer a downloadable audio track for this video."),
        ("no space left", "The drive is full."),
    ]
    for needle, msg in rules:
        if needle in low:
            return msg
    first = text.replace("ERROR:", "").strip().splitlines()[0] if text.strip() else type(exc).__name__
    first = re.sub(r"^\[[^\]]+\]\s*[A-Za-z0-9_-]{11}:\s*", "", first)
    return f"Couldn't download this video: {first[:160]}"


class Cancelled(Exception):
    pass


def _ydl_base_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "windowsfilenames": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        # Node is already installed on this PC; yt-dlp uses it to read YouTube's player.
        "js_runtimes": {"deno": {}, "node": {}},
    }


def expand_playlist(link: YouTubeLink) -> list[YouTubeLink]:
    import yt_dlp

    opts = _ydl_base_opts()
    opts.update({"extract_flat": "in_playlist", "noplaylist": False, "playlistend": MAX_PLAYLIST_ITEMS})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(link.url, download=False)
    out: list[YouTubeLink] = []
    seen: set[str] = set()
    for entry in (info or {}).get("entries") or []:
        vid = (entry or {}).get("id") or ""
        if VIDEO_ID_RE.match(vid) and vid not in seen:
            seen.add(vid)
            out.append(YouTubeLink("video", vid))
    return out


def download_audio(
    link: YouTubeLink,
    dest_dir: Path,
    on_progress: Callable[[float | None, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Download one video's audio to a temp folder, then move it into dest_dir in one step."""
    import yt_dlp

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="tw_youtube_"))
    title_holder = {"title": ""}

    def hook(d: dict) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        if on_progress is None:
            return
        info = d.get("info_dict") or {}
        title_holder["title"] = info.get("title") or title_holder["title"]
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            on_progress((done / total * 100.0) if total else None, title_holder["title"])
        elif d.get("status") == "finished":
            on_progress(100.0, title_holder["title"])

    opts = _ydl_base_opts()
    opts.update({
        # Audio only: smallest useful file, and Whisper only needs sound.
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best[height<=360]",
        "outtmpl": str(tmp / "%(title).90B [%(id)s].%(ext)s"),
        "progress_hooks": [hook],
    })
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(link.url, download=True)
        files = [p for p in tmp.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS]
        if not files:
            raise RuntimeError("The download finished but no audio file was saved.")
        src = max(files, key=lambda p: p.stat().st_size)
        final = dest_dir / src.name
        if final.exists():
            return final
        staging = dest_dir / (src.name + ".part")  # the watcher ignores .part files
        shutil.copyfile(src, staging)
        os.replace(staging, final)
        _ = info
        return final
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class _Job:
    link: YouTubeLink
    dest_dir: Path
    known_dirs: tuple[Path, ...]


class YouTubeDownloader:
    """Background download queue. The UI polls snapshot(); nothing here touches Tk."""

    def __init__(self, log_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._jobs: list[_Job] = []
        self._keys: set[str] = set()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._log_path = log_path
        self._state = {
            "running": False, "total": 0, "done": 0, "already": 0, "failed": 0,
            "percent": None, "current": "", "message": "", "errors": [],
        }

    def _log(self, line: str) -> None:
        if not self._log_path:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        except OSError:
            pass

    def add(self, links: Iterable[YouTubeLink], dest_dir: Path, known_dirs: Iterable[Path]) -> tuple[int, int]:
        """Queue links. Returns (added, already_in_queue)."""
        added = repeats = 0
        known = tuple(Path(p) for p in known_dirs)
        with self._lock:
            if not self._state["running"]:
                self._state.update(total=0, done=0, already=0, failed=0, errors=[], percent=None)
            for link in links:
                if link.key in self._keys:
                    repeats += 1
                    continue
                self._keys.add(link.key)
                self._jobs.append(_Job(link, Path(dest_dir), known))
                self._state["total"] += 1
                added += 1
            if added and not self._state["running"]:
                self._cancel.clear()
                self._state["running"] = True
                self._state["message"] = "Starting downloads"
                self._thread = threading.Thread(target=self._run, name="youtube-intake", daemon=True)
                self._thread.start()
        return added, repeats

    def cancel(self) -> None:
        with self._lock:
            if not self._state["running"]:
                return
            self._cancel.set()
            self._state["message"] = "Stopping after the current step"

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._state)
            s["errors"] = list(self._state["errors"])
            s["pending"] = len(self._jobs)
            return s

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    def _fail(self, label: str, msg: str) -> None:
        with self._lock:
            self._state["failed"] += 1
            self._state["errors"].append(f"{label}: {msg}")
        self._log(f"FAILED {label}: {msg}")

    def _run(self) -> None:
        try:
            while True:
                if self._cancel.is_set():
                    with self._lock:
                        dropped = len(self._jobs)
                        for j in self._jobs:
                            self._keys.discard(j.link.key)
                        self._jobs.clear()
                        self._state["total"] -= dropped
                        self._state["message"] = "Stopped. Files already downloaded stay in the watch folder."
                    break
                with self._lock:
                    if not self._jobs:
                        break
                    job = self._jobs.pop(0)
                link = job.link
                if link.kind == "playlist":
                    self._set(current=f"Reading playlist {link.id}", percent=None, message="Reading playlist")
                    try:
                        videos = expand_playlist(link)
                    except Exception as exc:  # noqa: BLE001
                        self._fail(f"Playlist {link.id}", friendly_download_error(exc))
                        continue
                    with self._lock:
                        self._state["total"] -= 1  # the playlist itself is replaced by its videos
                        new = [v for v in videos if v.key not in self._keys]
                        for v in new:
                            self._keys.add(v.key)
                        self._jobs[0:0] = [_Job(v, job.dest_dir, job.known_dirs) for v in new]
                        self._state["total"] += len(new)
                    self._log(f"PLAYLIST {link.id}: {len(videos)} videos, {len(new)} new")
                    if not videos:
                        self._fail(f"Playlist {link.id}", "That playlist is empty or private.")
                    continue

                existing = find_existing(link.id, (job.dest_dir, *job.known_dirs))
                if existing is not None:
                    with self._lock:
                        self._state["already"] += 1
                    self._log(f"SKIP {link.id}: already have {existing.name}")
                    continue

                def progress(pct: float | None, title: str, _vid: str = link.id) -> None:
                    self._set(percent=pct, current=title or _vid)

                with self._lock:
                    n = self._state["done"] + self._state["already"] + self._state["failed"] + 1
                    self._state.update(current=link.id, percent=0.0, message=f"Downloading {n} of {self._state['total']}")
                try:
                    path = download_audio(link, job.dest_dir, progress, self._cancel)
                except Cancelled:
                    self._log(f"CANCELLED {link.id}")
                    continue
                except Exception as exc:  # noqa: BLE001
                    if self._cancel.is_set():
                        self._log(f"CANCELLED {link.id}")
                        continue
                    self._fail(link.id, friendly_download_error(exc))
                    continue
                with self._lock:
                    self._state["done"] += 1
                self._log(f"OK {link.id} -> {path}")
        finally:
            with self._lock:
                s = self._state
                if not self._cancel.is_set():
                    s["message"] = "Finished"
                s.update(running=False, percent=None, current="")
                self._keys = {j.link.key for j in self._jobs}


def summary_line(s: dict) -> str:
    """Plain-language one-liner for the UI."""
    parts = []
    if s.get("total"):
        parts.append(f"{s.get('done', 0)} of {s['total']} downloaded")
    if s.get("already"):
        parts.append(f"{s['already']} already here")
    if s.get("failed"):
        parts.append(f"{s['failed']} failed")
    return " | ".join(parts)


# ---------------------------------------------------------------- Tk panel

def build_panel(parent, downloader: YouTubeDownloader, *, theme: dict, get_dirs: Callable[[], tuple[Path, list[Path]]],
                compact: bool = False):
    """Build the 'Add YouTube links' card inside parent. Returns the outer frame (already packed by caller)."""
    import tkinter as tk
    from tkinter import filedialog, ttk

    bg = theme["card"]
    fg = theme["fg"]
    dim = theme["dim"]
    accent = theme["accent"]
    red = theme["red"]
    green = theme["green"]
    field_bg = theme["field"]
    border = theme["border"]
    head_font = theme["headline_font"]
    body_font = theme["body_font"]
    mono_font = theme["mono_font"]

    outer = tk.Frame(parent, bg=bg, highlightthickness=1, highlightbackground=border)
    inner = tk.Frame(outer, bg=bg)
    inner.pack(fill="x", padx=18, pady=10 if compact else 18)

    row = tk.Frame(inner, bg=bg)
    row.pack(fill="x")
    tk.Label(row, text="Add YouTube links", fg=accent, bg=bg, font=(head_font, 13 if compact else 16, "bold")).pack(side="left")
    url_var = tk.StringVar()
    entry = tk.Entry(row, textvariable=url_var, bg=field_bg, fg=fg, insertbackground=fg, relief="flat",
                     highlightthickness=1, highlightbackground=border, highlightcolor=accent, font=(body_font, 10))
    entry.pack(side="left", fill="x", expand=True, padx=(14, 8), ipady=5)
    status_var = tk.StringVar(value="Paste a video, Shorts or playlist link, or load a CSV/TXT of links. Audio lands in the watch folder.")
    status_lbl = tk.Label(inner, textvariable=status_var, fg=dim, bg=bg, font=(body_font, 9), anchor="w", justify="left", wraplength=900)
    progress_var = tk.StringVar(value="")
    bar = ttk.Progressbar(inner, orient="horizontal", mode="determinate", maximum=100)

    def set_status(text: str, color: str = dim) -> None:
        status_var.set(text)
        status_lbl.configure(fg=color)

    def queue(result: IntakeResult, single_error: str | None = None) -> None:
        if not result.links:
            if single_error:
                set_status(single_error, red)
            elif result.skipped:
                set_status(f"No video or playlist links found. {len(result.skipped)} skipped, for example: {result.skipped[0][1]}", red)
            else:
                set_status("No YouTube links found.", red)
            return
        dest, known = get_dirs()
        added, repeats = downloader.add(result.links, dest, known)
        set_status(describe_intake(result, added, repeats), green if added else dim)

    def add_pasted(_event=None) -> None:
        text = url_var.get().strip()
        if not text:
            set_status("Paste a YouTube link first.", red)
            return
        result = extract_links(text)
        single_error = None
        if not result.links:
            try:
                parse_youtube_url(text)
            except LinkError as exc:
                single_error = str(exc)
            if result.skipped and not single_error:
                single_error = result.skipped[0][1]
        queue(result, single_error)
        if result.links:
            url_var.set("")

    def load_file() -> None:
        path = filedialog.askopenfilename(
            parent=outer, title="Choose a CSV or TXT file of YouTube links",
            filetypes=[("Link lists", "*.csv *.txt *.tsv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            result = read_links_file(path)
        except (LinkError, OSError) as exc:
            set_status(str(exc) if isinstance(exc, LinkError) else "That file couldn't be opened.", red)
            return
        queue(result)

    btn_style = "Accent.TButton"
    ttk.Button(row, text="Add link", style=btn_style, command=add_pasted).pack(side="left")
    ttk.Button(row, text="Load CSV / TXT", command=load_file).pack(side="left", padx=(8, 0))
    cancel_btn = ttk.Button(row, text="Stop downloads", command=downloader.cancel)
    entry.bind("<Return>", add_pasted)

    prog_row = tk.Frame(inner, bg=bg)
    prog_lbl = tk.Label(prog_row, textvariable=progress_var, fg=fg, bg=bg, font=(mono_font, 9), anchor="w")
    status_lbl.pack(fill="x", pady=(6, 0))
    prog_row.pack(fill="x")

    state = {"shown": False, "last_err": 0}

    def poll() -> None:
        if not outer.winfo_exists():
            return
        s = downloader.snapshot()
        if s["running"]:
            if not state["shown"]:
                prog_lbl.pack(side="left", fill="x", expand=True, pady=(4, 0))
                bar.pack(in_=prog_row, side="left", fill="x", expand=True, padx=(10, 0), pady=(4, 0))
                cancel_btn.pack(side="left", padx=(8, 0))
                state["shown"] = True
            pct = s.get("percent")
            if pct is None:
                bar.configure(mode="indeterminate")
                bar.start(12)
            else:
                bar.stop()
                bar.configure(mode="determinate", value=pct)
            cur = (s.get("current") or "")[:70]
            line = f"{s.get('message', '')}: {cur}" if cur else s.get("message", "")
            if pct is not None:
                line += f"  {pct:.0f}%"
            progress_var.set(line)
        elif state["shown"]:
            bar.stop()
            bar.pack_forget()
            cancel_btn.pack_forget()
            state["shown"] = False
            done = summary_line(s)
            msg = s.get("message") or "Finished"
            progress_var.set(f"{msg}. {done}." if done else msg)
            if s.get("errors"):
                set_status("Problem: " + s["errors"][-1], red)
            elif s.get("done"):
                set_status("Downloads are in the watch folder. Start the watcher if it isn't running and they'll be transcribed.", green)
        if s.get("errors") and len(s["errors"]) != state["last_err"] and s["running"]:
            state["last_err"] = len(s["errors"])
            set_status("Problem: " + s["errors"][-1], red)
        outer.after(400, poll)

    outer.after(400, poll)
    return outer
