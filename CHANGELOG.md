# Changelog

All notable changes to the Transcription Workbench.

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
