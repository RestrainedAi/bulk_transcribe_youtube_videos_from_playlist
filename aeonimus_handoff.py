"""Aeonimus handoff: how the Aeonimus apps pass files to each other.

See docs/aeonimus-handoff.md for the contract. In short:

* Inbox   (default %USERPROFILE%\\Downloads\\Aeonimus): drop-off only. The URL
  Lister browser extension can only save inside Downloads.
* Library (default %USERPROFILE%\\Documents\\Aeonimus Library): the real shared
  library. Both have link-lists\\, transcripts\\ and comments\\.
* %APPDATA%\\Aeonimus\\handoff.json holds {"inbox": ..., "library": ...} and is
  shared by every app.
* Each app remembers what it imported in its own app data, keyed by full path
  + modified time.

No Tk in this module so it can be unit tested.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

SUBFOLDERS = ("link-lists", "transcripts", "comments")
LINK_LIST_SUFFIXES = (".csv", ".txt", ".tsv")
CONFIG_ENV = "AEONIMUS_HANDOFF_CONFIG"  # tests can point this somewhere else
APP_DATA_NAME = "TranscriptionWorkbench"
VIDEO_ID_IN_NAME = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024  # small files only; no audio


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


def default_inbox() -> Path:
    return _home() / "Downloads" / "Aeonimus"


def default_library() -> Path:
    return _home() / "Documents" / "Aeonimus Library"


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(_home() / "AppData" / "Roaming")
    return Path(base) / "Aeonimus" / "handoff.json"


def app_data_dir() -> Path:
    """This app's own data (never inside the shared folders)."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).parent / APP_DATA_NAME
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(_home() / "AppData" / "Local")
    return Path(base) / "Aeonimus" / APP_DATA_NAME


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _read_config_raw(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@dataclass(frozen=True)
class HandoffConfig:
    inbox: Path
    library: Path

    def inbox_dir(self, sub: str) -> Path:
        return self.inbox / sub

    def library_dir(self, sub: str) -> Path:
        return self.library / sub


def load_config(path: Path | None = None) -> HandoffConfig:
    """Read the shared config; create it with defaults (or fill missing keys) if needed."""
    path = path or config_path()
    raw = _read_config_raw(path)
    inbox = str(raw.get("inbox") or "").strip()
    library = str(raw.get("library") or "").strip()
    changed = False
    if not inbox:
        inbox, changed = str(default_inbox()), True
    if not library:
        library, changed = str(default_library()), True
    if changed or not path.exists():
        try:
            _atomic_write_json(path, {**raw, "inbox": inbox, "library": library})
        except OSError:
            pass  # read-only profile: still work with the defaults
    return HandoffConfig(Path(os.path.expandvars(inbox)), Path(os.path.expandvars(library)))


def save_config(*, inbox: str | Path | None = None, library: str | Path | None = None, path: Path | None = None) -> HandoffConfig:
    """Update the shared config for every app, keeping keys we don't know about."""
    path = path or config_path()
    raw = _read_config_raw(path)
    current = load_config(path)
    raw["inbox"] = str(inbox if inbox is not None else current.inbox)
    raw["library"] = str(library if library is not None else current.library)
    _atomic_write_json(path, raw)
    return load_config(path)


def ensure_folders(cfg: HandoffConfig) -> None:
    for root in (cfg.inbox, cfg.library):
        for sub in SUBFOLDERS:
            (root / sub).mkdir(parents=True, exist_ok=True)


def unique_destination(folder: Path, name: str) -> Path:
    """folder/name, or folder/'name (2).ext', 'name (3).ext', ... if taken. Never overwrites."""
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while True:
        cand = folder / f"{stem} ({n}){suffix}"
        if not cand.exists():
            return cand
        n += 1


def _is_settled(p: Path, min_age_s: float) -> bool:
    try:
        return (time.time() - p.stat().st_mtime) >= min_age_s
    except OSError:
        return False


def move_inbox_link_lists(cfg: HandoffConfig, *, min_age_s: float = 2.0) -> list[Path]:
    """Move (not copy) link lists from inbox\\link-lists into library\\link-lists.

    Files still being written (Chrome's .crdownload, or changed in the last
    couple of seconds) are left for next time. Returns the new library paths.
    """
    src_dir = cfg.inbox_dir("link-lists")
    dst_dir = cfg.library_dir("link-lists")
    dst_dir.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    try:
        entries = sorted(src_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return moved
    for p in entries:
        if not p.is_file() or p.suffix.lower() not in LINK_LIST_SUFFIXES:
            continue
        if not _is_settled(p, min_age_s):
            continue
        dest = unique_destination(dst_dir, p.name)
        try:
            shutil.move(str(p), str(dest))  # keeps the modified time
        except OSError:
            continue
        moved.append(dest)
    return moved


def link_list_files(cfg: HandoffConfig) -> list[Path]:
    """Link lists in the library, newest first."""
    folder = cfg.library_dir("link-lists")
    try:
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in LINK_LIST_SUFFIXES]
    except OSError:
        return []

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(files, key=lambda p: (mtime(p), p.name), reverse=True)


class ImportLedger:
    """What this app already imported, keyed by full path + modified time."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / "imported_link_lists.json")
        self._keys: dict[str, str] = {}
        data = _read_config_raw(self.path)
        keys = data.get("imported")
        if isinstance(keys, dict):
            self._keys = {str(k): str(v) for k, v in keys.items()}

    @staticmethod
    def key_for(p: Path) -> str:
        st = Path(p).stat()
        return f"{os.path.normcase(str(Path(p).resolve()))}|{st.st_mtime_ns}"

    def seen(self, p: Path) -> bool:
        try:
            return self.key_for(p) in self._keys
        except OSError:
            return False

    def mark(self, p: Path) -> None:
        self._keys[self.key_for(p)] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _atomic_write_json(self.path, {"imported": self._keys})


@dataclass(frozen=True)
class Pick:
    path: Path | None
    is_new: bool
    moved: tuple[Path, ...] = ()


def pick_link_list(cfg: HandoffConfig, ledger: ImportLedger, *, move_inbox: bool = True) -> Pick:
    """Newest link list not imported yet; if none is new, the newest one (is_new=False)."""
    moved = tuple(move_inbox_link_lists(cfg)) if move_inbox else ()
    files = link_list_files(cfg)
    for p in files:
        if not ledger.seen(p):
            return Pick(p, True, moved)
    return Pick(files[0] if files else None, False, moved)


def _atomic_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def shared_transcript_name(src: Path) -> str:
    """'<title> [<id>]_2.txt' (a re-run) is shared as '<title> [<id>].txt'."""
    stem = re.sub(r"(\[[A-Za-z0-9_-]{11}\])_\d+$", r"\1", src.stem)
    return stem + src.suffix


def publish_transcript(txt_path: str | Path, json_path: str | Path, cfg: HandoffConfig | None = None) -> list[Path]:
    """Write a finished transcript's .txt and .json into library\\transcripts.

    Only files whose name carries a [videoId] are shared (that's the join key).
    Our own earlier copy of the same transcript is replaced; nothing else is.
    Returns the written paths; never raises.
    """
    out: list[Path] = []
    try:
        cfg = cfg or load_config()
        dest_dir = cfg.library_dir("transcripts")
        for src in (Path(txt_path), Path(json_path)):
            if not src.is_file() or not VIDEO_ID_IN_NAME.search(src.name):
                continue
            if src.stat().st_size > MAX_TRANSCRIPT_BYTES:
                continue
            dest = dest_dir / shared_transcript_name(src)
            _atomic_copy(src, dest)
            out.append(dest)
    except OSError:
        pass
    return out


def library_has_files(root: Path) -> bool:
    for sub in SUBFOLDERS:
        try:
            if any(p.is_file() for p in (root / sub).iterdir()):
                return True
        except OSError:
            continue
    return False


def move_library(old: Path, new: Path) -> tuple[int, int]:
    """Move the files in old's subfolders into new's. Never overwrites (adds a suffix).

    Returns (moved, failed). Empty old subfolders are removed; anything else in
    old is left alone.
    """
    moved = failed = 0
    old, new = Path(old), Path(new)
    if os.path.normcase(str(old.resolve())) == os.path.normcase(str(new.resolve())):
        return 0, 0
    for sub in SUBFOLDERS:
        src_dir, dst_dir = old / sub, new / sub
        try:
            items = [p for p in src_dir.iterdir() if p.is_file()]
        except OSError:
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        for p in items:
            try:
                shutil.move(str(p), str(unique_destination(dst_dir, p.name)))
                moved += 1
            except OSError:
                failed += 1
        try:
            src_dir.rmdir()
        except OSError:
            pass
    return moved, failed
