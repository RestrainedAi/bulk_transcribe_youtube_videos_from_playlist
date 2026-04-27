"""One-off: extend watcher_settings defaults and requirements.txt for YouTube caption export."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ws = ROOT / "watcher_settings.py"
    t = ws.read_text(encoding="utf-8")
    if "youtube_transcript_export_dir" in t:
        print("watcher_settings.py already extended; skipping DEFAULT_SETTINGS edit")
    else:
        _patch_watcher_defaults(ws, t)
    _patch_requirements(ROOT)


def _patch_watcher_defaults(ws: Path, t: str) -> None:
    needle_lf = '    "xai_api_key": "",\n}'
    needle_crlf = '    "xai_api_key": "",\r\n}'
    repl_lf = (
        '    "xai_api_key": "",\n'
        '    "youtube_api_key": "",\n'
        '    "youtube_transcript_export_dir": "youtube_captions_exports",\n'
        '    "youtube_transcript_lang": "en",\n'
        '    "youtube_transcript_concurrency": 2,\n'
        '    "youtube_transcript_also_save_txt": False,\n'
        "}"
    )
    repl_crlf = repl_lf.replace("\n", "\r\n")
    if needle_crlf in t:
        t = t.replace(needle_crlf, repl_crlf, 1)
    elif needle_lf in t:
        t = t.replace(needle_lf, repl_lf, 1)
    else:
        raise SystemExit("watcher_settings.py pattern not found; already patched?")
    ws.write_text(t, encoding="utf-8")
    print("Patched watcher_settings.py DEFAULT_SETTINGS")


def _patch_requirements(ROOT: Path) -> None:
    req = ROOT / "requirements.txt"
    r = req.read_text(encoding="utf-8").strip() + "\n"
    for pkg in ("google-api-python-client", "youtube-transcript-api"):
        if pkg not in r:
            r += pkg + "\n"
    req.write_text(r, encoding="utf-8")
    print("Patched requirements.txt")


if __name__ == "__main__":
    main()
