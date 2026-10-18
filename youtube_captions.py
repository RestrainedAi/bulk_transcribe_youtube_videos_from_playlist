"""'Use YouTube captions when available' for Transcription Workbench.

For a YouTube link, fetch the video's own captions (seconds, no GPU) instead of
downloading audio and running Whisper. Track order: manual captions first, then the
original-language auto captions (en-orig), then en. Machine-translated tracks
(tlang=..., or auto tracks for another language) are never used.

The caller falls back to the normal audio download + Whisper when this returns None:
no captions, the fetch failed (after rate-limit retries), or the coverage check
flags the captions.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

import coverage_check

SOURCE_MANUAL = "youtube_manual"
SOURCE_AUTO = "youtube_auto"
SOURCE_WHISPER = "whisper"
NOISE_LINES = {"[music]", "[applause]", "[laughter]", "\u266a", "\u266a\u266a"}
MIN_INTERVAL_S = 2.0  # between caption requests, so a long list doesn't hit HTTP 429
RETRIES_429 = 4

_last_request = [0.0]


def _is_translated(fmt_url: str) -> bool:
    return "tlang=" in (fmt_url or "")


def pick_track(info: dict, lang: str = "en") -> tuple[str, str, str] | None:
    """Return (source, lang_code, json3_url) or None. Manual > en-orig > en; never translated."""
    lang = (lang or "en").lower()
    for source, table in ((SOURCE_MANUAL, info.get("subtitles") or {}),
                          (SOURCE_AUTO, info.get("automatic_captions") or {})):
        if source == SOURCE_MANUAL:
            codes = [c for c in table if c.lower() == lang or c.lower().startswith(lang + "-")]
            codes = [c for c in codes if not c.lower().endswith("-orig")] + [c for c in codes if c.lower().endswith("-orig")]
            codes.sort(key=lambda c: c.lower() != lang)
        else:
            # Only the original-language ASR track; '<lang>' itself is accepted when it is not a translation.
            codes = [c for c in (f"{lang}-orig", lang) if c in table]
        for c in codes:
            for f in table.get(c) or []:
                if f.get("ext") == "json3" and f.get("url") and not _is_translated(f["url"]):
                    return source, c, f["url"]
    return None


def json3_to_segments(data: dict, source: str = SOURCE_AUTO) -> list[dict]:
    evs = [e for e in (data or {}).get("events", []) if "segs" in e and not e.get("aAppend")]
    out: list[dict] = []
    for i, e in enumerate(evs):
        text = re.sub(r"\s+", " ", "".join(s.get("utf8", "") for s in e["segs"])).strip()
        if not text or text.lower() in NOISE_LINES:
            continue
        start = e.get("tStartMs", 0) / 1000
        end = (e.get("tStartMs", 0) + e.get("dDurationMs", 0)) / 1000
        if i + 1 < len(evs):  # rolling auto-captions overlap; cut at the next line
            end = min(end, evs[i + 1].get("tStartMs", 0) / 1000)
        out.append({"start": round(start, 2), "end": round(max(end, start + 0.3), 2), "text": " " + text,
                    "source": source})
    return out


def _throttle(sleep=time.sleep, clock=time.monotonic) -> None:
    wait = MIN_INTERVAL_S - (clock() - _last_request[0])
    if wait > 0:
        sleep(wait)
    _last_request[0] = clock()


def _is_429(exc: BaseException) -> bool:
    return "429" in str(exc) or getattr(exc, "code", None) == 429 or getattr(exc, "status", None) == 429


def with_429_retry(fn: Callable[[], object], retries: int = RETRIES_429, sleep=time.sleep):
    delay = 5.0
    for attempt in range(retries + 1):
        _throttle(sleep)
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not _is_429(exc) or attempt == retries:
                raise
            sleep(delay)
            delay *= 2
    return None


def fetch(url: str, lang: str = "en", ydl_opts: dict | None = None) -> tuple[str, str, list[dict], dict] | None:
    """Return (source, lang_code, segments, info) or None when the video has no usable captions."""
    import yt_dlp

    opts = dict(ydl_opts or {})
    opts.update({"skip_download": True, "quiet": True, "no_warnings": True, "noplaylist": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = with_429_retry(lambda: ydl.extract_info(url, download=False))
        tr = pick_track(info or {}, lang)
        if not tr:
            return None
        source, code, curl = tr
        raw = with_429_retry(lambda: ydl.urlopen(curl).read().decode("utf-8"))
    segs = json3_to_segments(json.loads(raw), source)
    return (source, code, segs, info) if segs else None


def write_outputs(segs: list[dict], stem: str, dirs: dict, warnings: list[dict] | None = None) -> dict:
    """txt/srt/vtt in completed_transcripts, json (+csv) like a Whisper run. Returns the paths."""
    import transcript_search
    from transcript_review import render_readable_transcript

    tdir, jdir = Path(dirs["completed_transcripts"]), Path(dirs["completed_json"])
    tdir.mkdir(parents=True, exist_ok=True)
    jdir.mkdir(parents=True, exist_ok=True)
    rows = [dict(s, coverage_warnings=list(warnings or [])) for s in segs]
    txt = tdir / f"{stem}.txt"
    txt.write_text(render_readable_transcript(rows), encoding="utf-8")
    js = jdir / f"{stem}.json"
    js.write_text(json.dumps(rows, indent=4, ensure_ascii=False), encoding="utf-8")
    srt, vtt = transcript_search.write_subtitles(rows, tdir, stem)
    out = {"txt": txt, "json": js, "srt": srt, "vtt": vtt}
    csv_dir = dirs.get("completed_csv")
    if csv_dir:
        import csv
        Path(csv_dir).mkdir(parents=True, exist_ok=True)
        cp = Path(csv_dir) / f"{stem}.csv"
        with open(cp, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["start", "end", "text", "source"], extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        out["csv"] = cp
    return out


def safe_stem(title: str, video_id: str) -> str:
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title or "").strip(" .")[:90] or video_id
    return f"{t} [{video_id}]"


def try_captions(url: str, video_id: str, dirs: dict, *, lang: str = "en", max_block: float = 30.0,
                 max_gap: float = 60.0, ydl_opts: dict | None = None, publish: bool = True,
                 log: Callable[[str], None] = print, fetcher=fetch) -> dict | None:
    """Captions path for one video. Returns {source, stem, paths, words, warnings} or None to fall back."""
    try:
        got = fetcher(url, lang, ydl_opts)
    except Exception as exc:  # noqa: BLE001
        log(f"CAPTIONS {video_id}: fetch failed ({type(exc).__name__}: {exc}); using Whisper")
        return None
    if not got:
        log(f"CAPTIONS {video_id}: no usable captions; using Whisper")
        return None
    source, code, segs, info = got
    duration = (info or {}).get("duration")
    warnings = coverage_check.coverage_warnings(segs, duration, max_block, max_gap)
    if warnings:
        log(f"CAPTIONS {video_id}: {source}/{code} flagged by the coverage check "
            f"({'; '.join(coverage_check.summary_lines(warnings)[:3])}); using Whisper")
        return None
    stem = safe_stem((info or {}).get("title") or "", video_id)
    paths = write_outputs(segs, stem, dirs, warnings)
    if publish:
        try:
            import aeonimus_handoff
            aeonimus_handoff.publish_transcript(paths["txt"], paths["json"])
        except Exception as exc:  # noqa: BLE001  the shared library is optional
            log(f"CAPTIONS {video_id}: library copy skipped ({exc})")
    try:
        import transcript_search
        transcript_search.index_transcript(paths["json"], txt_path=paths["txt"])
    except Exception:  # noqa: BLE001
        pass
    words = sum(len(str(s["text"]).split()) for s in segs)
    log(f"CAPTIONS {video_id}: {source}/{code}, {len(segs)} lines, {words} words -> {paths['txt']}")
    return {"source": source, "lang": code, "stem": stem, "paths": paths, "words": words, "warnings": warnings}
