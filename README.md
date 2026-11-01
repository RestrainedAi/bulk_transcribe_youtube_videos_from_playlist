# Transcription Workbench

A Windows desktop app by Aeonimus that turns videos, YouTube links and Suno
songs into text transcripts on your own computer, using Whisper on the
graphics card. It is part of the Aeonimus workflow: it imports link lists from
the YouTube URL Lister and shares finished transcripts with the Comment
Workbench.

**Current version: 1.5.2** (see [CHANGELOG.md](CHANGELOG.md)). Pick a skin (Harbor, Midnight, Graphite or Aurora) in Settings > Appearance. The version
shows in the window title and the sidebar, and lives in `app_version.py`.

## Who it's for

Clients who need many transcripts at once: researchers, creators and teams
working through whole channels or playlists, without sending audio to a cloud
service.

## Install and launch

1. Python 3.12 with a virtual environment in `venv\`:
   `python -m venv venv` then `venv\Scripts\pip install -r requirements.txt`.
2. An NVIDIA graphics card is recommended (falls back to CPU). `ffmpeg` must be
   on the PATH; the launcher adds the WinGet copy if it is installed.
3. Launch with `Open_Watcher_Control_Panel.vbs` (no console window) or
   `Open_Watcher_Control_Panel.cmd`. `Transcription Control Panel.vbs` is the
   same launcher with a clearer name. `Create_Watcher_Control_Panel_Shortcut.ps1`
   makes a desktop shortcut named **Transcription Workbench** (it removes an old
   "Transcription Watcher Control Panel" shortcut if one is there).

The launcher runs a quick self-check (`startup_selfcheck.py`) and then
`watcher_control_panel.py`. Logs go to `logs\`.

## How to use it

- **Watch folder**: drop video or audio files into `watch_incoming\` and press
  **Start watcher**. Each file is transcribed one after another.
- **Add YouTube / Suno links** (Dashboard): paste one link and press Enter
  (or **Add link**), or click
  **Load CSV / TXT** for a list. Videos, Shorts, live replays and playlists
  work. Only the audio is downloaded, into the watch folder, named
  `<title> [<videoId>]`.
- **Open transcripts** (next to the status line in that panel) opens the
  library's `transcripts` folder. **Latest transcripts** below it lists the
  newest 5 finished transcripts; double-click one to open it, or use **Show in
  folder**.
- Every page fits on screen at about 1000x1024 and at 1280x720; pages scroll
  (scrollbar or mouse wheel) when the window is smaller. Settings always
  scrolls and keeps its Save bar at the bottom.
- **Queue** page: **Open watch folder**, **Import from URL Lister**, links,
  Pause queue, Move up / down and Skip selected. **Review** shows an amber
  strip when a transcript has possibly missed audio; **Jump to spot** selects
  it.
- **Suno songs**: paste a link like `https://suno.com/song/<uuid>` or include
  it in a CSV. Files are named `<title> [suno-<uuid>]`. Sung vocals come out
  rougher than speech.
- **Import from URL Lister** (the Aeonimus easy button, on the Dashboard):
  moves the newest URL Lister export from `Downloads\Aeonimus\link-lists` into
  the shared library, says which file it picked and how many links it has,
  queues the downloads and starts the watcher.
- **Library location** is in Settings (applied when you press **Save
  changes**); **Open handoff folder** is next to the easy button. Changing the library here also changes it for the Comment
  Workbench.
- Search, review and correct transcripts, and send a finished transcript to
  NotebookLM, LM Studio or an AI service (optional, your own keys).

- **Music and songs**: the default decode profile, **auto**, notices when a
  transcript skipped audio (long gaps or one line stretched over minutes) and
  redoes the file once with **music** settings. Pick **music** in Settings to
  always use them (slower: about 2 minutes for a 33-minute song).
- **Use YouTube captions when available** (Settings, on by default): YouTube links
  are transcribed from the video's own captions in seconds; the audio is only
  downloaded and run through Whisper when there are none.
- **Possibly missed audio** warnings appear under Latest transcripts and in each
  transcript's `.flags.txt`.

Private or members-only videos cannot be downloaded; the app says so plainly.

## Where data lives

| What | Where |
|---|---|
| Incoming files | `watch_incoming\` |
| Finished videos, audio, transcripts, CSV, JSON | `completed_videos\`, `completed_audio\`, `completed_transcripts\`, `completed_csv\`, `completed_json\` (in the Aeonimus Library once moved there; see `data_library.py`, `migrate_to_library.py`) |
| Shared copies of transcripts | `<library>\transcripts\` (Thomas: `D:\Aeonimus Library\transcripts`) |
| Settings | `watcher_settings.json` in the app folder (holds optional API keys: never share this file) |
| Shared handoff settings | `%APPDATA%\Aeonimus\handoff.json` |
| What it already imported | `%LOCALAPPDATA%\Aeonimus\TranscriptionWorkbench\` |

Handoff contract: [docs/aeonimus-handoff.md](docs/aeonimus-handoff.md).

## Transcribe one file by hand (worker)

The watcher runs `watch_transcribe_worker.py` once per file. You can run it
yourself:

```
venv\Scripts\python watch_transcribe_worker.py --input "C:\clips\talk.mp4" --stem talk --progress "C:\clips\talk.progress.json"
```

Add `--output-dir "C:\clips\out"` (1.5.2) to put that job's `.txt`, `.csv` and
`.json` in one folder of your choice; it is created if missing. Without it the
files go to the usual `completed_*` folders. It only affects that one run:
`watcher_settings.json` and the watcher are not changed.

## Tests

`venv\Scripts\python -m pytest tests`

## More docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): modules, data flow, dependencies and licenses
- [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md): the original open-source
  project's README (this app started as a fork of
  `bulk_transcribe_youtube_videos_from_playlist`, MIT license)
- [docs/yt_transcript_export_impl.md](docs/yt_transcript_export_impl.md): YouTube caption export tool
