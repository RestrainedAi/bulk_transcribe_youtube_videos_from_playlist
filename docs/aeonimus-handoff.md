# Aeonimus handoff contract (v1)

How the Aeonimus apps (YouTube URL Lister, Transcription Workbench, Comment
Workbench) hand files to each other. Every app follows this exactly. Each app
works on its own; the shared folders only make the apps work together.

## Two places: inbox and library

| Place | Default | What it is |
|---|---|---|
| **Inbox** | `%USERPROFILE%\Downloads\Aeonimus` | Drop-off only. Chrome extensions can only save inside Downloads, so the URL Lister saves here. Nothing is kept here long term. |
| **Library** | `%USERPROFILE%\Documents\Aeonimus Library` | The real shared library. Apps import from here and write their results here. |

Both have the same subfolders. Each app creates any that are missing:

| Subfolder | Contents | Written by |
|---|---|---|
| `link-lists\` | URL Lister CSV exports, named `<channel>_<type>_<yyyy-mm-dd>.csv` (same columns as a normal URL Lister CSV export) | URL Lister (into the inbox) |
| `transcripts\` | One finished transcript per video: `<title> [<videoId>].txt` and `.json` | Transcription Workbench (straight into the library) |
| `comments\` | Comment Workbench exports | Comment Workbench |

The 11-character YouTube video ID in square brackets, for example
`Me at the zoo [jNQXAC9IVRw].txt`, is the join key between apps.

## Shared settings file

`%APPDATA%\Aeonimus\handoff.json`, one small file every app reads and writes:

```json
{
  "inbox": "C:\\Users\\<you>\\Downloads\\Aeonimus",
  "library": "C:\\Users\\<you>\\Documents\\Aeonimus Library"
}
```

- If the file is missing or unreadable, the app uses the defaults above and
  creates the file with them.
- Changing the inbox or library location in any app's settings rewrites this
  file, so every app follows. Apps read it each time they need it; they do not
  cache it across runs.
- Write it atomically (write a temp file, then replace) and keep unknown keys.
- When the library location changes, the app offers to move the existing
  library files (`link-lists`, `transcripts`, `comments`) to the new place.
  Nothing is moved without asking, and nothing is overwritten.

The URL Lister cannot read this file (browser extensions cannot read local
files), so it always saves to `Downloads\Aeonimus\link-lists`. Its setting
"Save exports to the Aeonimus handoff folder" (on by default) can turn that off,
for clients who do not use the other apps; then each CSV asks where to save.

## The easy button

Each workbench has one button that imports from the other apps:

1. Move (not copy) every new file from `inbox\link-lists` into
   `library\link-lists`. If a file with the same name is already there, add a
   suffix (`name (2).csv`, `name (3).csv`, ...) instead of overwriting.
2. Pick the newest file in `library\link-lists` that this app has **not**
   imported yet (newest modified time first).
3. Show which file it picked (name and link count), import it from the library
   copy, and start the work (for the Transcription Workbench: queue the
   downloads and start the watcher).
4. If nothing is new, say so in plain words and offer to import the newest file
   again.

Each app remembers what it already imported in its **own** app data, never in
the shared folders, keyed by full path + modified time. A file that is changed
later counts as new again.

## Rules

- Small files only in the shared folders: link lists, transcripts, comment
  exports. No audio or video.
- Never delete or overwrite another app's files.
- Messages to clients use plain words, no error codes.
