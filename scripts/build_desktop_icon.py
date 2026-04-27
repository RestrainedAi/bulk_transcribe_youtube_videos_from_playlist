"""Build assets/watcher_desktop_icon.ico from assets/watcher_desktop_icon.png.

Requires: pip install Pillow
Run from repo root: venv\\Scripts\\python scripts\\build_desktop_icon.py
"""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image
except ImportError as e:
    raise SystemExit("Install Pillow: pip install Pillow") from e

ROOT = Path(__file__).resolve().parents[1]
PNG = ROOT / "assets" / "watcher_desktop_icon.png"
ICO = ROOT / "assets" / "watcher_desktop_icon.ico"


def main() -> None:
    if not PNG.is_file():
        raise SystemExit(f"Missing source PNG: {PNG}")
    im = Image.open(PNG).convert("RGBA")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    icons = [im.resize(s, Image.Resampling.LANCZOS) for s in sizes]
    ICO.parent.mkdir(parents=True, exist_ok=True)
    icons[0].save(
        ICO,
        format="ICO",
        sizes=[(x.width, x.height) for x in icons],
        append_images=icons[1:],
    )
    print(f"Wrote {ICO} ({ICO.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
