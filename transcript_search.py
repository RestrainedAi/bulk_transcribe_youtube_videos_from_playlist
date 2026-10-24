"""Subtitles (.srt/.vtt) and a full-text search index across all transcripts.

Uses only the Python standard library (sqlite3 with FTS5; SQLite is public domain).
The index lives in transcript_index.sqlite next to this file. Everything here is
safe to call repeatedly: re-indexing a transcript replaces its old rows.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "transcript_index.sqlite"
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus")


# ---------- subtitles ----------

def _ts(seconds: float, sep: str) -> str:
    ms = int(round(max(0.0, float(seconds)) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _cues(segments: Iterable[dict]) -> list[tuple[float, float, str]]:
    cues = []
    for seg in segments:
        text = _clean(seg.get("text"))
        if not text:
            continue
        if seg.get("speaker"):
            text = f"{_clean(seg.get('speaker'))}: {text}"  # v1.5.0 speaker labels
        start = float(seg.get("start", 0) or 0)
        end = max(float(seg.get("end", start) or start), start + 0.2)
        cues.append((start, end, text))
    return cues


def to_srt(segments: Iterable[dict]) -> str:
    out = []
    for i, (s, e, t) in enumerate(_cues(segments), 1):
        out.append(f"{i}\n{_ts(s, ',')} --> {_ts(e, ',')}\n{t}\n")
    return "\n".join(out)


def to_vtt(segments: Iterable[dict]) -> str:
    out = ["WEBVTT", ""]
    for s, e, t in _cues(segments):
        out.append(f"{_ts(s, '.')} --> {_ts(e, '.')}\n{t}\n")
    return "\n".join(out)


def write_subtitles(segments: list[dict], out_dir: str | Path, stem: str) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    srt, vtt = out_dir / f"{stem}.srt", out_dir / f"{stem}.vtt"
    srt.write_text(to_srt(segments), encoding="utf-8")
    vtt.write_text(to_vtt(segments), encoding="utf-8")
    return srt, vtt


def load_segments(json_path: str | Path) -> list[dict]:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("segments", [])
    return [s for s in data if isinstance(s, dict)]


# ---------- index ----------

def connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS transcripts (stem TEXT PRIMARY KEY, json_path TEXT, txt_path TEXT, "
        "video_path TEXT, segments INTEGER, duration REAL, indexed_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS segments USING fts5(text, stem UNINDEXED, start UNINDEXED, "
        "end UNINDEXED, tokenize='unicode61 remove_diacritics 2')"
    )
    return con


def index_transcript(json_path: str | Path, *, txt_path: str | Path | None = None, video_path: str | Path | None = None,
                     db_path: str | Path = DB_PATH) -> int:
    json_path = Path(json_path)
    stem = json_path.stem
    segs = _cues(load_segments(json_path))
    con = connect(db_path)
    try:
        with con:
            old = con.execute("SELECT video_path FROM transcripts WHERE stem=?", (stem,)).fetchone()
            vp = str(video_path) if video_path else (old[0] if old else None)
            con.execute("DELETE FROM segments WHERE stem=?", (stem,))
            con.executemany("INSERT INTO segments(text, stem, start, end) VALUES (?,?,?,?)",
                            [(t, stem, s, e) for s, e, t in segs])
            con.execute(
                "INSERT OR REPLACE INTO transcripts(stem, json_path, txt_path, video_path, segments, duration) VALUES (?,?,?,?,?,?)",
                (stem, str(json_path), str(txt_path) if txt_path else None, vp, len(segs), segs[-1][1] if segs else 0.0),
            )
    finally:
        con.close()
    return len(segs)


def lookup_video(stem: str, db_path: str | Path = DB_PATH) -> Path | None:
    """Video path recorded in the search index for this transcript, if it still exists."""
    try:
        con = connect(db_path)
        try:
            row = con.execute("SELECT video_path FROM transcripts WHERE stem=?", (stem,)).fetchone()
        finally:
            con.close()
    except Exception:
        return None
    if row and row[0] and Path(row[0]).is_file():
        return Path(row[0])
    return None


def set_video_path(stem: str, video_path: str | Path, db_path: str | Path = DB_PATH) -> None:
    con = connect(db_path)
    try:
        with con:
            con.execute("UPDATE transcripts SET video_path=? WHERE stem=?", (str(video_path), stem))
    finally:
        con.close()


def _fts_query(q: str) -> str:
    """Turn user input into a safe FTS5 query: quoted phrases kept, words AND-ed with prefix match."""
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]*"', " ", q)
    parts = ['"' + p.replace('"', "") + '"' for p in phrases if p.strip()]
    for w in re.findall(r"[\w']+", rest, flags=re.UNICODE):
        parts.append('"' + w.replace('"', "") + '"*')
    return " ".join(parts)


def search(query: str, limit: int = 200, db_path: str | Path = DB_PATH) -> list[dict]:
    fq = _fts_query(query)
    if not fq:
        return []
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT m.stem, m.start, m.end, m.ctx, t.video_path, t.json_path, t.txt_path FROM "
            "(SELECT stem, start, end, snippet(segments, 0, '[', ']', ' ... ', 18) AS ctx FROM segments WHERE segments MATCH ?) m "
            "LEFT JOIN transcripts t ON t.stem = m.stem ORDER BY m.stem, CAST(m.start AS REAL) LIMIT ?",
            (fq, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [dict(stem=r[0], start=float(r[1]), end=float(r[2]), context=r[3], video_path=r[4], json_path=r[5], txt_path=r[6])
            for r in rows]


def stats(db_path: str | Path = DB_PATH) -> tuple[int, int]:
    con = connect(db_path)
    try:
        return con.execute("SELECT COUNT(*), COALESCE(SUM(segments),0) FROM transcripts").fetchone()
    finally:
        con.close()


# ---------- locating and opening media ----------

def find_video(stem: str, search_dirs: Iterable[str | Path]) -> Path | None:
    for d in search_dirs:
        d = Path(d)
        for ext in VIDEO_EXTS:
            p = d / f"{stem}{ext}"
            if p.is_file():
                return p
    return None


def find_player() -> tuple[str, str] | None:
    candidates = [
        ("vlc", shutil.which("vlc") or r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
        ("vlc", r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"),
        ("mpc", r"C:\Program Files\MPC-HC\mpc-hc64.exe"),
        ("mpc", r"C:\Program Files (x86)\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe"),
        ("mpc", r"C:\Program Files\MPC-BE x64\mpc-be64.exe"),
        ("mpv", shutil.which("mpv") or ""),
    ]
    for kind, path in candidates:
        if path and os.path.isfile(path):
            return kind, path
    return None


def player_command(kind: str, exe: str, media: str, seconds: float) -> list[str]:
    s = max(0.0, float(seconds) - 1.0)  # start a second early so the phrase isn't clipped
    if kind == "vlc":
        return [exe, f"--start-time={s:.1f}", media]
    if kind == "mpc":
        return [exe, media, "/start", str(int(s * 1000))]
    return [exe, f"--start={s:.1f}", media]


def open_media_at(media: str | Path, seconds: float) -> str:
    """Open media at a time with VLC/MPC/mpv when available, else the default app. Returns what was used."""
    player = find_player()
    if player:
        subprocess.Popen(player_command(player[0], player[1], str(media), seconds), close_fds=True)
        return player[0]
    if sys.platform == "win32":
        os.startfile(str(media))  # noqa: S606
        return "default player (starts at 0:00)"
    subprocess.Popen(["xdg-open", str(media)])
    return "default player (starts at 0:00)"


# ---------- backfill ----------

def is_transcript_json(path: str | Path) -> bool:
    """Transcript JSONs only: not the duplicate registry or saved pre-edit originals."""
    name = Path(path).name
    return name.endswith(".json") and name != "fingerprints.json" and not name.endswith(".original.json")


def backfill(json_dir: str | Path, transcript_dir: str | Path, video_dirs: Iterable[str | Path],
             db_path: str | Path = DB_PATH) -> dict[str, int]:
    """Write .srt/.vtt for every JSON transcript and (re)index it."""
    done = subs = failed = 0
    vdirs = list(video_dirs)
    for jp in sorted(Path(json_dir).glob("*.json")):
        if not is_transcript_json(jp):
            continue
        try:
            segs = load_segments(jp)
            write_subtitles(segs, transcript_dir, jp.stem)
            subs += 1
            txt = Path(transcript_dir) / f"{jp.stem}.txt"
            index_transcript(jp, txt_path=txt if txt.is_file() else None, video_path=find_video(jp.stem, vdirs), db_path=db_path)
            done += 1
        except Exception as exc:  # one bad file must not stop the backfill
            failed += 1
            print(f"backfill skipped {jp.name}: {type(exc).__name__}: {exc}")
    return dict(indexed=done, subtitles=subs, failed=failed)


def after_transcript(stem: str, json_dir: str | Path, transcript_dir: str | Path) -> None:
    """Called once a transcript's JSON is final: write subtitles and update the index. Never raises."""
    try:
        jp = Path(json_dir) / f"{stem}.json"
        write_subtitles(load_segments(jp), transcript_dir, stem)
        txt = Path(transcript_dir) / f"{stem}.txt"
        index_transcript(jp, txt_path=txt if txt.is_file() else None)
    except Exception as exc:
        print(f"Subtitle/index update skipped for {stem}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    from output_paths import ensure_output_dirs
    from watcher_settings import load_settings
    dirs = ensure_output_dirs(ROOT, load_settings(ROOT))
    s = load_settings(ROOT)
    vdirs = [dirs["completed_videos"], ROOT / str(s.get("watch_dir") or "watch_incoming"), ROOT / "watch_incoming_staging"]
    print(backfill(dirs["completed_json"], dirs["completed_transcripts"], vdirs))
    print("index:", stats())
