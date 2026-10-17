# Architecture

Last updated: **2026-10-17**, version **1.2.0**.

## Big picture

```text
paste link / Load CSV / Import from URL Lister
        |  youtube_intake.py (yt-dlp, or the public Suno song file)
        v
watch_incoming\<title> [<key>].<audio>
        |  watch_and_transcribe.py (watchdog) -> watch_transcribe_worker.py (faster-whisper)
        v
completed_transcripts\ .txt, completed_json\ .json, completed_csv\ .csv
        |  aeonimus_handoff.publish_transcript
        v
<Aeonimus Library>\transcripts\  -> Comment Workbench "Attach transcripts"
```

## Main files

| File | What it does |
|---|---|
| `watcher_control_panel.py` | The Tk desktop app (Dashboard, queue, logs, settings, transcript review). Starts and stops the watcher. Only one copy runs at a time. |
| `app_version.py` | App name and version (single source). |
| `youtube_intake.py` | Reads YouTube and Suno links from text, CSV or TXT; expands playlists; downloads audio only with yt-dlp; downloads Suno songs from the public song page; builds the "Add YouTube / Suno links" card. No Tk at module level except the card builder. |
| `aeonimus_handoff.py` | The Aeonimus handoff contract: shared `handoff.json`, inbox to library moves, import ledger, publishing transcripts, moving the library. No Tk, unit tested. |
| `watch_and_transcribe.py` | The watcher: sees new or renamed files in the watch folder and queues them. |
| `watch_transcribe_worker.py` | Transcribes one file with faster-whisper (GPU model large-v3-turbo by default) and writes .txt/.json/.csv. |
| `watcher_settings.py` | Loads and saves `watcher_settings.json`. |
| `watcher_telemetry.py` | Heartbeat and progress reporting. |
| `post_transcript_handoff.py` | Optional: send a finished transcript to NotebookLM (clipboard), LM Studio or an AI service. |
| `youtube_transcript_export.py`, `youtube_transcript_panel.py`, `scripts/yt_export_transcripts.py` | Separate tool: export YouTube's own captions to WebVTT/TXT (`Open_Youtube_Caption_Export.cmd`). |
| `bulk_transcribe_youtube_videos_from_playlist.py` | The original command-line script from the upstream project. |
| `transcript_reader.html` | Simple reader for transcripts in a browser. |
| `start_watcher.ps1`, `stop_watcher.ps1` | Start and stop the watcher from PowerShell. |
| `tests/` | pytest suites (handoff, YouTube intake, Suno links, caption export, and more). |

### Not committed to git yet

These modules are used by the app today but are still uncommitted work in the
folder: `asr_engines.py`, `file_fingerprint.py`, `output_paths.py`,
`queue_control.py`, `safe_io.py`, `startup_selfcheck.py`, `support_tools.py`,
`system_health.py`, `transcript_review.py`, `transcript_search.py`,
`asr_eval_worker.py`, plus several test files. The committed code imports
several of them, so a clean checkout of the branch does not run on its own
until they are committed.

## Storage

- Plain files in the app folder (`watch_incoming\`, `completed_*\`, `logs\`).
- `watcher_settings.json`: app settings, including optional API keys (keep
  private, not in git).
- `%APPDATA%\Aeonimus\handoff.json`: shared inbox/library locations.
- `%LOCALAPPDATA%\Aeonimus\TranscriptionWorkbench\`: what was already imported.

## External services

- YouTube and Suno: public pages and media only, no login.
- Optional, only if the client adds keys or tools: OpenAI, Anthropic, Google
  AI, LM Studio (local), NotebookLM (opened in the browser).

## Dependencies and licenses

| Package | License | Notes |
|---|---|---|
| faster-whisper | MIT | Transcription |
| Whisper model weights | MIT | Downloaded on first use |
| nvidia-cublas-cu12, nvidia-cudnn-cu12 | NVIDIA software license | GPU runtime; check redistribution terms before shipping an installer |
| yt-dlp | Unlicense | Audio downloads |
| yt-dlp-ejs | Unlicense | YouTube player support for yt-dlp |
| pytubefix | MIT | Used by the original CLI script |
| watchdog | Apache-2.0 | Watch folder |
| psutil, pandas, numba | BSD | |
| tqdm | MPL-2.0 / MIT | |
| spacy | MIT | Optional sentence splitting |
| fastapi | MIT | |
| openai, google-api-python-client | Apache-2.0 | Optional |
| anthropic, youtube-transcript-api | MIT | Optional |
| ruff | MIT | Dev only |
| ffmpeg | LGPL or GPL, depending on the build | **Check before selling.** The WinGet build is usually GPL. Ship an LGPL build instead. |

`mutagen` (GPL) is deliberately not used. The app itself started as a fork of
an MIT-licensed project (see `LICENSE`).
