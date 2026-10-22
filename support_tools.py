"""Support helpers: a friendly catalog of every setting, a unified log, and a 'copy for support' report."""
from __future__ import annotations

import json
import platform
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
UNIFIED_LOG = LOG_DIR / "unified.log"
SECRET_KEYS = ("api_key", "secret")

# key -> (section, friendly label, help)
CATALOG: dict[str, tuple[str, str, str]] = {
    "watch_dir": ("Folders", "Watch folder", "New videos dropped here are transcribed."),
    "completed_dir": ("Folders", "Finished videos folder (legacy name)", "Same as 'Finished videos folder'; kept for old setups."),
    "completed_video_dir": ("Folders", "Finished videos folder", "Videos are moved here when done."),
    "completed_audio_dir": ("Folders", "Finished audio folder", ""),
    "completed_transcript_dir": ("Folders", "Transcripts folder (.txt/.srt/.vtt)", ""),
    "completed_csv_dir": ("Folders", "CSV folder", ""),
    "completed_json_dir": ("Folders", "JSON folder", "Segment data, flags and the duplicate registry."),
    "whisper_gpu_model": ("Transcription", "GPU model", "large-v3 is the accurate default."),
    "whisper_cpu_model": ("Transcription", "CPU fallback model", "Used only if the GPU fails."),
    "whisper_decode_profile": ("Transcription", "Decode profile",
                               "auto (recommended) = speech settings, redone once with music settings if audio looks missed; "
                               "speech = anti-hallucination; music = keeps singing; legacy = original."),
    "prefer_youtube_captions": ("Transcription", "Use YouTube captions when available",
                                "YouTube links use the video's own captions (seconds); Whisper runs only if there are none."),
    "coverage_max_block_s": ("Transcription", "Missed-audio check: longest line (s)", "Lines longer than this are flagged."),
    "coverage_max_gap_s": ("Transcription", "Missed-audio check: longest gap (s)", "Silent stretches longer than this are flagged."),
    "whisper_language": ("Transcription", "Language", "auto detects it per file; or e.g. en."),
    "asr_engine": ("Transcription", "Transcription engine", "whisper, voxtral-mini-3b or granite-2b."),
    "asr_crosscheck": ("Transcription", "Voxtral cross-check", "Second model compares and flags disagreements."),
    "whisper_batched": ("Speed", "Fast batched mode", "About 4x faster, very slightly less accurate. Off by default."),
    "whisper_batch_size": ("Speed", "Batch size (fast mode)", "1-32; 8 is a good value."),
    "skip_duplicates": ("Speed", "Skip files already transcribed", "Same content, even renamed, goes to skipped_duplicates."),
    "max_concurrent_jobs": ("Watcher", "Files at once", "Keep 1 on a shared GPU."),
    "start_scan_existing": ("Watcher", "Scan the watch folder on start", ""),
    "resume_on_start": ("Watcher", "Resume interrupted files on start", "Re-queues files left mid-way by a crash or stop."),
    "min_free_disk_gb": ("Watcher", "Pause when free disk is below (GB)", "Queue pauses instead of failing; resumes on its own."),
    "stall_timeout_seconds": ("Watcher", "Stall timeout (seconds)", "A worker with no progress this long is stopped."),
    "heartbeat_seconds": ("Watcher", "Status refresh (seconds)", ""),
    "log_profile": ("Watcher", "Log detail", "standard or verbose."),
    "asr_models_dir": ("Engines", "Extra models folder", "Voxtral / Granite models."),
    "asr_eval_python": ("Engines", "Python for extra engines", "Blank = venv_asr_eval."),
    "theme": ("Appearance", "Theme", "Harbor, Midnight, Graphite or Aurora. Unknown values use Harbor."),
    "shared_library_dir": ("Folders", "Shared library override", "Empty = the Aeonimus Library shared with the Comment Workbench."),
    "needs_review_dir": ("Folders", "Needs-review folder", "Empty = <library>\\transcription\\needs-review."),
    "auto_needs_review": ("Watcher", "Move no-speech files to needs-review", "Content failures go to needs-review with a reason file."),
    "keep_watching_after_run": ("Watcher", "Keep watching after run", "Off = the watcher turns off when the queue is empty after a run."),
    "music_vocal_separation": ("Transcription", "Separate vocals for Music/lyrics", "Uses audio-separator if installed; else the full mix."),
    "lm_studio_base_url": ("Handoff", "LM Studio URL", ""),
    "lm_studio_model": ("Handoff", "LM Studio model", ""),
    "default_post_transcript_destination": ("Handoff", "Default handoff destination", ""),
    "default_llm_provider": ("Handoff", "Default LLM provider", ""),
    "post_transcript_always_ask": ("Handoff", "Always ask after a transcript", ""),
    "handoff_try_api_first": ("Handoff", "Try API first", ""),
    "handoff_llm_system_prompt": ("Handoff", "Handoff prompt", ""),
    "openai_api_key": ("API keys", "OpenAI API key", ""),
    "anthropic_api_key": ("API keys", "Anthropic API key", ""),
    "google_api_key": ("API keys", "Google API key", ""),
    "xai_api_key": ("API keys", "xAI API key", ""),
    "youtube_api_key": ("API keys", "YouTube API key", ""),
    "youtube_api_key_secret_project": ("API keys", "YouTube key secret project", ""),
    "youtube_api_key_secret_id": ("API keys", "YouTube key secret id", ""),
    "youtube_transcript_export_dir": ("YouTube captions", "Export folder", ""),
    "youtube_transcript_lang": ("YouTube captions", "Language", ""),
    "youtube_transcript_concurrency": ("YouTube captions", "Parallel downloads", ""),
    "youtube_transcript_also_save_txt": ("YouTube captions", "Also save .txt", ""),
}
SECTION_ORDER = ["Transcription", "Speed", "Watcher", "Folders", "Engines", "Handoff", "YouTube captions", "API keys", "Appearance", "Other"]


def describe(key: str) -> tuple[str, str, str]:
    return CATALOG.get(key, ("Other", key.replace("_", " ").capitalize(), ""))


def is_secret(key: str) -> bool:
    return any(s in key for s in SECRET_KEYS)


def coerce(value: str | bool, default):
    """Turn a widget value back into the type of the default."""
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        return int(float(str(value).strip()))
    if isinstance(default, float):
        return float(str(value).strip())
    return str(value)


def redacted_settings(settings: dict) -> dict:
    return {k: ("***set***" if v else "") if is_secret(k) else v for k, v in settings.items()}


_TS = re.compile(r"^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


def _tail(path: Path, n: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def log_sources(watch_dir: Path) -> dict[str, Path]:
    return {
        "launcher": LOG_DIR / "launcher.log",
        "selfcheck": LOG_DIR / "selfcheck.log",
        "watcher": Path(watch_dir) / "watch_events.log",
        "worker": Path(watch_dir) / "whisper_live.log",
    }


def build_unified_log(watch_dir: Path, per_source: int = 400) -> Path:
    """Merge the app's logs into logs/unified.log, ordered by time where lines carry one."""
    rows = []
    for name, path in log_sources(watch_dir).items():
        last_ts = ""
        for i, line in enumerate(_tail(path, per_source)):
            m = _TS.match(line)
            if m:
                last_ts = m.group(1).replace("T", " ")
            if line.strip():
                rows.append((last_ts, name, i, line.rstrip()))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    LOG_DIR.mkdir(exist_ok=True)
    UNIFIED_LOG.write_text("\n".join(f"[{src:9}] {line}" for _ts, src, _i, line in rows) + "\n", encoding="utf-8")
    return UNIFIED_LOG


def support_report(watch_dir: Path, settings: dict, status: dict | None, health: str | None) -> str:
    out = [f"Transcription watcher support report - {datetime.now():%Y-%m-%d %H:%M:%S}",
           f"Python {sys.version.split()[0]} on {platform.platform()}", f"Folder: {ROOT}"]
    try:
        from importlib.metadata import version
        out.append("Packages: " + ", ".join(f"{p} {version(p)}" for p in ("faster-whisper", "ctranslate2", "watchdog", "psutil")))
    except Exception:
        pass
    out.append(f"Health: {health or '-'}")
    if status:
        keep = ("running", "status", "phase", "current_file", "last_error", "last_event", "completed_count",
                "failed_count", "skipped_count", "queue_paused", "pending_files")
        out.append("Status: " + json.dumps({k: status.get(k) for k in keep}, ensure_ascii=False))
    out.append("Settings: " + json.dumps(redacted_settings(settings), ensure_ascii=False, indent=1))
    for name, path in log_sources(watch_dir).items():
        out.append(f"\n--- {name} ({path.name}, last 40 lines) ---")
        out += _tail(path, 40)
    return "\n".join(out)
