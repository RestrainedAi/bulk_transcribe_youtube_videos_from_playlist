# Bulk Transcribe Videos — Project Overview

## What It Is

A self-hosted, GPU-accelerated video transcription pipeline running entirely on your local machine.
Drop a video into a watch folder (or point it at a YouTube playlist) and get back a plain-text
transcript, a CSV metadata table, and a full JSON segment file — then hand the result off to
NotebookLM, ChatGPT, Claude, Gemini, Grok, or a local LM Studio model in one click.

---

## Core Components

| File | Role |
|---|---|
| `bulk_transcribe_youtube_videos_from_playlist.py` | Transcription engine: Whisper model management, audio extraction, YouTube download, output generation |
| `watch_and_transcribe.py` | Folder watcher, queue manager, status tracker, stall detector |
| `watch_transcribe_worker.py` | Isolated worker process — one per video, writes heartbeat JSON so the watcher can kill stalls |
| `watcher_control_panel.py` | Tkinter dark-theme GUI dashboard (Pipeline Watcher) |
| `post_transcript_handoff.py` | Builds handoff bundles and dispatches to AI providers (clipboard + browser or direct API) |
| `watcher_settings.py` | JSON-backed settings with defaults; loaded by both watcher and control panel |
| `watcher_telemetry.py` | Samples CPU, RAM, and GPU (via `nvidia-smi`) into a rolling JSONL resource log |
| `youtube_transcript_export.py` | Fetches official YouTube captions via YouTube Data API v3 + `youtube-transcript-api` |
| `youtube_transcript_panel.py` | Tkinter panel for the YouTube caption export workflow |

---

## Key Features

### Transcription Engine
- **Model**: `faster-whisper` `large-v3` via `ctranslate2`
- **Device selection**: auto-detects CUDA GPU → falls back to CPU
- **CUDA runtime**: nvidia pip wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) installed in venv — no system CUDA required
- **Audio duration**: measured via `ffprobe` (no pydub/audiooop dependency)
- **Output formats**: plain `.txt`, segment `.csv` (with timestamps + confidence), full `.json`

### Folder Watcher
- Watches `watch_incoming/` for any new video file (mp4, mkv, mov, webm, avi, m4v, mpeg, wmv, flv)
- **File stability check**: polls size until stable for 1.5s before starting — safe for large file copies
- **Duplicate stem handling**: auto-appends `_1`, `_2` etc. when output names collide
- **Stall detection**: kills any worker silent for longer than `stall_timeout_seconds` (default 15 min)
- **Concurrent jobs**: configurable via `max_concurrent_jobs` (default 1, respects physical core count)
- **On completion**: stores outputs by type in repo-root folders such as `completed_videos/`, `completed_transcripts/`, `completed_csv/`, and `completed_json/`

### Control Panel (Pipeline Watcher GUI)
- Live dashboard: phase, status, heartbeat, current file, segment progress, active jobs, counters
- Queue, Monitor, Logs, Handoff, and Settings tabs
- Start / Stop watcher with one button
- Quick-action buttons: open watch folder, open videos/transcripts/CSV/JSON folders, clear error
- Single-instance enforcement via loopback socket lock

### Post-Transcript Handoff
Builds a bundle (title + source path + transcript text), then dispatches to:
- **NotebookLM** — copies bundle to clipboard, stages `last_handoff.txt`, opens browser
- **ChatGPT / Gemini / Claude / Grok** — copies to clipboard, opens provider in browser
- **LM Studio** — local OpenAI-compatible endpoint
- **API-first mode** — when `handoff_try_api_first = true`, calls the LLM API directly for an
  inline summary before opening the browser; result saved to `staged_for_notebooklm/last_api_summary.txt`

Supported API keys (settings or env vars): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY`, `XAI_API_KEY`

### YouTube Caption Export
- Accepts video URLs, video IDs, or playlist URLs
- Fetches official captions via YouTube Data API v3; falls back to `youtube-transcript-api`
- Concurrency-controlled (`youtube_transcript_concurrency`, default 2)
- Optional plain `.txt` save alongside WebVTT

### Telemetry
- 5-second resource snapshots written to `watch_incoming/watch_resource.jsonl`
- Captures: CPU %, RAM used/total, GPU utilization %, GPU VRAM used/total (when `nvidia-smi` available)
- Three log profiles: `standard`, `tuning`, `debug`

---

## Settings (`watcher_settings.json`)

| Key | Default | Description |
|---|---|---|
| `watch_dir` | `watch_incoming` | Folder to watch |
| `completed_video_dir` / `completed_dir` | `completed_videos` | Where finished videos land |
| `stall_timeout_seconds` | `900` | Kill worker if no heartbeat for this long |
| `heartbeat_seconds` | `5` | Status JSON refresh interval |
| `max_concurrent_jobs` | `1` | Parallel transcription workers |
| `start_scan_existing` | `true` | Process files already in watch folder on start |
| `default_post_transcript_destination` | `notebooklm` | Default handoff target |
| `handoff_try_api_first` | `false` | Call LLM API for summary before opening browser |
| `handoff_llm_system_prompt` | *(summarize prompt)* | System prompt sent to LLM in API-first mode |
| `lm_studio_base_url` | `http://localhost:1234/v1` | LM Studio endpoint |

---

## Requirements (`requirements.txt`)

```
faster-whisper          # Whisper inference via CTranslate2
nvidia-cublas-cu12      # CUDA 12 cuBLAS runtime (pip wheel, no system install needed)
nvidia-cudnn-cu12       # CUDA 12 cuDNN runtime
psutil                  # CPU/process/memory info
pytubefix               # YouTube video/playlist download
pandas                  # CSV/DataFrame output
fastapi                 # (reserved for future API surface)
spacy                   # Optional sentence splitting
numba                   # CUDA detection helper
openai                  # Handoff to ChatGPT / Grok / LM Studio
ruff                    # Linter
tqdm                    # Progress bars
watchdog                # File system events
google-api-python-client  # YouTube Data API v3
youtube-transcript-api  # YouTube caption fallback
```

---

## Launchers

| File | Purpose |
|---|---|
| `Open_Watcher_Control_Panel.cmd` | Activates venv and launches the Tkinter control panel |
| `Open_Youtube_Caption_Export.cmd` | Launches the YouTube caption export panel |
| `Create_Watcher_Control_Panel_Shortcut.ps1` | Creates a desktop shortcut with app icon (run once from repo root) |
| `.archive/bootstrap/Create_Desktop_Shortcut.vbs` | Legacy shortcut helper (hardcoded paths; kept for reference only) |

---

## Things To Do

### ✅ Completed

- [x] **Audio-only file support** — `.mp3`, `.m4a`, `.wma`, `.flac`, `.ogg`, `.wav` added to `MEDIA_EXTENSIONS`
- [x] **GPU crash auto-retry on CPU** — watcher detects Windows crash exit codes (0xC0000005 etc.) and
      automatically respawns the worker on CPU before marking failure
- [x] **CPU fallback model switched to `medium`** — GPU still uses `large-v3`; CPU fallback now uses `medium`
      (~5x faster on CPU); confirmed working end-to-end
- [x] **Unicode encode errors** — all file writes now use `encoding='utf-8'`; `sys.stdout.reconfigure(utf-8)`
      added to worker and engine; `ensure_ascii=False` on JSON/CSV outputs
- [x] **CUDA runtime pip wheels** — `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` installed; no system CUDA needed
- [x] **Compute type / VRAM pressure** — switched GPU mode to `int8_float16`, `beam_size=5`;
      4-attempt fallback cascade: gpu_vad → gpu_no_vad → cpu_medium → cpu_medium_auto
- [x] **Removed pydub dependency** — audio duration now measured via `ffprobe` subprocess
- [x] **Terminal window suppressed** — `start_watcher.ps1` uses `-WindowStyle Hidden`; Whisper output
      streamed to `watch_incoming/whisper_live.log`
- [x] **Live Transcribe panel** — dedicated nav tab shows `whisper_live.log` tail in real time
- [x] **Real-time log refresh** — Logs and Live Transcribe pages force 1s refresh
- [x] **Segment (s) tile on Dashboard** — live segment-end counter added as a 6th metric tile on the
      main dashboard; the clearest indicator the job is alive and moving
- [x] **Output directory unification** — `completed_videos/`, `completed_transcripts/`, `completed_csv/`, `completed_json/`
- [x] **Git protection** — all output dirs, watch folders, and media files locked out of git tracking
      (`.gitignore` + `git rm --cached`); previously-tracked MP4s and transcripts removed from index
- [x] **Startup crash fix** — `is_video_path` → `is_media_path`
- [x] **Atomic file writes** — `safe_io.py`; prevents status/settings corruption on Windows
- [x] **Kinetic Console color palette** — all color constants matched to design guide tokens
- [x] **Fonts wired** — Bahnschrift / Segoe UI Variable / Cascadia Mono
- [x] **Desktop shortcut** — `Create_Watcher_Control_Panel_Shortcut.ps1` (legacy VBS in `.archive/bootstrap/`)
- [x] **Repo cleanup** — legacy files in `.archive/`; `stop_watcher.ps1` hardened

---

### 🔴 High Priority
- [ ] **`_apply_theme()` full color pass** — `ttk.Style` entries in the control panel still reference the
      old accent values; the sidebar active-state highlight, button backgrounds, and treeview colors
      need a second pass to use the new Kinetic Console tokens throughout
- [ ] **Configurable Whisper model size** — `large-v3` is hardcoded; expose model choice in Settings tab
      (small / medium / large-v2 / large-v3) so users can trade speed for accuracy
- [ ] **Update Anthropic handoff model** — `post_transcript_handoff.py` calls `claude-3-5-haiku-20241022`
      via raw `urllib`; upgrade to the Anthropic SDK and use a current model (`claude-haiku-4-5` or later)
- [ ] **SpaCy environment fix** — SpaCy is disabled with a comment saying it crashes the Windows venv;
      diagnose and repair so sentence splitting can use the NLP model instead of the regex fallback
- [ ] **`completed_audio` not wired** — `output_paths.py` defines `completed_audio/` but the YouTube audio
      downloader still writes to the old path; wire it through `COMPLETED_AUDIO_DIR`

### 🟡 Control Panel UX
- [ ] **Transcription progress bar** — worker writes `last_segment_end`; divide by total audio duration
      to show a real % progress bar in the dashboard instead of just a raw timestamp
- [ ] **Windows toast notification** — fire a system toast when a transcription completes
      (via `plyer` or `win10toast`) so you don't have to watch the dashboard
- [ ] **Drag-and-drop into watch folder** — let users drag video files directly onto the panel window
      instead of navigating to the watch folder in Explorer
- [ ] **Running cost display** — the engine calculates per-minute Whisper cost; accumulate and show
      session total on the dashboard
- [ ] **Transcript preview pane** — show the last N lines of the in-progress transcript inside the panel
      (could replace or augment the Live Transcribe tab)
- [ ] **Log filter persistence** — the Logs page filter field resets every restart; save the last filter
      to settings or session state

### 🟢 Output & Integration
- [ ] **SRT / WebVTT subtitle output** — generate a subtitle file alongside the `.txt` so the transcript
      can be loaded in a media player or video editor
- [ ] **Speaker diarization** — integrate `pyannote.audio` to label segments by speaker
      (requires HuggingFace token; gate behind an optional feature flag)
- [ ] **Webhook / email notification** — POST a JSON payload (or send email) on completion/failure,
      useful when running headless overnight
- [ ] **Export session report** — generate an HTML or CSV summary of all completed transcriptions
      (filename, duration, cost, timestamp) from the telemetry JSONL

### ⚙️ Reliability & Ops
- [ ] **Remove `fastapi` from requirements** — it's in `requirements.txt` but nothing imports it;
      either wire up a REST API surface (status, queue, trigger jobs) or remove the dependency
- [ ] **Pin dependency versions** — `requirements.txt` has no version pins; a `requirements.lock.txt`
      with exact pinned versions would make reinstalls reproducible and safe
- [ ] **Scheduled runs** — add a "run at" time picker in Settings so transcription batches can be
      deferred to off-peak hours
- [ ] **`whisper_live.log` rotation** — the log appends indefinitely; add size-based rotation so it
      doesn't grow unbounded across many sessions
