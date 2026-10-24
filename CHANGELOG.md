# Changelog

All notable changes to the Transcription Workbench.

## 1.4.2 (2026-10-23, test build)

- **Accuracy fix: Whisper's words are kept when the engines disagree.** When a second engine (e.g. granite-2b)
  is selected, it now only replaces a line it fully agrees with. Any line where it heard something different
  keeps Whisper's text, stores the other engine's version as `alt_text`, and is flagged
  ("kept Whisper's words ...") so it shows up for review. Stops wrong-word swaps.
- **More sensitive voice detection** for the normal speech profile: VAD threshold 0.3, min silence 1000 ms,
  speech pad 400 ms, so quiet or overlapping talk isn't cut out.

## 1.4.1 (2026-10-22, test build)

- **Big status light** at the top of the dashboard, readable at a glance: green and pulsing = RUNNING
  (file name and "N of M"), gray = IDLE, amber = DONE with files that need review (count), red = STOPPED
  with a plain reason and what to do. It comes from one snapshot, so nothing else can contradict it.
- **Needs review list** with a count, a Re-run failed button (same re-run logic as 1.3.2) and an
  "Open needs-review folder" link. Failures from earlier runs stay here and never show red during a new run.
- **Details** (phase, PIDs, heartbeat, last error...) is now a collapsible box with a clickable header and
  arrow, closed by default; open/closed is remembered (setting `dashboard_details_open`).
- Removed the duplicate indicators: the old orange/red banner, the status-card dot and badge, and the
  "FAULT" health word (now "CHECK LOG"). Failed counts use amber instead of red.
## 1.4.0 (2026-10-22, test build)

- **Shared data library.** Videos and transcripts can live outside the program folder, in the
  Aeonimus Library shared with the Comment Workbench (`%APPDATA%\Aeonimus\handoff.json`,
  set in Settings > Library). Layout: `transcription\incoming`, `transcription\needs-review`,
  `transcription\transcripts|csv|json|completed-videos|completed-audio`; the shared
  `transcripts\` copy the Comment Workbench reads is unchanged. `migrate_to_library.py` COPIES
  the old folders with SHA-256 checks (dry run by default, `--apply` to copy), keeps the
  originals, and only then points the settings at the library.
- **needs-review.** Content failures (no speech found, no audio track, unreadable audio) are
  moved (copy, hash check, remove) to `needs-review` with a `.needs-review.txt` reason, instead
  of piling up as FAILED. Dashboard and Queue have "Open needs-review folder".
- **Audio mode per job:** Speech (default), Speech over music, Music/lyrics, chosen when you
  re-run from needs-review. See `docs/AUDIO_MODES.md` (incl. license notes).
- **Watcher auto-off:** after a run, when the queue is empty, the watcher turns off unless
  Settings > "Keep watching after run" is ticked (default off).
- **Plain-language status** replaces "LOAD: FAULT": All good / Working / N files need review /
  Engine crashed: see log. Hover for details, click to open the folder or log; clears itself.
- Fixed garbled "â€”" text in the panel (source was double-encoded UTF-8).
- Theme tests no longer assume the Harbor skin; they pass with any saved theme (e.g. Graphite).

## 1.3.1 (2026-10-17)

Four user-selectable skins, from the Figma "Workbench tokens" modes (Palette options page).

- Skins: **Harbor** (the original look, default), **Midnight**, **Graphite** and **Aurora**.
  Each defines the same 20 tokens; the values live in `themes.py` (one dict per skin) and
  match `docs/design/palettes.json`. Every text pair meets 4.5:1 contrast in every skin.
- Every hard-coded colour in the control panel now comes from the skin, including the 1.3.0
  warning tokens, button hover shades, the Monitor card highlight and the Queue row colours.
  Harbor keeps its exact v1.3.0 values; the other skins derive those shades from their tokens.
- Settings has a new **Appearance** group with a **Theme** dropdown. It saves through the
  sticky Save bar as `"theme"` in `watcher_settings.json`; unknown or missing values fall back
  to Harbor.
- The theme applies live on Save (ttk styles are rebuilt and plain Tk widgets, Text/Treeview
  tags and Canvas items are recoloured). If anything can't be recoloured, Settings shows
  "Restart the control panel to finish applying the theme."
- New tests: `tests/test_themes.py`.

## 1.3.0 (2026-10-17)

Redesign of every page except the Dashboard layout (Queue, Monitor, Logs, Live
Transcribe, Handoff, Search, Review, Settings), following the Figma screens
handoff. Every page fits at 1000x1024 and at 1280x720 with nothing clipped;
Settings scrolls by design and keeps its Save bar on screen.

- New colour tokens: warning `#ffb84d`, warning surface `#2b2112`, disabled text
  `#6b7480`. "Possibly missed audio" is now an amber strip on the Dashboard
  (Latest transcripts rows with a warning get a marker) and on Review.
- **Queue**: page header with Stop / Start watcher; four tiles; one **Add media**
  card (Watch folder, From the URL Lister, Add YouTube / Suno links); Active
  Queue with a "Now transcribing" progress row, Pause queue, Move up / down and
  Skip selected; Recently Completed. **Open watch folder** replaces the old
  "Browse files" and "New task" buttons (both only opened the watch folder).
- **Monitor**: CPU / Memory / GPU / Live health tiles, processing unit and
  disk / telemetry cards.
- **Logs**: status chip, Open log file, Unified log, Copy for support, Refresh,
  a filter box with a clear button, and lines coloured by level.
- **Live Transcribe**: status chip (DECODING / IDLE / PAUSED), progress bar,
  Open raw stream, and a Word wrap toggle; newest segment highlighted.
- **Handoff**: primary destination, read-only endpoint diagnostics, default
  formatting, and read-only flag chips that show real settings only
  (ASK_EACH_TIME, LLM_API_FIRST, RESUME_ON_START, SKIP_DUPLICATES, and the
  built-in CPU_RETRY_ON_CRASH). Flags with no real setting were dropped.
- **Search**: Play at time / Open transcript stay disabled until a result is
  selected; clearer "0 matches" message; index status next to the results.
- **Review**: transcript picker, amber missed-audio strip with **Jump to spot**,
  missed-audio and stretched-line rows in the flags table, "N of M reviewed",
  and an editor whose buttons are disabled when nothing editable is selected.
- **Settings**: cards for Transcription, Watcher, Handoff, Advanced and Folders;
  one decode-profile dropdown (auto / speech / music / legacy) with help text;
  an **Advanced overrides** section with the missed-audio limits (max block
  30 s, max gap 60 s); Browse... buttons for folders; number fields checked
  before saving; one sticky Save bar with unsaved-change count, Discard and
  Save changes. **Save** now also applies a changed Library location (the old
  "Use this location" button).
- Version label reads `app_version.py` (1.3.0).
- New tests: `tests/test_screens_layout.py` (fit at both sizes, column widths,
  Settings dirty state and validation, Handoff flags, Review strip and editor
  states, Search buttons, version label).

### Proposed (not built in 1.3.0)

- Logs level filter (Info / Warn / Error / Debug chips).
- Review Prev / Next buttons.
- Monitor trends chart.
- In-app Documentation page (the sidebar link still opens the docs folder).
- "Re-run with music profile" button on the Review missed-audio strip.

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
