"""Shared repo-root output directory layout for watcher + transcript jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any


DEFAULT_COMPLETED_VIDEO_DIR = "completed_videos"
DEFAULT_COMPLETED_AUDIO_DIR = "completed_audio"
DEFAULT_COMPLETED_TRANSCRIPT_DIR = "completed_transcripts"
DEFAULT_COMPLETED_CSV_DIR = "completed_csv"
DEFAULT_COMPLETED_JSON_DIR = "completed_json"


def _resolve_dir(repo_root: Path, raw: str, fallback: str) -> Path:
    value = raw.strip() or fallback
    p = Path(value)
    return p.resolve() if p.is_absolute() else (repo_root / p).resolve()


def resolved_completed_video_dir(
    repo_root: Path,
    settings: dict[str, Any] | None = None,
) -> Path:
    s = settings or {}
    raw = str(s.get("completed_video_dir") or s.get("completed_dir") or "").strip()
    return _resolve_dir(repo_root, raw, DEFAULT_COMPLETED_VIDEO_DIR)


def resolved_completed_audio_dir(
    repo_root: Path,
    settings: dict[str, Any] | None = None,
) -> Path:
    s = settings or {}
    raw = str(s.get("completed_audio_dir") or "").strip()
    return _resolve_dir(repo_root, raw, DEFAULT_COMPLETED_AUDIO_DIR)


def resolved_completed_transcript_dir(
    repo_root: Path,
    settings: dict[str, Any] | None = None,
) -> Path:
    s = settings or {}
    raw = str(s.get("completed_transcript_dir") or "").strip()
    return _resolve_dir(repo_root, raw, DEFAULT_COMPLETED_TRANSCRIPT_DIR)


def resolved_completed_csv_dir(
    repo_root: Path,
    settings: dict[str, Any] | None = None,
) -> Path:
    s = settings or {}
    raw = str(s.get("completed_csv_dir") or "").strip()
    return _resolve_dir(repo_root, raw, DEFAULT_COMPLETED_CSV_DIR)


def resolved_completed_json_dir(
    repo_root: Path,
    settings: dict[str, Any] | None = None,
) -> Path:
    s = settings or {}
    raw = str(s.get("completed_json_dir") or "").strip()
    return _resolve_dir(repo_root, raw, DEFAULT_COMPLETED_JSON_DIR)


def resolved_output_dirs(
    repo_root: Path,
    settings: dict[str, Any] | None = None,
) -> dict[str, Path]:
    return {
        "completed_videos": resolved_completed_video_dir(repo_root, settings),
        "completed_audio": resolved_completed_audio_dir(repo_root, settings),
        "completed_transcripts": resolved_completed_transcript_dir(repo_root, settings),
        "completed_csv": resolved_completed_csv_dir(repo_root, settings),
        "completed_json": resolved_completed_json_dir(repo_root, settings),
    }


def ensure_output_dirs(
    repo_root: Path,
    settings: dict[str, Any] | None = None,
) -> dict[str, Path]:
    dirs = resolved_output_dirs(repo_root, settings)
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs
