"""
Watch a folder for new video files, transcribe each with the same pipeline as the
YouTube script, then move the source video and outputs into a Completed folder
using matching basenames (e.g. Interview.mp4, Interview.txt, Interview.csv, Interview.json).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from watcher_settings import load_settings
from watcher_telemetry import TelemetryWriter

VIDEO_EXTENSIONS = {
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
}

# Partial / temp extensions to ignore while copies finish
IGNORE_SUFFIXES = (
    ".tmp",
    ".temp",
    ".part",
    ".crdownload",
    ".download",
    ".partial",
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class StatusTracker:
    def __init__(
        self,
        watch_dir: str,
        completed_dir: str,
        status_file: str,
        log_file: str,
        heartbeat_seconds: float,
    ) -> None:
        self.watch_dir = watch_dir
        self.completed_dir = completed_dir
        self.status_file = status_file
        self.log_file = log_file
        self.heartbeat_seconds = max(1.0, heartbeat_seconds)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = {
            "running": False,
            "pid": os.getpid(),
            "watch_dir": self.watch_dir,
            "completed_dir": self.completed_dir,
            "started_at": utc_now(),
            "last_heartbeat": None,
            "last_progress_at": None,
            "last_progress_file": None,
            "last_worker_pid": None,
            "last_segment_end": 0.0,
            "stalled": False,
            "stall_timeout_seconds": None,
            "status": "starting",
            "phase": "starting",
            "current_file": None,
            "queue_count": 0,
            "processed_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "last_error": None,
            "last_event": "initialized",
            "active_jobs": [],
            "max_concurrent_jobs": 1,
            "log_profile": "standard",
            "last_completed_video": None,
        }

    def _write_status(self) -> None:
        with open(self.status_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def _append_log(self, message: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"{ts} {message}\n")

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                self.state["last_heartbeat"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                )
                self._write_status()
            self._stop.wait(self.heartbeat_seconds)

    def start(self) -> None:
        with self._lock:
            self.state["running"] = True
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["last_event"] = "watcher_started"
            self._write_status()
        self._append_log("[watch] watcher started")
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def apply_runtime_flags(
        self, *, stall_timeout: float, max_jobs: int, log_profile: str
    ) -> None:
        with self._lock:
            self.state["stall_timeout_seconds"] = stall_timeout
            self.state["max_concurrent_jobs"] = max_jobs
            self.state["log_profile"] = log_profile
            self._write_status()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._lock:
            self.state["running"] = False
            self.state["status"] = "stopped"
            self.state["phase"] = "off"
            self.state["current_file"] = None
            self.state["last_worker_pid"] = None
            self.state["active_jobs"] = []
            self.state["last_event"] = "watcher_stopped"
            self.state["last_heartbeat"] = utc_now()
            self._write_status()
        self._append_log("[watch] watcher stopped")

    def mark_queued(self, src_path: str, queue_count: int) -> None:
        with self._lock:
            self.state["status"] = "queued"
            self.state["phase"] = "queued"
            self.state["current_file"] = src_path
            self.state["queue_count"] = queue_count
            self.state["last_event"] = f"queued:{src_path}"
            self._write_status()
        self._append_log(f"[watch] queued: {src_path}")

    def mark_processing(self, src_path: str) -> None:
        with self._lock:
            self.state["status"] = "processing"
            self.state["phase"] = "transcribing"
            self.state["current_file"] = src_path
            self.state["stalled"] = False
            self.state["last_event"] = f"processing:{src_path}"
            self._write_status()
        self._append_log(f"[watch] processing: {src_path}")

    def mark_progress(
        self,
        src_path: str,
        *,
        progress_file: str,
        worker_pid: int | None,
        last_progress_at: str | None,
        last_segment_end: float | int | None,
    ) -> None:
        with self._lock:
            self.state["status"] = "processing"
            self.state["phase"] = "transcribing"
            self.state["current_file"] = src_path
            self.state["last_progress_file"] = progress_file
            self.state["last_worker_pid"] = worker_pid
            self.state["last_progress_at"] = last_progress_at
            self.state["last_segment_end"] = float(last_segment_end or 0.0)
            self.state["stalled"] = False
            self.state["last_event"] = f"progress:{src_path}"
            self._sync_active_job_segment(src_path, worker_pid, last_segment_end)
            self._write_status()

    def _sync_active_job_segment(
        self,
        src_path: str,
        worker_pid: int | None,
        last_segment_end: float | int | None,
    ) -> None:
        jobs: list[dict] = []
        seg = float(last_segment_end or 0.0)
        found = False
        for j in self.state.get("active_jobs", []):
            if j.get("path") == src_path:
                nj = dict(j)
                nj["last_segment_end"] = seg
                nj["worker_pid"] = worker_pid
                nj["phase"] = "transcribing"
                jobs.append(nj)
                found = True
            else:
                jobs.append(dict(j))
        if not found:
            jobs.append(
                {
                    "path": src_path,
                    "worker_pid": worker_pid,
                    "phase": "transcribing",
                    "last_segment_end": seg,
                }
            )
        self.state["active_jobs"] = jobs

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.state["phase"] = phase
            self._write_status()

    def job_begin(self, path: str, worker_pid: int | None) -> None:
        with self._lock:
            jobs = [j for j in self.state.get("active_jobs", []) if j.get("path") != path]
            jobs.append(
                {
                    "path": path,
                    "worker_pid": worker_pid,
                    "phase": "transcribing",
                    "last_segment_end": 0.0,
                }
            )
            self.state["active_jobs"] = jobs
            self.state["last_worker_pid"] = worker_pid
            self.state["current_file"] = path
            self._write_status()

    def job_set_phase(self, path: str, phase: str) -> None:
        with self._lock:
            jobs = []
            for j in self.state.get("active_jobs", []):
                if j.get("path") == path:
                    nj = dict(j)
                    nj["phase"] = phase
                    jobs.append(nj)
                else:
                    jobs.append(dict(j))
            self.state["active_jobs"] = jobs
            self.state["phase"] = phase
            self._write_status()

    def job_end(self, path: str) -> None:
        with self._lock:
            self.state["active_jobs"] = [
                j for j in self.state.get("active_jobs", []) if j.get("path") != path
            ]
            if not self.state["active_jobs"]:
                self.state["current_file"] = None
                self.state["last_worker_pid"] = None
            self._write_status()

    def mark_completed(self, src_path: str, dest_path: str) -> None:
        with self._lock:
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["current_file"] = None
            self.state["processed_count"] += 1
            self.state["completed_count"] += 1
            self.state["queue_count"] = max(0, self.state["queue_count"] - 1)
            self.state["last_error"] = None
            self.state["last_worker_pid"] = None
            self.state["stalled"] = False
            self.state["last_event"] = f"completed:{dest_path}"
            self.state["last_completed_video"] = dest_path
            self._write_status()
        self._append_log(f"[watch] completed: {src_path} -> {dest_path}")

    def mark_failed(self, src_path: str, error: str) -> None:
        with self._lock:
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["current_file"] = None
            self.state["processed_count"] += 1
            self.state["failed_count"] += 1
            self.state["queue_count"] = max(0, self.state["queue_count"] - 1)
            self.state["last_error"] = error
            self.state["last_worker_pid"] = None
            self.state["last_event"] = f"failed:{src_path}"
            self._write_status()
        self._append_log(f"[watch] failed: {src_path} :: {error}")

    def mark_stalled(self, src_path: str, error: str) -> None:
        with self._lock:
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["current_file"] = None
            self.state["processed_count"] += 1
            self.state["failed_count"] += 1
            self.state["queue_count"] = max(0, self.state["queue_count"] - 1)
            self.state["last_error"] = error
            self.state["last_worker_pid"] = None
            self.state["stalled"] = True
            self.state["last_event"] = f"stalled:{src_path}"
            self._write_status()
        self._append_log(f"[watch] stalled: {src_path} :: {error}")

    def mark_skipped(self, src_path: str, reason: str) -> None:
        with self._lock:
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["current_file"] = None
            self.state["processed_count"] += 1
            self.state["skipped_count"] += 1
            self.state["queue_count"] = max(0, self.state["queue_count"] - 1)
            self.state["last_worker_pid"] = None
            self.state["last_event"] = f"skipped:{reason}:{src_path}"
            self._write_status()
        self._append_log(f"[watch] skipped: {src_path} ({reason})")


def safe_output_stem(path: str) -> str:
    stem = Path(path).stem
    stem = re.sub(r'[<>:"/\\|?*]', "_", stem)
    stem = stem.strip(" .")
    return stem or "video"


def is_video_path(path: str) -> bool:
    p = Path(path)
    lower = p.suffix.lower()
    if lower not in VIDEO_EXTENSIONS:
        return False
    name_lower = p.name.lower()
    for ign in IGNORE_SUFFIXES:
        if name_lower.endswith(ign):
            return False
    return True


def is_under_dir(path: str, parent: str) -> bool:
    try:
        path_r = Path(path).resolve()
        parent_r = Path(parent).resolve()
        return parent_r in path_r.parents or path_r == parent_r
    except OSError:
        return False


def wait_until_file_stable(
    path: str,
    *,
    stability_seconds: float = 1.5,
    poll_interval: float = 0.25,
    timeout_seconds: float = 600.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    last_size = -1
    stable_since = None
    while time.monotonic() < deadline:
        try:
            if not os.path.isfile(path):
                return False
            size = os.path.getsize(path)
        except OSError:
            return False
        now = time.monotonic()
        if size <= 0:
            stable_since = None
            last_size = size
            time.sleep(poll_interval)
            continue
        if size == last_size:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= stability_seconds:
                return True
        else:
            stable_since = None
        last_size = size
        time.sleep(poll_interval)
    return False


def allocate_completed_stem(completed_dir: str, base_stem: str, extension: str) -> str:
    """Return a stem such that no completed file of that stem exists yet."""
    ext = extension.lower()
    counter = 0
    while True:
        stem = base_stem if counter == 0 else f"{base_stem}_{counter}"
        video_path = os.path.join(completed_dir, stem + ext)
        if not os.path.exists(video_path):
            return stem
        counter += 1


async def transcribe_and_move(
    src_path: str,
    watch_dir: str,
    completed_dir: str,
    status: StatusTracker,
    stall_timeout_seconds: float,
) -> None:
    src_path = os.path.abspath(src_path)
    watch_dir = os.path.abspath(watch_dir)
    completed_dir = os.path.abspath(completed_dir)

    if not is_video_path(src_path):
        status.mark_skipped(src_path, "not_video")
        return
    if is_under_dir(src_path, completed_dir):
        status.mark_skipped(src_path, "already_completed")
        return

    status.set_phase("settling_file")
    if not wait_until_file_stable(src_path):
        print(f"[watch] Skipped (not stable or missing): {src_path}")
        status.set_phase("watching")
        status.mark_skipped(src_path, "not_stable")
        return

    path_obj = Path(src_path)
    base_guess = safe_output_stem(src_path)
    ext = path_obj.suffix
    output_stem = allocate_completed_stem(completed_dir, base_guess, ext)
    print(f"[watch] Transcribing {src_path!r} -> basename {output_stem!r}")
    status.set_phase("spawning_worker")
    status.mark_processing(src_path)
    status.state["stall_timeout_seconds"] = stall_timeout_seconds

    progress_file = os.path.join(
        watch_dir, f".watch_progress_{safe_output_stem(src_path)}_{os.getpid()}.json"
    )
    worker_script = os.path.join(os.path.dirname(__file__), "watch_transcribe_worker.py")
    process = subprocess.Popen(
        [
            os.sys.executable,
            worker_script,
            "--input",
            src_path,
            "--stem",
            output_stem,
            "--progress",
            progress_file,
        ],
        cwd=os.path.dirname(__file__),
        shell=False,
    )
    status.job_begin(src_path, process.pid)
    status.set_phase("transcribing")
    try:
        last_progress_monotonic = time.monotonic()
        while True:
            exit_code = process.poll()
            progress_data: dict = {}
            if os.path.isfile(progress_file):
                try:
                    with open(progress_file, "r", encoding="utf-8") as f:
                        progress_data = json.load(f)
                except Exception:
                    progress_data = {}

            if progress_data.get("last_progress_at"):
                last_progress_monotonic = time.monotonic()
                status.mark_progress(
                    src_path,
                    progress_file=progress_file,
                    worker_pid=process.pid,
                    last_progress_at=progress_data.get("last_progress_at"),
                    last_segment_end=progress_data.get("last_segment_end"),
                )

            if exit_code is not None:
                if exit_code != 0:
                    error = progress_data.get(
                        "error", f"transcription_worker_exit_{exit_code}"
                    )
                    print(
                        f"[watch] Failed transcription for {src_path!r}; "
                        f"worker exited with {exit_code}."
                    )
                    status.job_end(src_path)
                    status.mark_failed(src_path, error)
                    return
                break

            if time.monotonic() - last_progress_monotonic > stall_timeout_seconds:
                process.kill()
                process.wait(timeout=10)
                error = (
                    "transcription_stalled:"
                    f" no progress heartbeat for {int(stall_timeout_seconds)}s"
                )
                print(f"[watch] Stalled transcription for {src_path!r}; worker killed.")
                status.job_end(src_path)
                status.mark_stalled(src_path, error)
                return

            await asyncio.sleep(2.0)
    finally:
        if os.path.exists(progress_file):
            try:
                os.remove(progress_file)
            except OSError:
                pass

    status.job_set_phase(src_path, "moving_outputs")

    os.makedirs(completed_dir, exist_ok=True)
    dest_video = os.path.join(completed_dir, output_stem + ext)

    txt_tmp = os.path.join("generated_transcript_combined_texts", f"{output_stem}.txt")
    csv_tmp = os.path.join("generated_transcript_metadata_tables", f"{output_stem}.csv")
    json_tmp = os.path.join("generated_transcript_metadata_tables", f"{output_stem}.json")

    try:
        shutil.move(src_path, dest_video)
        if os.path.isfile(txt_tmp):
            shutil.move(txt_tmp, os.path.join(completed_dir, f"{output_stem}.txt"))
        if os.path.isfile(csv_tmp):
            shutil.move(csv_tmp, os.path.join(completed_dir, f"{output_stem}.csv"))
        if os.path.isfile(json_tmp):
            shutil.move(json_tmp, os.path.join(completed_dir, f"{output_stem}.json"))
    except OSError:
        traceback.print_exc()
        print(
            f"[watch] Transcript done but move failed for {src_path!r}. "
            "Check outputs in generated_* folders."
        )
        status.job_end(src_path)
        status.mark_failed(src_path, "move_failed")
        return

    print(f"[watch] Completed: {dest_video}")
    status.job_end(src_path)
    status.mark_completed(src_path, dest_video)


class VideoHandler(FileSystemEventHandler):
    def __init__(
        self,
        watch_dir: str,
        completed_dir: str,
        status: StatusTracker,
        stall_timeout_seconds: float,
        max_concurrent_jobs: int,
    ) -> None:
        self.watch_dir = os.path.abspath(watch_dir)
        self.completed_dir = os.path.abspath(completed_dir)
        self.status = status
        self.stall_timeout_seconds = stall_timeout_seconds
        self._semaphore = threading.Semaphore(max(1, int(max_concurrent_jobs)))
        self._lock = threading.Lock()
        self._queued: set[str] = set()
        self._inflight = 0
        self._inflight_lock = threading.Lock()

    def wait_until_idle(self, poll_seconds: float = 0.25) -> None:
        while True:
            with self._inflight_lock:
                busy = self._inflight > 0
            with self._lock:
                q = len(self._queued)
            if not busy and q == 0:
                return
            time.sleep(poll_seconds)

    def _schedule(self, src_path: str) -> None:
        if not is_video_path(src_path):
            return
        abs_path = os.path.abspath(src_path)
        if is_under_dir(abs_path, self.completed_dir):
            return
        with self._lock:
            if abs_path in self._queued:
                return
            self._queued.add(abs_path)
            self.status.mark_queued(abs_path, len(self._queued))

        def run() -> None:
            self._semaphore.acquire()
            with self._inflight_lock:
                self._inflight += 1
            try:
                asyncio.run(
                    transcribe_and_move(
                        abs_path,
                        self.watch_dir,
                        self.completed_dir,
                        self.status,
                        self.stall_timeout_seconds,
                    )
                )
            finally:
                with self._inflight_lock:
                    self._inflight -= 1
                self._semaphore.release()
                with self._lock:
                    self._queued.discard(abs_path)

        threading.Thread(target=run, daemon=True).start()

    def on_created(self, event):  # noqa: ANN001
        if event.is_directory:
            return
        self._schedule(event.src_path)

    def on_modified(self, event):  # noqa: ANN001
        if event.is_directory:
            return
        self._schedule(event.src_path)


def scan_existing(handler: VideoHandler, watch_dir: str, completed_dir: str) -> None:
    watch_dir = os.path.abspath(watch_dir)
    completed_dir = os.path.abspath(completed_dir)
    for root, _dirs, files in os.walk(watch_dir):
        if is_under_dir(root, completed_dir) or os.path.abspath(root) == completed_dir:
            continue
        for name in files:
            path = os.path.join(root, name)
            if is_video_path(path):
                handler._schedule(path)
    handler.wait_until_idle()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Watch a folder for videos, transcribe with Whisper, "
            "then move video + transcripts to Completed with matching names."
        )
    )
    parser.add_argument(
        "--watch",
        default=os.environ.get("TRANSCRIBE_WATCH_DIR", "watch_incoming"),
        help="Directory to watch (default: watch_incoming or TRANSCRIBE_WATCH_DIR).",
    )
    parser.add_argument(
        "--completed",
        default=None,
        help=(
            "Directory for finished video + transcripts "
            "(default: <watch>/Completed or TRANSCRIBE_COMPLETED_DIR)."
        ),
    )
    parser.add_argument(
        "--scan-existing",
        action="store_true",
        help="Process videos already in the watch folder before watching.",
    )
    parser.add_argument(
        "--status-file",
        default=None,
        help="Path to watcher status JSON (default: <watch>/watch_status.json).",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to watcher event log text file (default: <watch>/watch_events.log).",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=None,
        help="How often to refresh status file (overrides watcher_settings.json if set).",
    )
    parser.add_argument(
        "--stall-timeout-seconds",
        type=float,
        default=None,
        help="Kill a worker if it stops reporting progress for this long (overrides settings).",
    )
    parser.add_argument(
        "--max-concurrent-jobs",
        type=int,
        default=None,
        help="Parallel transcribe jobs (default from watcher_settings.json).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    settings = load_settings(repo_root)
    heartbeat_seconds = (
        float(args.heartbeat_seconds)
        if args.heartbeat_seconds is not None
        else float(settings["heartbeat_seconds"])
    )
    stall_timeout_seconds = (
        float(args.stall_timeout_seconds)
        if args.stall_timeout_seconds is not None
        else float(settings["stall_timeout_seconds"])
    )
    max_concurrent_jobs = (
        int(args.max_concurrent_jobs)
        if args.max_concurrent_jobs is not None
        else int(settings["max_concurrent_jobs"])
    )
    log_profile = str(settings.get("log_profile", "standard"))

    watch_dir = os.path.abspath(args.watch)
    completed_dir = (
        os.path.abspath(args.completed)
        if args.completed
        else os.environ.get("TRANSCRIBE_COMPLETED_DIR")
    )
    if not completed_dir:
        completed_dir = os.path.join(watch_dir, "Completed")
    else:
        completed_dir = os.path.abspath(completed_dir)

    os.makedirs(watch_dir, exist_ok=True)
    os.makedirs(completed_dir, exist_ok=True)
    status_file = os.path.abspath(args.status_file) if args.status_file else os.path.join(watch_dir, "watch_status.json")
    log_file = os.path.abspath(args.log_file) if args.log_file else os.path.join(watch_dir, "watch_events.log")
    status = StatusTracker(
        watch_dir=watch_dir,
        completed_dir=completed_dir,
        status_file=status_file,
        log_file=log_file,
        heartbeat_seconds=heartbeat_seconds,
    )
    status.start()
    status.apply_runtime_flags(
        stall_timeout=stall_timeout_seconds,
        max_jobs=max_concurrent_jobs,
        log_profile=log_profile,
    )

    resource_jsonl = Path(watch_dir) / "watch_resource.jsonl"

    def is_busy() -> bool:
        st = status.state
        return st.get("status") == "processing" or len(st.get("active_jobs", [])) > 0

    telemetry = TelemetryWriter(resource_jsonl, repo_root, is_busy, interval_seconds=5.0)
    telemetry.start()

    handler = VideoHandler(
        watch_dir,
        completed_dir,
        status,
        stall_timeout_seconds,
        max_concurrent_jobs,
    )
    if args.scan_existing:
        print(f"[watch] Scanning existing files in {watch_dir!r} ...")
        scan_existing(handler, watch_dir, completed_dir)

    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=True)
    observer.start()
    print(f"[watch] Watching {watch_dir!r} -> completed {completed_dir!r} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        observer.stop()
    finally:
        telemetry.stop()
        status.stop()
    observer.join(timeout=5)


if __name__ == "__main__":
    main()
