"""Persisted watcher + control panel settings (JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from safe_io import atomic_write_text

DEFAULT_SETTINGS: dict[str, Any] = {
    "watch_dir": "watch_incoming",
    "completed_dir": "",
    "completed_video_dir": "completed_videos",
    "completed_audio_dir": "completed_audio",
    "completed_transcript_dir": "completed_transcripts",
    "completed_csv_dir": "completed_csv",
    "completed_json_dir": "completed_json",
    "stall_timeout_seconds": 900.0,
    "heartbeat_seconds": 5.0,
    "max_concurrent_jobs": 1,
    "log_profile": "standard",
    "start_scan_existing": True,
    "lm_studio_base_url": "http://localhost:1234/v1",
    "lm_studio_model": "",
    "default_post_transcript_destination": "notebooklm",
    "default_llm_provider": "chatgpt",
    "post_transcript_always_ask": False,
    "handoff_try_api_first": False,
    "handoff_llm_system_prompt": (
        "Summarize the transcript for an operator: key points, action items, and notable quotes."
    ),
    "openai_api_key": "",
    "anthropic_api_key": "",
    "google_api_key": "",
    "xai_api_key": "",
    "youtube_api_key": "",
    "youtube_transcript_export_dir": "youtube_captions_exports",
    "youtube_transcript_lang": "en",
    "youtube_transcript_concurrency": 2,
    "youtube_transcript_also_save_txt": False,
    "youtube_api_key_secret_project": "",
    "youtube_api_key_secret_id": "",
    "whisper_gpu_model": "large-v3",
    "whisper_cpu_model": "medium",
    "whisper_decode_profile": "auto",
    "prefer_youtube_captions": True,
    "coverage_max_block_s": 30,
    "coverage_max_gap_s": 60,
    "whisper_language": "auto",
    "asr_engine": "whisper",
    "asr_crosscheck": False,
    "whisper_batched": False,
    "whisper_batch_size": 8,
    "skip_duplicates": True,
    "resume_on_start": True,
    "min_free_disk_gb": 10.0,
    "asr_models_dir": "E:\\.lmstudio\\models\\ASR",
    "asr_eval_python": "",
    "theme": "Harbor",
}


THEME_NAMES = ("Harbor", "Midnight", "Graphite", "Aurora")


def normalize_theme(value: Any) -> str:
    """Known skin name (case-insensitive) or the Harbor default."""
    if isinstance(value, str):
        for n in THEME_NAMES:
            if n.lower() == value.strip().lower():
                return n
    return "Harbor"


def settings_path(repo_root: Path) -> Path:
    return repo_root / "watcher_settings.json"


def load_settings(repo_root: Path) -> dict[str, Any]:
    path = settings_path(repo_root)
    data = dict(DEFAULT_SETTINGS)
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for k, v in loaded.items():
                    if k in DEFAULT_SETTINGS:
                        data[k] = v
        except (json.JSONDecodeError, OSError):
            pass
    # granite-8b was removed (model deleted); older saved settings fall back to the default engine.
    if data.get("asr_engine") not in ("whisper", "voxtral-mini-3b", "granite-2b"):
        data["asr_engine"] = "whisper"
    data["theme"] = normalize_theme(data.get("theme"))
    return data


def save_settings(repo_root: Path, updates: dict[str, Any]) -> None:
    data = load_settings(repo_root)
    for k, v in updates.items():
        if k in DEFAULT_SETTINGS:
            data[k] = v
    # Keep the legacy alias in sync with the canonical completed video setting.
    if "completed_video_dir" in updates and "completed_dir" not in updates:
        data["completed_dir"] = data["completed_video_dir"]
    if "completed_dir" in updates and "completed_video_dir" not in updates:
        data["completed_video_dir"] = data["completed_dir"]
    path = settings_path(repo_root)
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))


def resolved_watch_dir(repo_root: Path, settings: dict[str, Any] | None = None) -> Path:
    s = settings if settings is not None else load_settings(repo_root)
    raw = (str(s.get("watch_dir") or "watch_incoming")).strip() or "watch_incoming"
    p = Path(raw)
    return p.resolve() if p.is_absolute() else (repo_root / p).resolve()


def resolved_completed_dir(repo_root: Path, settings: dict[str, Any] | None = None) -> Path:
    s = settings if settings is not None else load_settings(repo_root)
    raw = str(s.get("completed_video_dir") or s.get("completed_dir") or "").strip()
    if raw:
        p = Path(raw)
        return p.resolve() if p.is_absolute() else (repo_root / p).resolve()
    return (repo_root / "completed_videos").resolve()
