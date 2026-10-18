# Architecture

Last updated: **2026-10-17**, version **1.3.1**.

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
| `coverage_check.py` | Missed-audio check: flags lines over 30 s and gaps over 60 s (start and end of file included). Used by the transcriber, transcript review and the Dashboard. |
| `youtube_captions.py` | "Use YouTube captions when available": picks a caption track (manual > en-orig > en, never translated), rate-limits and retries HTTP 429, converts json3 to segments, writes txt/srt/vtt/json/csv and the library copy. Called by the download queue before the audio download; returns None to fall back to Whisper. |
| `watch_and_transcribe.py` | The watcher: sees new or renamed files in the watch folder and queues them. |
| `watch_transcribe_worker.py` | Transcribes one file with faster-whisper (GPU model large-v3-turbo by default) and writes .txt/.json/.csv. |
| `watcher_settings.py` | Loads and saves `watcher_settings.json`. |
| `watcher_telemetry.py` | Heartbeat and progress reporting. |
| `post_transcript_handoff.py` | Optional: send a finished transcript to NotebookLM (clipboard), LM Studio or an AI service. |
| `youtube_transcript_export.py`, `youtube_transcript_panel.py`, `scripts/yt_export_transcripts.py` | Separate tool: export YouTube's own captions to WebVTT/TXT (`Open_Youtube_Caption_Export.cmd`). |
| `bulk_transcribe_youtube_videos_from_playlist.py` | Whisper transcription core (started from the upstream script): decode profiles auto / speech (robust) / music / legacy, GPU-then-CPU attempts, the auto music retry, and `coverage_warnings` in the JSON and `.flags.txt`. |
| `transcript_reader.html` | Simple reader for transcripts in a browser. |
| `start_watcher.ps1`, `stop_watcher.ps1` | Start and stop the watcher from PowerShell. |
| `tests/` | pytest suites (handoff, YouTube intake, Suno links, caption export, and more). |

Since the 2026-10-17 snapshot every module the app imports is committed.

## Skins (1.3.1)

- `themes.py` holds `SKINS` (Harbor, Midnight, Graphite, Aurora; 20 tokens each, same order as
  `docs/design/palettes.json`), `normalize()` (unknown -> Harbor), `panel_constants()` (every colour
  constant the control panel uses) and `color_map(old, new)`.
- `watcher_control_panel.py` binds its colour constants from `panel_constants(theme)` at import, using the
  `theme` key in `watcher_settings.json` (normalised by `watcher_settings.normalize_theme`). There are no hex
  literals left in the panel.
- `ControlPanel.apply_skin(name)` switches live: rebinds the module constants, reruns `_apply_theme`,
  `_apply_screen_styles` and `_apply_dash_styles`, then walks every plain Tk widget and remaps colour options,
  Text/Treeview tags and Canvas items through `color_map`. Any failure shows a restart note under the Theme field.

## Control panel layout (1.3.0)

Every page is built inside a `ScrollablePage` by a `_build_<page>_page` method
using shared helpers (`_page_header`, `_control_bar`, `_card_header`, `_tiles`,
`_table`, `_textview`, `_chip`). Each page that changes shape registers a layout
function in `self._page_layouts`; `_rerun_layouts()` calls them when the window
crosses the compact threshold (width >= 1200 and height < 900, the same rule as
the Dashboard). Compact layouts re-pack existing widgets with `in_=` into
two- or three-column frames, so no widget is rebuilt and no state is lost.
Column widths for the Treeviews come from the Figma spec and are set with
`_set_columns`. Settings has a dirty-state model (`SETTINGS_FIELDS`,
`_settings_snapshot`) behind one sticky Save bar outside the scrolled canvas.
`tests/test_screens_layout.py` checks that every page fits at 1000x1024 and
1280x720.

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
