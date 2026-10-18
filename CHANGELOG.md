# Changelog

All notable changes to the Transcription Workbench.

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
