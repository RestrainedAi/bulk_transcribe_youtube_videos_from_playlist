# Aeonimus handoff contract (v1)

How the Aeonimus apps (YouTube URL Lister, Transcription Workbench, Comment
Workbench, and later Director OS) pass files to each other, so nobody has to
copy files by hand. Each app works on its own. The shared folders only make
them work together. This file is the same in every repo.

Last checked against the code: 2026-10-17 (URL Lister 2.6.4, Transcription
Workbench 1.2.0, Comment Workbench 1.1.0).

## Two places: inbox and library

| Place | Default for a new install | What it is |
|---|---|---|
| **Inbox** | `Downloads\Aeonimus` | Drop-off only. Chrome extensions can only save inside Downloads, so the URL Lister saves here. Nothing stays here long term. |
| **Library** | `Documents\Aeonimus Library` | The real shared library. Apps import from here and write their results here. |

The library can live anywhere (Thomas's is `D:\Aeonimus Library`). The
Transcription Workbench finds the real Documents and Downloads folders even
when Windows has moved them (for example into OneDrive).

Both places have the same subfolders. Each app creates any that are missing.

| Subfolder | Contents | Written by |
|---|---|---|
| `link-lists\` | URL Lister CSV exports: `<channel>_<type>_<yyyy-mm-dd>.csv`, or `youtube-url-lister_library_<date>.csv` for the whole library. The link is in the `canonicalUrl` column. Links can be `watch?v=`, `/shorts/`, `/post/` or `playlist?list=`. | URL Lister (into the inbox) |
| `transcripts\` | One finished transcript per video or song: `<title> [<key>].txt` and `.json` | Transcription Workbench (straight into the library) |
| `comments\` | Comment exports, for example `comments_export.csv` | Comment Workbench |

## The join key

The key is whatever is inside the **last** pair of square brackets in a file
name:

- YouTube: the 11-character video ID, for example `Me at the zoo [jNQXAC9IVRw].txt`
- Suno: `suno-<uuid>`, for example `My Song [suno-1f859585-68cb-4440-b38e-3871c7088658].txt`

The Comment Workbench only matches YouTube IDs, because it only holds YouTube
comments. It counts Suno transcripts as "no video ID" and leaves them alone.

## Shared settings file

`%APPDATA%\Aeonimus\handoff.json`, one small file every desktop app reads and
writes:

```json
{
  "version": 1,
  "inbox": "C:\\Users\\<you>\\Downloads\\Aeonimus",
  "library": "D:\\Aeonimus Library"
}
```

- Missing file: the app creates it with the defaults.
- Damaged file: the app uses the defaults. The Comment Workbench also sets the
  bad file aside as `handoff.json.bad`.
- Keep keys you don't know about; another app may have written them. Only
  change the key you mean to change. Write it atomically (temp file, then
  replace).
- Changing the library location in either workbench's settings rewrites this
  file, so the other workbench follows. Apps read it each time they need it.
- When the library moves, the app offers to move the existing `link-lists`,
  `transcripts` and `comments` files. Nothing moves without asking, and
  nothing is overwritten.

The URL Lister cannot read this file (browser extensions cannot read local
files), so it always saves to `Downloads\Aeonimus\link-lists`. Its setting
**Save exports to the Aeonimus handoff folder** (on by default) turns that off
for clients who only use the extension. Then each CSV asks where to save.

## The easy button ("Import from URL Lister")

1. **Move** (not copy) every new file from `inbox\link-lists` into
   `library\link-lists`. If the name is taken, add ` (2)`, ` (3)` and so on.
   Never overwrite. Files that are still downloading or locked are left for
   next time (Chrome's `.crdownload` files never match).
2. Pick the newest file in `library\link-lists` that this app has **not**
   imported yet.
3. Say which file it picked and how many links it has, then start the work:
   - Transcription Workbench: queue the audio downloads and start the watcher.
   - Comment Workbench: load the links into the harvest queue and start the
     harvest.
4. If nothing is new, say so plainly and offer the newest file again.

Each app remembers what it imported in its **own** app data, never in the
shared folders, keyed by full path + modified time. A file that changes later
counts as new again.

| App | Where it remembers imports |
|---|---|
| Transcription Workbench | `%LOCALAPPDATA%\Aeonimus\TranscriptionWorkbench\` |
| Comment Workbench | `%LOCALAPPDATA%\YouTubeCommentsWorkbench\aeonimus_imports.json` (transcript index: `aeonimus_transcripts.json`) |

## Other handoff buttons

- **Transcription Workbench** copies every finished transcript that has a key
  in its name into `library\transcripts` (re-runs named `..._2` are shared
  under the plain name). Files over 50 MB are skipped.
- **Comment Workbench, Attach transcripts**: sweeps `inbox\transcripts` into
  the library, links every `[videoId]` transcript to its video, and reports how
  many matched. Select a comment in **Investigation** to see that video's
  transcript under it (read-only, first 4,000 characters).
- **Open handoff folder** opens the library. **Library location...** moves it.

## Rules

- Small files only in the shared folders: link lists, transcripts, comment
  exports. No audio or video.
- Never delete or overwrite another app's files.
- Messages to clients use plain words, no error codes.
