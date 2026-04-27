"""Export YouTube captions to WebVTT using Data API v3 + youtube-transcript-api."""

from __future__ import annotations

import os
import random
import re
import time
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


def _api_key_from_secret_manager(settings: dict) -> str:
    proj = str(settings.get("youtube_api_key_secret_project") or "").strip()
    sec = str(settings.get("youtube_api_key_secret_id") or "").strip()
    if not proj or not sec:
        return ""
    try:
        from google.cloud import secretmanager  # type: ignore[import-untyped]
    except ImportError:
        return ""
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{proj}/secrets/{sec}/versions/latest"
        resp = client.access_secret_version(request={"name": name})
        return resp.payload.data.decode("utf-8").strip()
    except Exception:
        return ""


def resolve_api_key(settings: dict | None) -> str:
    k = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if k:
        return k
    if settings:
        sm = _api_key_from_secret_manager(settings)
        if sm:
            return sm
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
    done_count = 0

    def one(vid: str) -> tuple[str, bool]:
        title = job.titles.get(vid, vid)
        _sleep_jitter()
        ok = write_export_for_video(vid, title, job.dest_dir, job.lang, job.also_txt, log)
        return vid, ok

    concurrency = max(1, min(job.concurrency, 8, total))
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
