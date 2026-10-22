"""Optional vocal separation for the Music/lyrics audio mode (v1.4.0).

Pulls the singing out of the backing track before Whisper large-v3 transcribes it,
which is what makes lyrics transcribable. It is OPTIONAL: if the separator is not
installed, Music/lyrics still runs (Whisper's music decode profile on the full mix).

Choice (see docs/AUDIO_MODES.md for the license notes):
  * Tool:  python-audio-separator (MIT)  - pip install "audio-separator[gpu]"
  * Model: Mel-Band RoFormer vocals by Kimberley Jensen (MIT on Hugging Face),
           file name below; downloaded on first use, never bundled.
Not used: Demucs/HTDemucs weights (research-only), Spleeter (weights unlicensed),
Open-Unmix umxl (CC-BY-NC-SA), anything GPL.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

MODEL_FILENAME = "vocals_mel_band_roformer.ckpt"
TOOL = "audio-separator"


def available() -> bool:
    return importlib.util.find_spec("audio_separator") is not None


def separate_vocals(input_path: str, work_dir: str | None = None, model_dir: str | None = None) -> str | None:
    """Path to a vocals-only WAV, or None if separation is unavailable or fails (never raises)."""
    if not available():
        print(f"[music] {TOOL} is not installed; transcribing the full mix with the music profile.")
        return None
    try:
        from audio_separator.separator import Separator  # type: ignore

        out_dir = work_dir or tempfile.mkdtemp(prefix="vocals_")
        kwargs = {"output_dir": out_dir, "output_single_stem": "Vocals", "output_format": "WAV"}
        if model_dir:
            kwargs["model_file_dir"] = model_dir
        sep = Separator(**kwargs)
        sep.load_model(model_filename=MODEL_FILENAME)
        outs = sep.separate(input_path)
        for o in outs or []:
            p = Path(o if os.path.isabs(o) else os.path.join(out_dir, o))
            if p.is_file():
                print(f"[music] Vocals separated: {p}")
                return str(p)
    except Exception as exc:  # noqa: BLE001 - fall back to the full mix
        print(f"[music] Vocal separation failed ({type(exc).__name__}: {exc}); using the full mix.")
    return None
