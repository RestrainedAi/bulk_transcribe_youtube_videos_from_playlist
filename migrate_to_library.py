"""Copy the watch / transcript folders out of the program folder into the Aeonimus Library (v1.4.0).

    python migrate_to_library.py              # dry run (default): reports only, writes nothing
    python migrate_to_library.py --apply      # copy with SHA-256 checks, then point settings at the library
    python migrate_to_library.py --skip-videos  # leave completed_videos / completed_audio where they are

Stop the watcher first. Originals are never deleted or changed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import data_library
from watcher_settings import load_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="really copy (default is a dry run)")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent), help="program folder with watcher_settings.json")
    ap.add_argument("--library", default=None, help="library folder (default: shared Aeonimus setting)")
    ap.add_argument("--skip-videos", action="store_true")
    a = ap.parse_args(argv)
    repo = Path(a.repo)
    settings = load_settings(repo)
    plan = data_library.plan_migration(repo, settings, Path(a.library) if a.library else None,
                                       include_videos=not a.skip_videos)
    stats = data_library.run_migration(repo, plan, dry_run=not a.apply)
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
