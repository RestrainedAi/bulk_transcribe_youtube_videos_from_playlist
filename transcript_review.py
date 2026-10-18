"""Review flagged transcript segments: list them, play the audio there, and save corrections.

Corrections keep the original: the first edit of a segment stores its old text in
'original_text', and the very first save of a transcript copies the JSON to
<stem>.original.json. After every save the .txt, .csv, .srt/.vtt and the search index
are regenerated from the corrected JSON.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import transcript_search

LOW_LOGPROB = -1.0
HIGH_NO_SPEECH = 0.6
HIGH_COMPRESSION = 2.4


def computed_reasons(seg: dict) -> str:
    """Flags for older transcripts that were made before flags were stored."""
    why = []
    try:
        if float(seg.get("avg_logprob", 0) or 0) < LOW_LOGPROB:
            why.append(f"low avg_logprob {seg.get('avg_logprob')}")
        if float(seg.get("no_speech_prob", 0) or 0) > HIGH_NO_SPEECH:
            why.append(f"high no_speech {seg.get('no_speech_prob')}")
        if float(seg.get("compression_ratio", 0) or 0) > HIGH_COMPRESSION:
            why.append(f"high compression {seg.get('compression_ratio')}")
    except (TypeError, ValueError):
        pass
    return "; ".join(why)


def reason(seg: dict) -> str:
    stored = seg.get("flags")
    if isinstance(stored, list):
        stored = "; ".join(map(str, stored))
    return str(stored or "") or computed_reasons(seg)


def _load(json_path: Path) -> list[dict]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("segments", [])
    return [s for s in data if isinstance(s, dict)]


def list_transcripts(json_dir: str | Path) -> list[dict]:
    out = []
    for p in sorted(Path(json_dir).glob("*.json")):
        if p.name == "fingerprints.json" or p.name.endswith(".original.json"):
            continue
        try:
            segs = _load(p)
        except Exception:
            continue
        flagged = sum(1 for s in segs if reason(s))
        edited = sum(1 for s in segs if "original_text" in s)
        reviewed = sum(1 for s in segs if reason(s) and s.get("reviewed"))
        out.append({"stem": p.stem, "json_path": str(p), "segments": len(segs), "flagged": flagged,
                    "edited": edited, "reviewed": reviewed})
    return out


def flagged_items(json_path: str | Path, include_all_edited: bool = True) -> list[dict]:
    items = []
    for i, s in enumerate(_load(Path(json_path))):
        r = reason(s)
        if r or (include_all_edited and "original_text" in s):
            items.append({"index": i, "start": float(s.get("start", 0) or 0), "end": float(s.get("end", 0) or 0),
                          "text": str(s.get("text", "")).strip(), "original_text": s.get("original_text"),
                          "reason": r or "edited", "reviewed": bool(s.get("reviewed"))})
    return items


def render_readable_transcript(segments: list[dict]) -> str:
    """Same layout the transcriber writes: one paragraph per segment."""
    blocks = [" ".join(str(s.get("text", "")).split()).strip() for s in segments]
    return "\n\n".join(b for b in blocks if b)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _regenerate(stem: str, segs: list[dict], dirs: dict) -> None:
    tdir = Path(dirs["completed_transcripts"])
    txt = tdir / f"{stem}.txt"
    _atomic_write(txt, render_readable_transcript(segs))
    csv_dir = dirs.get("completed_csv")
    if csv_dir and (Path(csv_dir) / f"{stem}.csv").is_file():
        keys: list[str] = []
        for s in segs:
            keys += [k for k in s if k not in keys]
        cp = Path(csv_dir) / f"{stem}.csv"
        tmp = cp.with_suffix(".csv.tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(segs)
        os.replace(tmp, cp)
    transcript_search.write_subtitles(segs, tdir, stem)
    transcript_search.index_transcript(Path(dirs["completed_json"]) / f"{stem}.json", txt_path=txt)


def _apply(stem: str, dirs: dict, index: int, change) -> dict:
    jp = Path(dirs["completed_json"]) / f"{stem}.json"
    segs = _load(jp)
    if not 0 <= index < len(segs):
        raise IndexError(f"segment {index} out of range")
    orig_copy = jp.with_name(f"{stem}.original.json")
    if not orig_copy.exists():
        shutil.copy2(jp, orig_copy)
    change(segs[index])
    _atomic_write(jp, json.dumps(segs, indent=4, ensure_ascii=False))
    _regenerate(stem, segs, dirs)
    return segs[index]


def save_correction(stem: str, dirs: dict, index: int, new_text: str) -> dict:
    new_text = " ".join(str(new_text).split())
    if not new_text:
        raise ValueError("corrected text is empty")

    def change(s: dict) -> None:
        if "original_text" not in s:
            s["original_text"] = s.get("text", "")
        s["text"] = " " + new_text
        s["reviewed"] = True
        s["edited_at"] = datetime.now().isoformat(timespec="seconds")
    return _apply(stem, dirs, index, change)


def mark_reviewed(stem: str, dirs: dict, index: int, ok: bool = True) -> dict:
    return _apply(stem, dirs, index, lambda s: s.__setitem__("reviewed", bool(ok)))


def revert(stem: str, dirs: dict, index: int) -> dict:
    def change(s: dict) -> None:
        if "original_text" in s:
            s["text"] = s.pop("original_text")
            s.pop("edited_at", None)
        s["reviewed"] = False
    return _apply(stem, dirs, index, change)


def snippet_command(kind: str, exe: str, media: str, start: float, end: float, pad: float = 1.0) -> list[str]:
    s, e = max(0.0, start - pad), end + pad
    if kind == "vlc":
        # own instance + play-and-exit so VLC closes by itself when the snippet ends
        return [exe, "--no-one-instance", "--play-and-exit", f"--start-time={s:.2f}", f"--stop-time={e:.2f}", media]
    if kind == "mpv":
        return [exe, f"--start={s:.2f}", f"--end={e:.2f}", media]
    return transcript_search.player_command(kind, exe, media, s)


def find_media(stem: str, folders) -> Path | None:
    """Look in the search index first, then every known folder (any media extension)."""
    hit = transcript_search.lookup_video(stem)
    if hit:
        return hit
    found = transcript_search.find_video(stem, [f for f in folders if f and Path(f).is_dir()])
    return Path(found) if found else None


def play_snippet(media: str | Path, start: float, end: float) -> str:
    player = transcript_search.find_player()
    if not player:
        return transcript_search.open_media_at(media, start)
    proc = subprocess.Popen(snippet_command(player[0], player[1], str(media), start, end), close_fds=True)
    if player[0] == "vlc":
        # VLC on this PC ignores --play-and-exit with --stop-time, so close the copy we opened once the snippet is over.
        import threading
        t = threading.Timer(max(0.0, end - start) + 2 * 1.0 + 4.0, lambda: proc.poll() is None and proc.terminate())
        t.daemon = True
        t.start()
    return player[0]
