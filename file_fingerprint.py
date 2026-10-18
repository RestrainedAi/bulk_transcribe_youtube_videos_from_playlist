"""Content fingerprints so a file that was already transcribed is not transcribed again.

A fingerprint is the file size plus a SHA-256 of three 4 MB samples (start, middle,
end). That reads at most 12 MB per file, so it is fast even for multi-GB videos, and a
renamed or re-downloaded copy of the same file still matches.
The registry lives in completed_json/fingerprints.json and maps fingerprint -> stem.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time

SAMPLE_BYTES = 4 * 1024 * 1024
REGISTRY_NAME = "fingerprints.json"
SKIPPED_DIR_NAME = "skipped_duplicates"
_lock = threading.Lock()


def fingerprint(path: str) -> str:
    size = os.path.getsize(path)
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        if size <= 3 * SAMPLE_BYTES:
            h.update(f.read())
        else:
            for offset in (0, size // 2 - SAMPLE_BYTES // 2, size - SAMPLE_BYTES):
                f.seek(offset)
                h.update(f.read(SAMPLE_BYTES))
    return f"{size}:{h.hexdigest()}"


def registry_path(completed_json_dir: str) -> str:
    return os.path.join(completed_json_dir, REGISTRY_NAME)


def load_registry(completed_json_dir: str) -> dict[str, str]:
    try:
        with open(registry_path(completed_json_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_registry(completed_json_dir: str, data: dict[str, str]) -> None:
    os.makedirs(completed_json_dir, exist_ok=True)
    path = registry_path(completed_json_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def record(completed_json_dir: str, fp: str, stem: str) -> None:
    with _lock:
        data = load_registry(completed_json_dir)
        data[fp] = stem
        _save_registry(completed_json_dir, data)


def find_done(completed_json_dir: str, fp: str) -> str | None:
    """Return the stem of a finished transcript with this fingerprint, if its JSON still exists."""
    stem = load_registry(completed_json_dir).get(fp)
    if stem and os.path.isfile(os.path.join(completed_json_dir, stem + ".json")):
        return stem
    return None


def move_to_skipped(src_path: str, repo_root: str) -> str:
    """Move a duplicate out of the (recursively) watched folder so it is not picked up again."""
    dest_dir = os.path.join(repo_root, SKIPPED_DIR_NAME)
    os.makedirs(dest_dir, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(src_path))
    dest = os.path.join(dest_dir, base + ext)
    if os.path.exists(dest):
        dest = os.path.join(dest_dir, f"{base}_{int(time.time())}{ext}")
    shutil.move(src_path, dest)
    return dest


def backfill(completed_videos_dir: str, completed_json_dir: str) -> int:
    """Fingerprint already-completed videos whose JSON exists. Returns how many were added."""
    data = load_registry(completed_json_dir)
    known = set(data.values())
    added = 0
    if not os.path.isdir(completed_videos_dir):
        return 0
    for name in sorted(os.listdir(completed_videos_dir)):
        path = os.path.join(completed_videos_dir, name)
        stem = os.path.splitext(name)[0]
        if not os.path.isfile(path) or stem in known:
            continue
        if not os.path.isfile(os.path.join(completed_json_dir, stem + ".json")):
            continue
        try:
            data[fingerprint(path)] = stem
            added += 1
        except OSError as exc:
            print(f"fingerprint skipped for {name}: {exc}")
    if added:
        with _lock:
            merged = load_registry(completed_json_dir)
            merged.update(data)
            _save_registry(completed_json_dir, merged)
    return added


if __name__ == "__main__":
    from pathlib import Path
    from output_paths import resolved_output_dirs
    from watcher_settings import load_settings

    root = Path(__file__).resolve().parent
    dirs = resolved_output_dirs(root, load_settings(root))
    print("added", backfill(str(dirs["completed_videos"]), str(dirs["completed_json"])))
    sys.exit(0)
