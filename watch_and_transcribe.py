"""
Watch a folder for new media files, transcribe each with the same pipeline as the
YouTube script, then move the source media and outputs into repo-root completed_*
folders using matching basenames (e.g. Interview.mp4, Interview.txt, Interview.csv, Interview.json).
"""

from __future__ import annotations

import argparse
import asyncio
import glob
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

from output_paths import ensure_output_dirs
from safe_io import atomic_write_json
from watcher_settings import load_settings
from watcher_telemetry import TelemetryWriter

MEDIA_EXTENSIONS = {
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
    ".mp3",
    ".m4a",
    ".wma",
    ".flac",
    ".ogg",
    ".wav",
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


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


class WatcherLock:
    def __init__(self, lock_path: str) -> None:
        self.lock_path = Path(lock_path)
        self.pid_path = self.lock_path / "pid.txt"

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock_path.mkdir()
        except FileExistsError:
            try:
                stale_pid = int(self.pid_path.read_text(encoding="utf-8").strip())
            except Exception:
                stale_pid = 0
            if stale_pid and process_alive(stale_pid):
                return False
            try:
                if self.pid_path.exists():
                    self.pid_path.unlink()
                self.lock_path.rmdir()
                self.lock_path.mkdir()
            except OSError:
                return False
        try:
            self.pid_path.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except Exception:
            self.release()
            raise

    def release(self) -> None:
        try:
            if self.pid_path.exists():
                self.pid_path.unlink()
        except OSError:
            pass
        try:
            if self.lock_path.exists():
                self.lock_path.rmdir()
        except OSError:
            pass


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
            "pending_files": [],
            "health": None,
            "resumed_files": [],
        }

    def _sync_current_job_locked(self) -> None:
        jobs = self.state.get("active_jobs", []) or []
        if not jobs:
            self.state["current_file"] = None
            self.state["last_worker_pid"] = None
            return
        current = jobs[-1]
        self.state["current_file"] = current.get("path")
        self.state["last_worker_pid"] = current.get("worker_pid")

    def _write_status(self) -> None:
        atomic_write_json(self.status_file, self.state)

    def _append_log(self, message: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        log_path = Path(self.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
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
            self.state["pid"] = os.getpid()
            self.state["started_at"] = utc_now()
            self.state["running"] = True
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["current_file"] = None
            self.state["last_progress_at"] = None
            self.state["last_progress_file"] = None
            self.state["last_worker_pid"] = None
            self.state["last_segment_end"] = 0.0
            self.state["stalled"] = False
            self.state["last_error"] = None
            self.state["active_jobs"] = []
            self.state["last_completed_video"] = None
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
            self.state["pid"] = None
            self.state["status"] = "stopped"
            self.state["phase"] = "off"
            self.state["current_file"] = None
            self.state["last_progress_at"] = None
            self.state["last_progress_file"] = None
            self.state["last_worker_pid"] = None
            self.state["last_segment_end"] = 0.0
            self.state["active_jobs"] = []
            self.state["last_event"] = "watcher_stopped"
            self.state["last_heartbeat"] = None
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
            self._sync_current_job_locked()
            self._write_status()

    def job_update(self, path: str, **fields) -> None:
        with self._lock:
            jobs = []
            for j in self.state.get("active_jobs", []):
                nj = dict(j)
                if nj.get("path") == path:
                    nj.update(fields)
                jobs.append(nj)
            self.state["active_jobs"] = jobs
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
            self._sync_current_job_locked()
            self._write_status()

    def job_end(self, path: str) -> None:
        with self._lock:
            self.state["active_jobs"] = [
                j for j in self.state.get("active_jobs", []) if j.get("path") != path
            ]
            self._sync_current_job_locked()
            self._write_status()

    def mark_completed(self, src_path: str, dest_path: str) -> None:
        with self._lock:
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["processed_count"] += 1
            self.state["completed_count"] += 1
            self.state["queue_count"] = max(0, self.state["queue_count"] - 1)
            self.state["last_error"] = None
            self.state["stalled"] = False
            self.state["last_event"] = f"completed:{dest_path}"
            self.state["last_completed_video"] = dest_path
            self._sync_current_job_locked()
            self._write_status()
        self._append_log(f"[watch] completed: {src_path} -> {dest_path}")

    def mark_failed(self, src_path: str, error: str) -> None:
        with self._lock:
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["processed_count"] += 1
            self.state["failed_count"] += 1
            self.state["queue_count"] = max(0, self.state["queue_count"] - 1)
            self.state["last_error"] = error
            self.state["last_event"] = f"failed:{src_path}"
            self._sync_current_job_locked()
            self._write_status()
        self._append_log(f"[watch] failed: {src_path} :: {error}")

    def mark_stalled(self, src_path: str, error: str) -> None:
        with self._lock:
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["processed_count"] += 1
            self.state["failed_count"] += 1
            self.state["queue_count"] = max(0, self.state["queue_count"] - 1)
            self.state["last_error"] = error
            self.state["stalled"] = True
            self.state["last_event"] = f"stalled:{src_path}"
            self._sync_current_job_locked()
            self._write_status()
        self._append_log(f"[watch] stalled: {src_path} :: {error}")

    def mark_skipped(self, src_path: str, reason: str) -> None:
        with self._lock:
            self.state["status"] = "idle"
            self.state["phase"] = "watching"
            self.state["processed_count"] += 1
            self.state["skipped_count"] += 1
            self.state["queue_count"] = max(0, self.state["queue_count"] - 1)
            self.state["last_event"] = f"skipped:{reason}:{src_path}"
            self._sync_current_job_locked()
            self._write_status()
        self._append_log(f"[watch] skipped: {src_path} ({reason})")

    def mark_runtime_error(self, error: str) -> None:
        with self._lock:
            self.state["running"] = False
            self.state["pid"] = None
            self.state["status"] = "stopped"
            self.state["phase"] = "off"
            self.state["current_file"] = None
            self.state["last_progress_file"] = None
            self.state["last_worker_pid"] = None
            self.state["active_jobs"] = []
            self.state["last_error"] = error
            self.state["last_event"] = "watcher_runtime_error"
            self.state["last_heartbeat"] = None
            self._write_status()
        self._append_log(f"[watch] runtime error: {error}")


def safe_output_stem(path: str) -> str:
    stem = Path(path).stem
    stem = re.sub(r'[<>:"/\\|?*]', "_", stem)
    stem = stem.strip(" .")
    return stem or "video"


def is_media_path(path: str) -> bool:
    p = Path(path)
    lower = p.suffix.lower()
    if lower not in MEDIA_EXTENSIONS:
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


def allocate_completed_stem(
    output_dirs: dict[str, str],
    base_stem: str,
    extension: str,
) -> str:
    """Return a stem such that no destination output file of that stem exists yet."""
    ext = extension.lower()
    counter = 0
    while True:
        stem = base_stem if counter == 0 else f"{base_stem}_{counter}"
        candidates = [
            os.path.join(output_dirs["completed_videos"], stem + ext),
            os.path.join(output_dirs["completed_transcripts"], stem + ".txt"),
            os.path.join(output_dirs["completed_csv"], stem + ".csv"),
            os.path.join(output_dirs["completed_json"], stem + ".json"),
        ]
        if not any(os.path.exists(path) for path in candidates):
            return stem
        counter += 1


def count_watch_media_files(watch_dir: str) -> int:
    try:
        return sum(1 for p in Path(watch_dir).iterdir() if is_media_path(str(p)))
    except OSError:
        return 0


def find_latest_progress_for_source(watch_dir: str, src_path: str) -> tuple[dict, str] | tuple[None, None]:
    base = safe_output_stem(src_path)
    best_path: str | None = None
    best_data: dict | None = None
    best_mtime = -1.0
    for path in Path(watch_dir).glob(f".watch_progress_{glob.escape(base)}_*.json"):
        try:
            mtime = path.stat().st_mtime
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(data.get("input") or "") != src_path:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = str(path)
            best_data = data
    return best_data, best_path


def reconcile_orphaned_jobs(
    watch_dir: str,
    output_dirs: dict[str, str],
    status: StatusTracker,
    stall_timeout_seconds: float,
) -> None:
    snapshot_jobs = [dict(j) for j in (status.state.get("active_jobs") or [])]
    if not snapshot_jobs:
        return

    now = time.time()
    for job in snapshot_jobs:
        src_path = str(job.get("path") or "")
        worker_pid = int(job.get("worker_pid") or 0)
        if not src_path or process_alive(worker_pid):
            continue

        progress_data, progress_path = find_latest_progress_for_source(watch_dir, src_path)
        if progress_data:
            stem = str(progress_data.get("stem") or "").strip()
            if stem:
                expected_txt = os.path.join(output_dirs["completed_transcripts"], stem + ".txt")
                expected_csv = os.path.join(output_dirs["completed_csv"], stem + ".csv")
                expected_json = os.path.join(output_dirs["completed_json"], stem + ".json")
                expected_video = os.path.join(
                    output_dirs["completed_videos"], stem + Path(src_path).suffix
                )
                outputs_exist = all(os.path.isfile(path) for path in (expected_txt, expected_csv, expected_json))
                if progress_data.get("status") == "completed" and outputs_exist:
                    status.job_end(src_path)
                    if os.path.isfile(src_path) and not os.path.exists(expected_video):
                        try:
                            os.makedirs(os.path.dirname(expected_video), exist_ok=True)
                            shutil.move(src_path, expected_video)
                        except OSError:
                            status.mark_failed(src_path, "reconcile_move_failed")
                            continue
                    status.mark_completed(src_path, expected_video)
                    if progress_path and os.path.exists(progress_path):
                        try:
                            os.remove(progress_path)
                        except OSError:
                            pass
                    continue
                if progress_data.get("status") == "failed":
                    error = progress_data.get("error", "transcription_failed")
                    status.job_end(src_path)
                    status.mark_failed(src_path, error)
                    continue

            last_progress = progress_data.get("last_progress_at")
            if last_progress:
                try:
                    ts = time.strptime(last_progress, "%Y-%m-%dT%H:%M:%SZ")
                    age = now - time.mktime(ts)
                except ValueError:
                    age = None
                if age is not None and age > stall_timeout_seconds:
                    status.job_end(src_path)
                    status.mark_failed(src_path, "worker_disappeared_after_progress_timeout")
                    continue

        status.job_end(src_path)
        status.mark_failed(src_path, "worker_process_disappeared")


def skip_requested(watch_dir: str, src_path: str) -> bool:
    """True (and the request is consumed) when the panel asked to skip this running file."""
    try:
        import queue_control
        return bool(queue_control.take_skips(watch_dir, [src_path]))
    except Exception:
        return False


def kill_process_tree(pid: int) -> None:
    """Stop a worker and its children (the venv python.exe launcher spawns the real interpreter)."""
    try:
        import psutil
        parent = psutil.Process(pid)
        procs = parent.children(recursive=True) + [parent]
        for p in procs:
            try:
                p.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(procs, timeout=10)
    except Exception:
        pass


def media_duration_seconds(path: str) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        return round(float(out), 2) if out else None
    except Exception:
        return None


def describe_engine(settings: dict, force_cpu: bool = False) -> str:
    engine = str(settings.get("asr_engine") or "whisper")
    if force_cpu:
        label = f"whisper {settings.get('whisper_cpu_model') or 'medium'} (CPU)"
    elif engine == "whisper":
        label = f"whisper {settings.get('whisper_gpu_model') or 'large-v3'}"
        if settings.get("whisper_batched"):
            label += " fast"
    else:
        label = engine
    if settings.get("asr_crosscheck") and engine == "whisper":
        label += " +check"
    return label


def catch_up_new_files(handler, watch_dir: str, completed_dir: str, since_epoch: float) -> int:
    """Queue media files created after the watcher process started but before the observer was live."""
    n = 0
    for root, dirs, files in os.walk(watch_dir):
        if is_under_dir(root, completed_dir):
            dirs[:] = []
            continue
        for name in files:
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if is_media_path(path) and max(st.st_ctime, st.st_mtime) >= since_epoch:
                handler._schedule(path)
                n += 1
    return n


def _min_free_gb(repo_root: str) -> float:
    try:
        return max(0.0, float(load_settings(Path(repo_root)).get("min_free_disk_gb", 10.0)))
    except (TypeError, ValueError):
        return 10.0


def refresh_health(status: "StatusTracker", output_dirs: dict[str, str], watch_dir: str, repo_root: str) -> list[dict]:
    import system_health
    min_gb = _min_free_gb(repo_root)
    disks = system_health.disk_snapshot([watch_dir, *output_dirs.values()])
    status.state["health"] = system_health.health_line(system_health.gpu_snapshot(), disks, min_gb)
    return disks


def wait_for_disk_space(status: "StatusTracker", output_dirs: dict[str, str], watch_dir: str, repo_root: str,
                        poll_seconds: float = 30.0) -> None:
    """Pause (not fail) while any output drive is below min_free_disk_gb; warn when within 2x."""
    import system_health
    announced = False
    while True:
        try:
            disks = refresh_health(status, output_dirs, watch_dir, repo_root)
        except Exception as exc:
            print(f"[watch] Disk check skipped: {exc}")
            return
        min_gb = _min_free_gb(repo_root)
        low = system_health.low_disk(disks, min_gb)
        if not low:
            if announced:
                status._append_log("[watch] disk space OK again; resuming")
                status.state["last_error"] = None
            for d in system_health.warn_disk(disks, min_gb):
                status._append_log(f"[watch] WARNING: {d['drive']} has {d['free_gb']} GB free (pause below {min_gb} GB)")
            return
        if not announced:
            msg = ", ".join(f"{d['drive']} {d['free_gb']} GB free" for d in low)
            status._append_log(f"[watch] PAUSED: low disk space ({msg}; need {min_gb} GB). Free space or lower "
                               "min_free_disk_gb; the queue resumes automatically.")
            status.state["last_error"] = f"paused_low_disk: {msg}"
            announced = True
        status.set_phase("paused_low_disk")
        time.sleep(poll_seconds)


def interrupted_files_from_status(status_file: str, watch_dir: str, completed_dir: str) -> list[str]:
    """Files that were queued or mid-transcription when the previous watcher stopped or crashed."""
    try:
        with open(status_file, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        return []
    paths = list(prev.get("pending_files") or [])
    paths += [str(j.get("path") or "") for j in (prev.get("active_jobs") or [])]
    out: list[str] = []
    for p in paths:
        p = os.path.abspath(p) if p else ""
        if p and p not in out and os.path.isfile(p) and is_media_path(p) and is_under_dir(p, watch_dir) \
                and not is_under_dir(p, completed_dir):
            out.append(p)
    return out


async def transcribe_and_move(
    src_path: str,
    watch_dir: str,
    completed_dir: str,
    output_dirs: dict[str, str],
    status: StatusTracker,
    stall_timeout_seconds: float,
) -> None:
    src_path = os.path.abspath(src_path)
    watch_dir = os.path.abspath(watch_dir)
    completed_dir = os.path.abspath(completed_dir)

    if not is_media_path(src_path):
        status.mark_skipped(src_path, "not_media")
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
    repo_root = os.path.dirname(os.path.abspath(__file__))
    fingerprint = None
    try:
        import file_fingerprint
        if load_settings(Path(repo_root)).get("skip_duplicates", True):
            status.set_phase("fingerprinting")
            fingerprint = file_fingerprint.fingerprint(src_path)
            dup_stem = file_fingerprint.find_done(output_dirs["completed_json"], fingerprint)
            if dup_stem:
                dest = file_fingerprint.move_to_skipped(src_path, repo_root)
                print(f"[watch] Skipped duplicate of {dup_stem!r}: moved to {dest}")
                status.set_phase("watching")
                status.mark_skipped(src_path, f"duplicate_of:{dup_stem}")
                return
    except Exception as exc:  # duplicate check is best-effort; never block a transcription
        print(f"[watch] Duplicate check skipped: {type(exc).__name__}: {exc}")
    wait_for_disk_space(status, output_dirs, watch_dir, repo_root)
    output_stem = allocate_completed_stem(output_dirs, base_guess, ext)
    print(f"[watch] Transcribing {src_path!r} -> basename {output_stem!r}")
    status.set_phase("spawning_worker")
    status.mark_processing(src_path)
    status.state["stall_timeout_seconds"] = stall_timeout_seconds

    progress_file = os.path.join(
        watch_dir, f".watch_progress_{safe_output_stem(src_path)}_{int(time.time() * 1000)}.json"
    )
    worker_script = os.path.join(os.path.dirname(__file__), "watch_transcribe_worker.py")
    whisper_log_path = os.path.join(watch_dir, "whisper_live.log")
    whisper_log_fh = open(whisper_log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    try:
        retried_cpu = False

        def spawn_worker(*, force_cpu: bool) -> subprocess.Popen:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            worker_settings = load_settings(Path(os.path.dirname(__file__)))
            env["WHISPER_GPU_MODEL"] = str(worker_settings.get("whisper_gpu_model") or "large-v3")
            env["WHISPER_CPU_MODEL"] = str(worker_settings.get("whisper_cpu_model") or "medium")
            env["ASR_ENGINE"] = str(worker_settings.get("asr_engine") or "whisper")
            env["ASR_CROSSCHECK"] = "1" if worker_settings.get("asr_crosscheck") else "0"
            env["WHISPER_LANGUAGE"] = str(worker_settings.get("whisper_language") or "auto")
            env["WHISPER_DECODE_PROFILE"] = str(worker_settings.get("whisper_decode_profile") or "robust")
            env["WHISPER_BATCHED"] = "1" if worker_settings.get("whisper_batched") else "0"
            env["WHISPER_BATCH_SIZE"] = str(worker_settings.get("whisper_batch_size") or 8)
            if force_cpu:
                env["WHISPER_FORCE_CPU"] = "1"
            else:
                env.pop("WHISPER_FORCE_CPU", None)
            proc = subprocess.Popen(
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
                stdout=whisper_log_fh,
                stderr=whisper_log_fh,
                cwd=os.path.dirname(__file__),
                shell=False,
                env=env,
                # own process group: a console Ctrl+C aimed at the watcher can't kill a worker mid-start;
                # the watcher's stall-kill / panel Stop still terminate it by PID.
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            status.job_begin(src_path, proc.pid)
            status.job_update(
                src_path,
                duration=media_duration_seconds(src_path),
                engine=describe_engine(worker_settings, force_cpu),
                started_at_epoch=time.time(),
            )
            status.set_phase("transcribing")
            return proc

        process = spawn_worker(force_cpu=False)
        last_progress_monotonic = time.monotonic()
        while True:
            exit_code = process.poll()
            if exit_code is None and skip_requested(watch_dir, src_path):
                kill_process_tree(process.pid)
                print(f"[watch] Skipped by user while running: {src_path!r}; worker stopped.")
                try:
                    os.remove(progress_file)
                except OSError:
                    pass
                status.job_end(src_path)
                status.mark_skipped(src_path, "skipped_by_user_while_running")
                return
            progress_data: dict = {}
            if os.path.isfile(progress_file):
                try:
                    with open(progress_file, "r", encoding="utf-8") as f:
                        progress_data = json.load(f)
                except Exception:
                    progress_data = {}

            if progress_data.get("status") == "completed":
                expected_txt = os.path.join(output_dirs["completed_transcripts"], output_stem + ".txt")
                expected_csv = os.path.join(output_dirs["completed_csv"], output_stem + ".csv")
                expected_json = os.path.join(output_dirs["completed_json"], output_stem + ".json")
                if all(os.path.isfile(path) for path in (expected_txt, expected_csv, expected_json)):
                    print(
                        f"[watch] Worker progress reports completed for {src_path!r}; "
                        "reconciling watcher state from output files."
                    )
                    break

            if progress_data.get("last_progress_at"):
                last_progress_monotonic = time.monotonic()
                status.mark_progress(
                    src_path,
                    progress_file=progress_file,
                    worker_pid=int(progress_data.get("worker_pid") or process.pid),
                    last_progress_at=progress_data.get("last_progress_at") or progress_data.get("updated_at"),
                    last_segment_end=progress_data.get("last_segment_end") or progress_data.get("segment_end"),
                )

            if progress_data.get("status") == "failed":
                error = progress_data.get("error", "transcription_failed")
                print(f"[watch] Worker reported failed status for {src_path!r}.")
                status.job_end(src_path)
                status.mark_failed(src_path, error)
                return

            if exit_code is not None:
                if exit_code != 0:
                    if exit_code in {3221226505, 3221225477, -1073740791, -1073741819} and not retried_cpu:
                        print(
                            f"[watch] Worker crashed for {src_path!r} with exit {exit_code}; retrying once on CPU."
                        )
                        retried_cpu = True
                        process = spawn_worker(force_cpu=True)
                        last_progress_monotonic = time.monotonic()
                        await asyncio.sleep(2.0)
                        continue
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
        try:
            whisper_log_fh.close()
        except OSError:
            pass
        if os.path.exists(progress_file):
            try:
                os.remove(progress_file)
            except OSError:
                pass

    status.job_set_phase(src_path, "moving_outputs")

    for path in output_dirs.values():
        os.makedirs(path, exist_ok=True)
    expected_txt = os.path.join(output_dirs["completed_transcripts"], output_stem + ".txt")
    expected_csv = os.path.join(output_dirs["completed_csv"], output_stem + ".csv")
    expected_json = os.path.join(output_dirs["completed_json"], output_stem + ".json")
    missing_outputs = [
        path for path in (expected_txt, expected_csv, expected_json) if not os.path.isfile(path)
    ]
    if missing_outputs:
        print(
            f"[watch] Transcript outputs missing for {src_path!r}; "
            f"not moving source video. Missing: {', '.join(missing_outputs)}"
        )
        status.job_end(src_path)
        status.mark_failed(src_path, "transcript_outputs_missing")
        return
    dest_video = os.path.join(output_dirs["completed_videos"], output_stem + ext)
    already_moved = not os.path.exists(src_path) and os.path.isfile(dest_video)
    try:
        if already_moved:
            # the orphan reconciler can finish the move first once the worker exits
            print(f"[watch] Source already moved to {dest_video!r} by the reconciler.")
        else:
            shutil.move(src_path, dest_video)
    except OSError:
        traceback.print_exc()
        print(
            f"[watch] Transcript done but move failed for {src_path!r}. "
            "Check the completed_* output folders."
        )
        status.job_end(src_path)
        status.mark_failed(src_path, "move_failed")
        return

    print(f"[watch] Completed: {dest_video}")
    try:
        import aeonimus_handoff
        shared = aeonimus_handoff.publish_transcript(expected_txt, expected_json)
        if shared:
            print(f"[watch] Transcript added to the Aeonimus library: {shared[0].parent}")
    except Exception as exc:  # the shared library is optional
        print(f"[watch] Aeonimus library copy skipped: {exc}")
    try:
        import transcript_search
        transcript_search.set_video_path(output_stem, dest_video)
    except Exception as exc:  # the search index is optional
        print(f"[watch] Search index video link skipped: {exc}")
    if fingerprint:
        try:
            import file_fingerprint
            file_fingerprint.record(output_dirs["completed_json"], fingerprint, output_stem)
        except Exception as exc:
            print(f"[watch] Fingerprint record skipped: {exc}")
    status.job_end(src_path)
    status.mark_completed(src_path, dest_video)


class VideoHandler(FileSystemEventHandler):
    def __init__(
        self,
        watch_dir: str,
        completed_dir: str,
        output_dirs: dict[str, str],
        status: StatusTracker,
        stall_timeout_seconds: float,
        max_concurrent_jobs: int,
    ) -> None:
        self.watch_dir = os.path.abspath(watch_dir)
        self.completed_dir = os.path.abspath(completed_dir)
        self.output_dirs = {k: os.path.abspath(v) for k, v in output_dirs.items()}
        self.status = status
        self.stall_timeout_seconds = stall_timeout_seconds
        self._semaphore = threading.Semaphore(max(1, int(max_concurrent_jobs)))
        self._lock = threading.Lock()
        self._queued: set[str] = set()
        self._active: set[str] = set()
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._max_jobs = max(1, int(max_concurrent_jobs))
        self._pending: list[str] = []  # arrival order; queue_control decides the actual order
        self._dispatcher: threading.Thread | None = None

    def wait_until_idle(self, poll_seconds: float = 0.25) -> None:
        while True:
            with self._inflight_lock:
                busy = self._inflight > 0
            with self._lock:
                q = len(self._queued)
            if not busy and q == 0:
                return
            time.sleep(poll_seconds)

    def _publish_queue_locked(self, paused: bool | None = None) -> None:
        import queue_control
        order = queue_control.load(self.watch_dir)["order"]
        self.status.state["pending_files"] = sorted(self._active) + queue_control.ordered(list(self._pending), order)
        self.status.state["queued_order"] = queue_control.ordered(list(self._pending), order)
        if paused is not None:
            self.status.state["queue_paused"] = paused

    def _schedule(self, src_path: str) -> None:
        if not is_media_path(src_path):
            return
        abs_path = os.path.abspath(src_path)
        if is_under_dir(abs_path, self.completed_dir):
            return
        with self._lock:
            if abs_path in self._queued or abs_path in self._active:
                return
            self._queued.add(abs_path)
            self._pending.append(abs_path)
            self._publish_queue_locked()
            self.status.mark_queued(abs_path, len(self._queued))
            if self._dispatcher is None or not self._dispatcher.is_alive():
                self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True)
                self._dispatcher.start()

    def _dispatch_loop(self) -> None:
        """Start queued files one slot at a time, honouring pause / order / skip from the panel."""
        import queue_control
        while True:
            with self._lock:
                if not self._pending:
                    self._publish_queue_locked()
                    self._dispatcher = None
                    return
                pending = list(self._pending)
            for path in queue_control.take_skips(self.watch_dir, pending):
                with self._lock:
                    if path in self._pending:
                        self._pending.remove(path)
                        self._queued.discard(path)
                self.status.mark_skipped(path, "skipped_by_user")
            control = queue_control.load(self.watch_dir)
            with self._lock:
                self._publish_queue_locked(paused=control["paused"])
                with self._inflight_lock:
                    free = self._inflight < self._max_jobs
                nxt = None
                if self._pending and free and not control["paused"]:
                    nxt = queue_control.ordered(list(self._pending), control["order"])[0]
                    self._pending.remove(nxt)
                    self._queued.discard(nxt)
                    self._active.add(nxt)
                    with self._inflight_lock:
                        self._inflight += 1
                    self._publish_queue_locked()
            if nxt is None:
                if control["paused"] and self.status.state.get("phase") != "queue_paused" and not self._active:
                    self.status.set_phase("queue_paused")
                time.sleep(0.5)
                continue
            threading.Thread(target=self._run_job, args=(nxt,), daemon=True).start()

    def _run_job(self, abs_path: str) -> None:
        try:
            asyncio.run(
                transcribe_and_move(
                    abs_path,
                    self.watch_dir,
                    self.completed_dir,
                    self.output_dirs,
                    self.status,
                    self.stall_timeout_seconds,
                )
            )
        except Exception:
            traceback.print_exc()
        finally:
            with self._inflight_lock:
                self._inflight -= 1
            try:
                import queue_control
                queue_control.forget(self.watch_dir, abs_path)
            except Exception:
                pass
            with self._lock:
                self._active.discard(abs_path)
                self._publish_queue_locked()

    def on_created(self, event):  # noqa: ANN001
        if event.is_directory:
            return
        self._schedule(event.src_path)

    def on_modified(self, event):  # noqa: ANN001
        if event.is_directory:
            return
        self._schedule(event.src_path)

    def on_moved(self, event):  # noqa: ANN001
        # Downloads (YouTube/Suno intake, browsers) land as a .part file and are then
        # renamed into place; without this a file that arrives while the watcher runs is missed.
        if event.is_directory:
            return
        dest = os.path.abspath(getattr(event, "dest_path", "") or "")
        if dest and is_under_dir(os.path.dirname(dest), self.watch_dir):
            self._schedule(dest)


def scan_existing(handler: VideoHandler, watch_dir: str, completed_dir: str) -> None:
    watch_dir = os.path.abspath(watch_dir)
    completed_dir = os.path.abspath(completed_dir)
    for root, _dirs, files in os.walk(watch_dir):
        if is_under_dir(root, completed_dir) or os.path.abspath(root) == completed_dir:
            continue
        for name in files:
            path = os.path.join(root, name)
            if is_media_path(path):
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
    output_dirs = {
        key: str(path)
        for key, path in ensure_output_dirs(repo_root, settings).items()
    }
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
        completed_dir = output_dirs["completed_videos"]
    else:
        completed_dir = os.path.abspath(completed_dir)

    os.makedirs(watch_dir, exist_ok=True)
    os.makedirs(completed_dir, exist_ok=True)
    lock = WatcherLock(os.path.join(watch_dir, ".watcher.lock"))
    if not lock.acquire():
        print(f"[watch] Another watcher instance is already running for {watch_dir!r}.")
        return
    status_file = os.path.abspath(args.status_file) if args.status_file else os.path.join(watch_dir, "watch_status.json")
    log_file = os.path.abspath(args.log_file) if args.log_file else os.path.join(watch_dir, "watch_events.log")
    resume_files = (
        interrupted_files_from_status(status_file, watch_dir, completed_dir)
        if settings.get("resume_on_start", True) else []
    )
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

    observer: Observer | None = None
    try:
        handler = VideoHandler(
            watch_dir,
            completed_dir,
            output_dirs,
            status,
            stall_timeout_seconds,
            max_concurrent_jobs,
        )
        if args.scan_existing:
            print(f"[watch] Scanning existing files in {watch_dir!r} ...")
            scan_existing(handler, watch_dir, completed_dir)
        if resume_files:
            status.state["resumed_files"] = resume_files
            for path in resume_files:
                for stale in Path(watch_dir).glob(f".watch_progress_{glob.escape(safe_output_stem(path))}_*.json"):
                    try:
                        stale.unlink()
                    except OSError:
                        pass
                status._append_log(f"[watch] resuming interrupted file: {path}")
                handler._schedule(path)
        try:
            refresh_health(status, output_dirs, watch_dir, str(repo_root))
            status._append_log(f"[watch] health: {status.state.get('health')}")
        except Exception as exc:
            print(f"[watch] Health check skipped: {exc}")
        last_health = time.monotonic()

        observer = Observer()
        observer.schedule(handler, watch_dir, recursive=True)
        observer.start()
        try:
            import psutil as _ps
            started = _ps.Process(os.getpid()).create_time() - 2
        except Exception:
            started = time.time() - 30
        caught = catch_up_new_files(handler, watch_dir, completed_dir, started)
        if caught:
            print(f"[watch] Picked up {caught} file(s) that arrived during startup.")
        print(
            f"[watch] Watching {watch_dir!r} -> completed {completed_dir!r} "
            "(Ctrl+C to stop)"
        )
        while True:
            reconcile_orphaned_jobs(watch_dir, output_dirs, status, stall_timeout_seconds)
            if time.monotonic() - last_health > 30:
                last_health = time.monotonic()
                try:
                    refresh_health(status, output_dirs, watch_dir, str(repo_root))
                except Exception:
                    pass
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        status.mark_runtime_error(error)
        raise
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)
        telemetry.stop()
        status.stop()
        lock.release()


if __name__ == "__main__":
    main()
