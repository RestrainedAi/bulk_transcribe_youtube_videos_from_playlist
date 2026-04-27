"""Materialize python files from the yt_transcript_export_impl bundle (FILE markers + fenced code)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "yt_transcript_export_impl.md"


def main() -> None:
    text = MD.read_text(encoding="utf-8")
    tick = chr(96)
    fence_py = tick * 3 + "python"
    fence_end = tick * 3
    pat = re.compile(
        r"<!-- FILE: ([^\n]+) -->\s*" + fence_py + r"\r?\n(.*?)" + fence_end,
        re.DOTALL,
    )
    for m in pat.finditer(text):
        rel = m.group(1).strip().replace("/", Path.sep)
        out = ROOT / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        body = m.group(2).rstrip() + "\n"
        out.write_text(body, encoding="utf-8")
        print("wrote", out.relative_to(ROOT), file=sys.stderr)
    print("done", file=sys.stderr)


if __name__ == "__main__":
    main()
