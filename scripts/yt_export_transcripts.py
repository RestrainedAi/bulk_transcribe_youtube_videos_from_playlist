"""CLI: export YouTube captions to WebVTT."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from watcher_settings import load_settings  # noqa: E402
from youtube_transcript_export import run_playlist_export, run_single_export  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Export YouTube captions as WebVTT.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--playlist", metavar="URL_OR_ID", help="Playlist URL or ID")
    g.add_argument("--video", metavar="URL_OR_ID", help="Single video URL or ID")
    args = p.parse_args()
    settings = load_settings(ROOT)

    def log(m: str) -> None:
        print(m, flush=True)

    def prog(cur: int, tot: int, _x: str) -> None:
        print(f"\rprogress {cur}/{tot}", end="", flush=True)

    if args.playlist:
        s = run_playlist_export(ROOT, settings, args.playlist, log, prog)
    else:
        s = run_single_export(ROOT, settings, args.video, log, prog)
    print(f"\nDone: ok={s.ok} failed={s.failed} dest={s.dest}")


if __name__ == "__main__":
    main()
