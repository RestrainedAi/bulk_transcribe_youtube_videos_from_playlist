import argparse
import asyncio
import os
import sys
import time

import bulk_transcribe_youtube_videos_from_playlist as bt
from safe_io import atomic_write_json


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_progress(progress_path: str, payload: dict) -> None:
    payload["worker_pid"] = os.getpid()
    payload["updated_at"] = utc_now()
    atomic_write_json(progress_path, payload)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Transcribe one local video file.")
    parser.add_argument("--input", required=True, help="Path to local video/audio file")
    parser.add_argument("--stem", required=True, help="Output filename stem")
    parser.add_argument("--progress", required=True, help="Path to heartbeat/progress JSON")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    stem = args.stem
    progress_path = os.path.abspath(args.progress)
    size_mb = os.path.getsize(input_path) / (1024 * 1024)

    progress = {
        "input": input_path,
        "stem": stem,
        "worker_pid": os.getpid(),
        "status": "starting",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "last_progress_at": None,
        "last_segment_end": 0.0,
        "last_segment_text": None,
    }
    write_progress(progress_path, progress)

    def on_progress(start: float, end: float, text: str) -> None:
        progress["status"] = "processing"
        progress["last_progress_at"] = utc_now()
        progress["last_segment_end"] = round(float(end), 2)
        progress["last_segment_text"] = text[:200]
        write_progress(progress_path, progress)

    audio_path = input_path
    audio_mode = (os.environ.get("AUDIO_MODE") or "speech").strip().lower()
    progress["audio_mode"] = audio_mode
    if audio_mode == "music" and os.environ.get("MUSIC_VOCAL_SEPARATION", "1") == "1":
        import vocal_separation
        progress["status"] = "separating_vocals"
        write_progress(progress_path, progress)
        audio_path = vocal_separation.separate_vocals(input_path) or input_path
        progress["vocals_separated"] = audio_path != input_path
        write_progress(progress_path, progress)
    try:
        transcript_text, transcript_segments, _sentences = asyncio.run(
            bt.compute_transcript_with_whisper_from_audio_func(
                audio_path,
                stem,
                size_mb,
                progress_callback=on_progress,
            )
        )
        txt_path = os.path.join(bt.COMPLETED_TRANSCRIPTS_DIR, f"{stem}.txt")
        csv_path = os.path.join(bt.COMPLETED_CSV_DIR, f"{stem}.csv")
        json_path = os.path.join(bt.COMPLETED_JSON_DIR, f"{stem}.json")
        if not transcript_text.strip() or not transcript_segments:
            raise RuntimeError("transcription_returned_no_segments")
        if not (os.path.isfile(txt_path) and os.path.isfile(csv_path) and os.path.isfile(json_path)):
            raise RuntimeError("transcription_outputs_missing")
    except Exception as exc:
        progress["status"] = "failed"
        progress["error"] = f"{type(exc).__name__}: {exc}"
        progress["failed_at"] = utc_now()
        write_progress(progress_path, progress)
        raise

    progress["status"] = "completed"
    progress["completed_at"] = utc_now()
    write_progress(progress_path, progress)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
