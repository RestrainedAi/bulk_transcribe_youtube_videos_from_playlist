"""Shared data folders in the Aeonimus Library (v1.4.0).

Program files stay in the repo; the videos and transcripts live in the shared
Aeonimus Library (the same folder the Comment Workbench uses, read from
%APPDATA%\\Aeonimus\\handoff.json "library"). Layout inside the library:

    transcription\\incoming        watch folder (drop videos here)
    transcription\\needs-review    files that finished with no usable speech
    transcription\\transcripts     .txt   (completed_transcript_dir)
    transcription\\csv             .csv
    transcription\\json            .json
    transcription\\completed-videos  finished source videos
    transcription\\completed-audio
    transcripts\\                  the shared copy the Comment Workbench reads (unchanged)

No Tk here so it can be unit tested.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

TRANSCRIPTION = "transcription"
LAYOUT = {
    "watch_dir": ("transcription", "incoming"),
    "needs_review_dir": ("transcription", "needs-review"),
    "completed_transcript_dir": ("transcription", "transcripts"),
    "completed_csv_dir": ("transcription", "csv"),
    "completed_json_dir": ("transcription", "json"),
    "completed_video_dir": ("transcription", "completed-videos"),
    "completed_audio_dir": ("transcription", "completed-audio"),
}
# setting key -> legacy (in-program-folder) default
LEGACY_DEFAULTS = {
    "watch_dir": "watch_incoming",
    "completed_transcript_dir": "completed_transcripts",
    "completed_csv_dir": "completed_csv",
    "completed_json_dir": "completed_json",
    "completed_video_dir": "completed_videos",
    "completed_audio_dir": "completed_audio",
}
# watcher bookkeeping that must not be copied (belongs to a running watcher)
SKIP_NAMES = {".watcher.lock"}
SKIP_PREFIXES = (".watch_progress_",)
SIDE_CAR_SUFFIX = ".needs-review.txt"


# --------------------------------------------------------------------------- locations

def library_root(settings: dict[str, Any] | None = None) -> Path:
    """The shared library: the setting if set, else handoff.json (shared with Comment Workbench)."""
    raw = str((settings or {}).get("shared_library_dir") or "").strip()
    if raw:
        return Path(os.path.expandvars(raw))
    import aeonimus_handoff
    return aeonimus_handoff.load_config().library


def library_dirs(library: Path) -> dict[str, Path]:
    return {k: Path(library).joinpath(*parts) for k, parts in LAYOUT.items()}


def needs_review_dir(repo_root: Path, settings: dict[str, Any] | None = None) -> Path:
    s = settings or {}
    raw = str(s.get("needs_review_dir") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (Path(repo_root) / p)
    try:
        return library_dirs(library_root(s))["needs_review_dir"]
    except Exception:  # noqa: BLE001 - never block on the shared config
        return Path(repo_root) / "needs_review"


def set_library(path: str | Path) -> None:
    """Change the library for every Aeonimus app (writes handoff.json, keeps other keys)."""
    import aeonimus_handoff
    aeonimus_handoff.save_config(library=str(path))


# --------------------------------------------------------------------------- hashing / safe move

def sha256(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def unique_destination(folder: Path, name: str) -> Path:
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while True:
        cand = folder / f"{stem} ({n}){suffix}"
        if not cand.exists():
            return cand
        n += 1


def verified_copy(src: Path, dest: Path) -> str:
    """Copy src to dest (via .part), check SHA-256 of both; returns the hash. Raises on mismatch."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    want = sha256(src)
    tmp = dest.with_name(dest.name + ".part")
    shutil.copy2(src, tmp)
    got = sha256(tmp)
    if got != want:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise OSError(f"hash mismatch copying {src} -> {dest}")
    os.replace(tmp, dest)
    return want


def verified_move(src: Path, folder: Path) -> Path:
    """Copy, verify the hash, then remove the original. Never overwrites."""
    src = Path(src)
    dest = unique_destination(Path(folder), src.name)
    verified_copy(src, dest)
    src.unlink()
    return dest


# --------------------------------------------------------------------------- failure classes

# Content problems: the file is fine to keep but needs a person (or another audio mode).
CONTENT_FAILURES = {
    "transcription_returned_no_segments": (
        "No speech was found. The file may be silent, music only, or the voices are very quiet. "
        "Listen to it; if it is music or talking over music, re-run it with that audio mode."
    ),
    "no audio stream": (
        "The file has no audio track, so there is nothing to transcribe. Check that it is the right file."
    ),
    "invalid data found when processing input": (
        "The audio could not be read (the file may be damaged or an unsupported format)."
    ),
    "coverage_too_low": (
        "Only a small part of the file produced text. It is probably music or noisy; try another audio mode."
    ),
}


def classify_error(error: str) -> str:
    """'content' (needs review), 'crash' (engine/worker problem) or 'other'."""
    e = (error or "").lower()
    if any(k in e for k in CONTENT_FAILURES):
        return "content"
    crash_markers = ("worker_exit", "stalled", "all_whisper_attempts_failed", "cuda", "memoryerror",
                     "runtimeerror: cublas", "access violation", "move_failed", "outputs_missing")
    if any(k in e for k in crash_markers):
        return "crash"
    return "other"


def plain_reason(error: str) -> str:
    e = (error or "").lower()
    for key, text in CONTENT_FAILURES.items():
        if key in e:
            return text
    return f"The transcription did not finish: {error}"


def move_to_needs_review(src: str | Path, error: str, folder: Path, *, audio_mode: str = "speech") -> Path:
    """Move a content-failure file into needs-review with a plain-language sidecar .txt."""
    dest = verified_move(Path(src), Path(folder))
    sidecar = dest.with_name(dest.name + SIDE_CAR_SUFFIX)
    sidecar.write_text(
        "Why this file is here\n"
        "=====================\n"
        f"{plain_reason(error)}\n\n"
        f"File:        {dest.name}\n"
        f"Came from:   {src}\n"
        f"Moved at:    {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Audio mode:  {audio_mode}\n"
        f"Error code:  {error}\n\n"
        "What to do\n"
        "----------\n"
        "Listen to it. Then, in the Workbench, open Failed / needs-review, pick the audio mode\n"
        "(Speech, Speech over music, or Music/lyrics) and click Re-run, or move it back into the\n"
        "incoming folder yourself. Delete it if it is not worth transcribing.\n",
        encoding="utf-8",
    )
    return dest


def review_items(folder: Path) -> list[Path]:
    """Media files waiting in needs-review (sidecars excluded)."""
    try:
        return sorted(p for p in Path(folder).iterdir()
                      if p.is_file() and not p.name.endswith(SIDE_CAR_SUFFIX) and not p.name.endswith(".part"))
    except OSError:
        return []


# --------------------------------------------------------------------------- plain-language status (item 7)

@dataclass(frozen=True)
class StatusBadge:
    text: str
    level: str  # ok | busy | review | crash | off
    tooltip: str
    action: str  # "" | "needs_review" | "log" | "failed"


def status_badge(*, running: bool, busy: bool, crash_failures: int, review_count: int,
                 other_failures: int = 0, stale: bool = False) -> StatusBadge:
    """Replaces the 'LOAD: FAULT' badge. Clears itself when the counts go back to 0."""
    if stale:
        return StatusBadge("Watcher stopped unexpectedly: see log", "crash",
                           "The status file says running but the watcher process is gone. Click to open the log.", "log")
    if crash_failures:
        n = crash_failures
        return StatusBadge(f"Engine crashed on {n} file{'s' if n != 1 else ''}: see log", "crash",
                           "The speech engine or worker failed (not a problem with the file). Click to open the log; "
                           "Re-run failed tries again.", "log")
    if review_count:
        n = review_count
        return StatusBadge(f"{n} file{'s' if n != 1 else ''} need{'s' if n == 1 else ''} review: no speech found", "review",
                           "These files finished without usable speech and were moved to needs-review. "
                           "Click to open the folder.", "needs_review")
    if other_failures:
        n = other_failures
        return StatusBadge(f"{n} file{'s' if n != 1 else ''} failed: see Failed list", "crash",
                           "Click to see the failed jobs.", "failed")
    if busy:
        return StatusBadge("Working", "busy", "A file is being transcribed.", "")
    if running:
        return StatusBadge("All good", "ok", "Watching for new files. Nothing needs attention.", "")
    return StatusBadge("All good (watcher off)", "off", "Nothing needs attention. Start the watcher to process files.", "")


# --------------------------------------------------------------------------- migration

@dataclass
class MigrationItem:
    key: str
    src: Path
    dest: Path
    files: int = 0
    bytes: int = 0
    conflicts: int = 0


@dataclass
class MigrationPlan:
    library: Path
    items: list[MigrationItem] = field(default_factory=list)
    settings_updates: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _skip(p: Path) -> bool:
    return p.name in SKIP_NAMES or p.name.startswith(SKIP_PREFIXES) or p.name.endswith(".part")


def _iter_files(root: Path) -> Iterable[Path]:
    for dirpath, _dirs, files in os.walk(root):
        for n in files:
            p = Path(dirpath) / n
            if not _skip(p):
                yield p


def _resolve(repo_root: Path, raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else Path(repo_root) / p


def plan_migration(repo_root: Path, settings: dict[str, Any], library: Path | None = None,
                   *, include_videos: bool = True) -> MigrationPlan:
    library = Path(library) if library else library_root(settings)
    plan = MigrationPlan(library)
    targets = library_dirs(library)
    for key, legacy in LEGACY_DEFAULTS.items():
        if not include_videos and key in ("completed_video_dir", "completed_audio_dir"):
            plan.notes.append(f"{key}: left in place (--skip-videos)")
            continue
        raw = str(settings.get(key) or (settings.get("completed_dir") if key == "completed_video_dir" else "") or legacy)
        src = _resolve(repo_root, raw)
        dest = targets[key]
        if os.path.normcase(str(src.resolve())) == os.path.normcase(str(dest.resolve())):
            plan.notes.append(f"{key}: already at {dest}")
            continue
        item = MigrationItem(key, src, dest)
        if src.is_dir():
            # completed_videos may sit inside the watch folder; don't count it twice
            for f in _iter_files(src):
                if key == "watch_dir" and any(_inside(f, _resolve(repo_root, str(settings.get(k) or v)))
                                              for k, v in LEGACY_DEFAULTS.items() if k != "watch_dir"):
                    continue
                item.files += 1
                try:
                    item.bytes += f.stat().st_size
                except OSError:
                    pass
                if (dest / f.relative_to(src)).exists():
                    item.conflicts += 1
        else:
            plan.notes.append(f"{key}: source {src} does not exist (nothing to copy)")
        plan.items.append(item)
        plan.settings_updates[key] = str(dest)
    if "completed_video_dir" in plan.settings_updates:
        plan.settings_updates["completed_dir"] = plan.settings_updates["completed_video_dir"]
    plan.settings_updates["needs_review_dir"] = str(targets["needs_review_dir"])
    return plan


def _inside(p: Path, parent: Path) -> bool:
    try:
        Path(p).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def run_migration(repo_root: Path, plan: MigrationPlan, *, dry_run: bool = True, log=print) -> dict[str, int]:
    """COPY everything in the plan with SHA-256 checks; originals are never touched.

    Existing identical files are skipped; a different file with the same name is
    kept and the incoming copy gets a ' (2)' name. Settings are only changed when
    every file copied and verified.
    """
    from watcher_settings import save_settings, load_settings

    stats = {"copied": 0, "identical": 0, "renamed": 0, "failed": 0, "bytes": 0}
    log(f"{'DRY RUN - nothing will be written' if dry_run else 'MIGRATING'}: library = {plan.library}")
    for note in plan.notes:
        log(f"  note: {note}")
    for item in plan.items:
        log(f"  {item.key}: {item.src} -> {item.dest}  ({item.files} files, {item.bytes / 1e9:.2f} GB, "
            f"{item.conflicts} name clashes)")
        if dry_run or not item.src.is_dir():
            continue
        for f in _iter_files(item.src):
            if item.key == "watch_dir" and any(_inside(f, Path(v)) for k, v in
                                               ((i.key, i.src) for i in plan.items) if k != "watch_dir"):
                continue
            rel = f.relative_to(item.src)
            dest = item.dest / rel
            try:
                if dest.exists():
                    if dest.stat().st_size == f.stat().st_size and sha256(dest) == sha256(f):
                        stats["identical"] += 1
                        continue
                    dest = unique_destination(dest.parent, dest.name)
                    stats["renamed"] += 1
                verified_copy(f, dest)
                stats["copied"] += 1
                stats["bytes"] += f.stat().st_size
            except OSError as exc:
                stats["failed"] += 1
                log(f"    FAILED {f}: {exc}")
    for d in library_dirs(plan.library).values():
        if not dry_run:
            d.mkdir(parents=True, exist_ok=True)
    log(f"  settings to change: {plan.settings_updates}")
    if dry_run:
        log("DRY RUN done. Run again with --apply to copy (originals are kept).")
    elif stats["failed"]:
        log(f"{stats['failed']} file(s) failed; settings NOT changed. Originals untouched.")
    else:
        save_settings(Path(repo_root), plan.settings_updates)
        log(f"Copied {stats['copied']} file(s), {stats['identical']} already there, {stats['renamed']} renamed. "
            "Settings now point to the library. Originals untouched; delete them yourself when happy.")
        _ = load_settings(Path(repo_root))
    return stats


# --------------------------------------------------------------------------- v1.4.1 run light

@dataclass(frozen=True)
class RunLight:
    """The one big status light at the top of the dashboard (single source of truth)."""
    level: str   # running | idle | review | error
    title: str   # big words, readable from across the room
    detail: str  # one plain sentence underneath
    action: str = ""  # "" | "needs_review" | "log" | "start"


def run_light(*, running: bool, stale: bool = False, worker_gone: bool = False, busy: bool = False,
              current_file: str = "", done: int = 0, total: int = 0, review_count: int = 0,
              error: str = "") -> RunLight:
    """Decide the status light from one snapshot.

    Old failures never turn the light red while a run is going: they only count toward
    'needs review' once the run is over. Red is reserved for "the watcher itself stopped".
    """
    if stale:
        return RunLight("error", "STOPPED: the watcher quit unexpectedly",
                        "It stopped without finishing. Click Start watcher to carry on; "
                        "if it happens again, click here to open the log.", "log")
    if running and worker_gone:
        return RunLight("error", "STOPPED: the transcriber crashed",
                        "The watcher is on but the transcriber process is gone. Stop and start the watcher; "
                        "click here to open the log.", "log")
    if running and busy:
        name = Path(str(current_file)).name if current_file else "a file"
        count = f"  ({min(done + 1, total)} of {total})" if total else ""
        return RunLight("running", f"RUNNING{count}", f"Transcribing {name}")
    if not running and error:
        return RunLight("error", "STOPPED: an error ended the run",
                        f"{error} Fix that, then click Start watcher. Click here to open the log.", "log")
    if review_count:
        n = review_count
        return RunLight("review", f"DONE: {n} file{'s' if n != 1 else ''} need{'s' if n == 1 else ''} review",
                        "See the Needs review list below: re-run them or open the folder.", "needs_review")
    if running:
        return RunLight("idle", "IDLE: watching for new files", "Nothing to do right now. Drop files in the watch folder.")
    return RunLight("idle", "IDLE: watcher is off", "All done. Click Start watcher to process new files.", "start")


def split_previous_failures(jobs: list[dict], run_started_local: str) -> tuple[list[dict], list[dict]]:
    """(failures from earlier runs, failures from this run). Log 'at' and the start are 'YYYY-MM-DD HH:MM:SS' local."""
    if not run_started_local:
        return list(jobs), []
    prev, cur = [], []
    for j in jobs:
        (cur if str(j.get("at", "")) >= run_started_local else prev).append(j)
    return prev, cur


def utc_iso_to_local(iso_utc: object) -> str:
    """'2026-10-22T05:00:00Z' -> local 'YYYY-MM-DD HH:MM:SS' ('' if unreadable)."""
    from datetime import datetime, timezone
    s = str(iso_utc or "").strip()
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def files_finished_since(log_path: str | os.PathLike, run_started_local: str) -> int:
    """How many files finished (completed, failed, stalled or needs-review) in watch_events.log since the run began."""
    import re
    if not run_started_local:
        return 0
    pat = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[watch\] (completed|failed|stalled|needs_review): ")
    n = 0
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = pat.match(line)
                if m and m.group(1) >= run_started_local:
                    n += 1
    except OSError:
        return 0
    return n
