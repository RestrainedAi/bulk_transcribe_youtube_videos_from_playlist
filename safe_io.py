"""Small filesystem helpers for atomic text/json writes."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def atomic_write_text(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    replace_retries: int = 8,
    replace_delay_seconds: float = 0.05,
) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
        last_error: Exception | None = None
        for attempt in range(max(1, replace_retries)):
            try:
                os.replace(tmp_name, dest)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if attempt >= replace_retries - 1:
                    raise
                time.sleep(replace_delay_seconds * (attempt + 1))
            except OSError as exc:
                last_error = exc
                if attempt >= replace_retries - 1:
                    raise
                time.sleep(replace_delay_seconds * (attempt + 1))
        if last_error is not None:
            raise last_error
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: str | Path, payload: Any, *, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(payload, indent=indent, ensure_ascii=False))
