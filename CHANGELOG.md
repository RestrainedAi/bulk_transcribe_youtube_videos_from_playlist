# Changelog

All notable changes to the Transcription Workbench.

## 1.2.2 (2026-10-17)

- Music no longer comes out mostly empty. Whisper's speech filter treated singing
  as noise and dropped whole verses; the next kept line was then stretched across
  the hole (one line covering 22:51-30:17 on a test video).
- New decode profiles: **auto** (new default), **speech** (the old "robust"),
  **music** (speech filter off, no prompt, no silence skipping, no_speech 0.9) and
  **legacy**. Auto transcribes with speech settings first and, if the result has
  long gaps or stretched lines (or mean no_speech_prob is above 0.8), redoes the
  file once with music settings ("music profile retry" in the log).
- **Use YouTube captions when available** (Settings, on by default): YouTube links
  use the video's own captions (manual first, then original-language auto
  captions; never machine-translated) in seconds, with rate limiting and HTTP 429
  retries. Whisper runs when there are no captions, the fetch fails, or the
  captions fail the missed-audio check. Each segment records its `source`
  (youtube_manual, youtube_auto or whisper).
- **Missed-audio check**: transcripts store `coverage_warnings` in the JSON and in
  `<name>.flags.txt`; the Dashboard shows "Possibly missed audio at mm:ss-mm:ss"
  under Latest transcripts. Limits: lines over 30 s, gaps over 60 s (including the
  start and end of the file); settings `coverage_max_block_s`, `coverage_max_gap_s`.
- New modules `coverage_check.py` and `youtube_captions.py`; tests in
  `tests/test_music_captions.py`.

## 1.2.1 (2026-10-17)

- Dashboard fits at about 1000x1024: compact control bar across the top (Start/Stop
  watcher, Refresh now, Auto-refresh), one-row stat strip (Queue, Completed, Failed,
  Processed, Segment), tighter status, counters and quick-action cards
- Every page scrolls when the window is smaller than its content (scrollbar and
  mouse wheel); the window opens sized to the screen and can be made smaller
- **Open transcripts** button in the Add YouTube / Suno links panel opens the
  library's `transcripts` folder
- **Latest transcripts** list: newest 5 finished transcripts with time;
  double-click (or Open) opens the .txt, **Show in folder** selects it in Explorer
- Dashboard redesigned to the Figma build spec: 48 px header and 176 px sidebar,
  page header with SYSTEM badge (green when running, red when stopped),
  Watcher Control bar, 60 px stat tiles, Watcher status card with a 3-column
  field grid and Runtime footer, Historical Counters and Latest transcripts
  side by side, Terminal Quick Actions in two groups
- Compact layout for wide, short windows (1200+ wide, under 900 tall, e.g.
  1280x720): controls move into the top bar and Latest transcripts / Historical
  Counters move into a right column
- Latest transcripts: click the > to show the file in Explorer, right-click for
  Open / Show in folder / Open transcripts folder
- Auto-refresh has a clear unchecked state ("Polling paused")
- Fixed: the Health field on the Dashboard was hidden behind Last error
- Fixed: the help window's mouse wheel no longer breaks scrolling in the main window

## 1.2.0 (2026-10-17)

- **Import from URL Lister** easy button: moves the newest URL Lister export
  from `Downloads\Aeonimus` into the shared Aeonimus Library, picks the newest
  list not imported yet, queues the downloads and starts the watcher
- Finished transcripts are also saved to the library's `transcripts` folder,
  so the Comment Workbench can show them next to comments
- **Library location** setting (shared with the Comment Workbench) that offers
  to move existing library files
- Suno song links (paste or CSV/TXT): downloads the public song file, checks
  it is real audio, and names files `[suno-<uuid>]`
- Fixed: the watcher now picks up downloads that finish while it is running
- Fixed: file names with square brackets
- New installs find the real Documents and Downloads folders, even when
  Windows has moved them

## 1.1.0 (2026-10-17)

- Renamed the app to **Transcription Workbench** (it showed a placeholder
  design name before) and added a real version number
- **Add YouTube links**: paste one link or load a CSV/TXT list; videos,
  Shorts, live replays and playlists. Audio-only downloads in the background
  into the watch folder, named `<title> [<videoId>]`

## Before 1.1.0

The watcher control panel, watch folder, Whisper transcription, transcript
review and search, and the YouTube caption export tool. No version numbers yet.
