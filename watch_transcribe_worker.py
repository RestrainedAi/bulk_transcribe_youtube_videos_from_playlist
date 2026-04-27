import argparse
import asyncio
import json
import os
import time

import bulk_transcribe_youtube_videos_from_playlist as bt


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_progress(progress_path: str, payload: dict) -> None:
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def main() -> int:
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
        "status": "starting",
        "started_at": utc_now(),
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

    try:
        asyncio.run(
            bt.compute_transcript_with_whisper_from_audio_func(
                input_path,
                stem,
                size_mb,
                progress_callback=on_progress,
            )
        )
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
