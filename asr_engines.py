"""Optional extra ASR engines (Voxtral, Granite) and the Voxtral cross-check.

The watcher's own venv never imports torch. The extra engines run in a separate
process (asr_eval_worker.py under venv_asr_eval). This module only contains
pure-Python helpers: engine selection, a VRAM check, word alignment against the
Whisper segments (so timestamps keep working), and confidence flags.
Everything here fails soft: on any problem the Whisper result is kept.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_MODELS_DIR = r"E:\.lmstudio\models\ASR"
DEFAULT_ENGINE = "whisper"

# name -> (model folder, VRAM needed in GB, human label)
ENGINES: dict[str, tuple[str | None, float, str]] = {
    "whisper": (None, 0.0, "faster-whisper (default)"),
    "voxtral-mini-3b": ("Voxtral-Mini-3B-2507", 11.0, "Voxtral Mini 3B"),
    "granite-2b": ("granite-speech-3.3-2b", 7.0, "Granite Speech 3.3 2B"),
}
CROSSCHECK_ENGINE = "voxtral-mini-3b"
WORKER_TIMEOUT_SECONDS = 3 * 3600

LOW_LOGPROB = -0.6
HIGH_NO_SPEECH = 0.5
HIGH_COMPRESSION = 2.4
LOW_AGREEMENT = 0.8


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def resolve_engine(settings: dict | None = None) -> str:
    data = settings or {}
    raw = str(os.environ.get("ASR_ENGINE") or data.get("asr_engine") or DEFAULT_ENGINE).strip().lower()
    return raw if raw in ENGINES else DEFAULT_ENGINE


def resolve_crosscheck(settings: dict | None = None) -> bool:
    env = os.environ.get("ASR_CROSSCHECK")
    if env is not None and env != "":
        return _truthy(env)
    return _truthy((settings or {}).get("asr_crosscheck", False))


def eval_python(settings: dict | None = None) -> Path:
    raw = (settings or {}).get("asr_eval_python") or ""
    return Path(raw) if raw else REPO_ROOT / "venv_asr_eval" / "Scripts" / "python.exe"


def models_dir(settings: dict | None = None) -> Path:
    return Path((settings or {}).get("asr_models_dir") or DEFAULT_MODELS_DIR)


def free_vram_gb() -> float | None:
    """Free memory on GPU 0 in GB via nvidia-smi, or None if it can't be read."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        return round(int(out.stdout.strip().splitlines()[0]) / 1024, 2)
    except Exception:
        return None


def check_engine_ready(engine: str, settings: dict | None = None, free_gb: float | None = None) -> str | None:
    """Return None when the engine can run, otherwise a clear reason why not."""
    if engine == "whisper":
        return None
    folder, need_gb, label = ENGINES[engine]
    py = eval_python(settings)
    if not py.is_file():
        return f"{label}: the ASR venv was not found at {py}."
    mdir = models_dir(settings) / str(folder)
    if not (mdir / "config.json").is_file():
        return f"{label}: model files were not found in {mdir}."
    if free_gb is None:
        free_gb = free_vram_gb()
    if free_gb is None:
        return f"{label}: could not read free GPU memory (nvidia-smi unavailable)."
    if free_gb < need_gb:
        return (f"{label}: needs about {need_gb:.1f} GB of free GPU memory but only {free_gb:.1f} GB is free. "
                "Close other GPU apps (LM Studio, DaVinci Resolve, games) and retry.")
    return None


def _norm(token: str) -> str:
    return re.sub(r"[^a-z0-9']", "", token.lower())


def align_text_to_segments(segments: list[dict], alt_text: str) -> tuple[list[list[str]], list[float], list[list[str]]]:
    """Map an engine's plain text onto Whisper's timed segments.

    Returns, per segment: the alt-engine words that belong to it, the share of Whisper
    words the alt engine agreed with (0..1), and short notes describing disagreements.
    """
    w_tokens: list[str] = []
    owner: list[int] = []
    for i, seg in enumerate(segments):
        for tok in str(seg.get("text", "")).split():
            n = _norm(tok)
            if n:
                w_tokens.append(n)
                owner.append(i)
    alt_raw = [t for t in alt_text.split() if _norm(t)]
    alt_norm = [_norm(t) for t in alt_raw]
    words: list[list[str]] = [[] for _ in segments]
    notes: list[list[str]] = [[] for _ in segments]
    matched = [False] * len(w_tokens)
    if not segments:
        return words, [], notes
    if not w_tokens:
        if alt_raw:
            words[0] = list(alt_raw)
        return words, [0.0] * len(segments), notes
    sm = difflib.SequenceMatcher(None, w_tokens, alt_norm, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                matched[i1 + k] = True
                words[owner[i1 + k]].append(alt_raw[j1 + k])
            continue
        if i2 > i1:
            segs_hit = owner[i1:i2]
            span = j2 - j1
            for k in range(span):  # spread replacement words across the replaced Whisper words
                words[segs_hit[min(len(segs_hit) - 1, k * len(segs_hit) // max(1, span))]].append(alt_raw[j1 + k])
            target = segs_hit[0]
        else:
            target = owner[i1 - 1] if i1 > 0 else owner[0]
            words[target].extend(alt_raw[j1:j2])
        notes[target].append(f"whisper '{' '.join(w_tokens[i1:i2])}' vs alt '{' '.join(alt_norm[j1:j2])}'")
    agreement = []
    for i in range(len(segments)):
        idx = [k for k, o in enumerate(owner) if o == i]
        agreement.append(round(sum(matched[k] for k in idx) / len(idx), 2) if idx else 0.0)
    return words, agreement, notes


def segment_flags(seg: dict, agreement: float | None = None, notes: list[str] | None = None) -> list[str]:
    why = []
    if float(seg.get("avg_logprob", 0) or 0) < LOW_LOGPROB:
        why.append(f"low avg_logprob {seg.get('avg_logprob')}")
    if float(seg.get("no_speech_prob", 0) or 0) > HIGH_NO_SPEECH:
        why.append(f"high no_speech {seg.get('no_speech_prob')}")
    if float(seg.get("compression_ratio", 0) or 0) > HIGH_COMPRESSION:
        why.append(f"high compression {seg.get('compression_ratio')}")
    if agreement is not None and agreement < LOW_AGREEMENT:
        why.append(f"low model agreement {agreement}")
    if notes:
        why.append("models disagree: " + "; ".join(notes)[:300])
    return why


def _fmt(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def write_flag_summary(path: str | Path, segments: list[dict], header: str) -> None:
    lines = [header, ""]
    flagged = [s for s in segments if s.get("flags")]
    for s in flagged:
        lines.append(f"[{_fmt(float(s['start']))}] {str(s['text']).strip()[:120]}  <- {s['flags']}")
    if not flagged:
        lines.append("No low-confidence spots found.")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_worker(engine: str, audio_path: str, settings: dict | None = None) -> dict:
    """Run asr_eval_worker.py in the ASR venv; returns its JSON result or raises."""
    folder = ENGINES[engine][0]
    with tempfile.TemporaryDirectory(prefix="asr_engine_") as tmp:
        wav = os.path.join(tmp, "audio16k.wav")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000", wav],
                       check=True, timeout=1800)
        out_json = os.path.join(tmp, "result.json")
        cmd = [str(eval_python(settings)), str(REPO_ROOT / "asr_eval_worker.py"), "--engine", engine,
               "--model-dir", str(models_dir(settings) / str(folder)), "--audio", wav, "--out", out_json]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=WORKER_TIMEOUT_SECONDS, env=env)
        if proc.returncode != 0 or not os.path.isfile(out_json):
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            raise RuntimeError(f"{engine} worker failed (exit {proc.returncode}): {' | '.join(tail)}")
        return json.loads(Path(out_json).read_text(encoding="utf-8"))


def apply_engine_pipeline(audio_path: str, stem: str, segments: list[dict], settings: dict | None,
                          flags_dir: str, runner=run_worker, free_gb_func=free_vram_gb) -> list[dict]:
    """Post-process Whisper segments with the selected engine and/or the Voxtral cross-check.

    Always returns usable segments. Every segment gets 'engine' and 'flags'; when another
    engine ran, also 'agreement'. A <stem>.flags.txt summary is written next to the JSON.
    """
    engine = resolve_engine(settings)
    crosscheck = resolve_crosscheck(settings)
    used = "whisper"
    note = ""
    alt_engine = engine if engine != "whisper" else (CROSSCHECK_ENGINE if crosscheck else None)
    result = None
    if alt_engine:
        reason = check_engine_ready(alt_engine, settings, free_gb_func())
        if reason:
            note = f"{reason} Kept the faster-whisper result."
        else:
            try:
                print(f"ASR engine: running {ENGINES[alt_engine][2]} ...", flush=True)
                result = runner(alt_engine, audio_path, settings)
            except Exception as exc:  # never crash the job because of an optional engine
                note = f"{ENGINES[alt_engine][2]} failed: {exc}. Kept the faster-whisper result."
        if note:
            print(f"ASR engine WARNING: {note}", flush=True)
    out: list[dict] = []
    if result is not None and not segments and result.get("chunks"):
        # No Whisper timing to align to: use chunk offsets as segment times.
        for ch in result["chunks"]:
            if str(ch.get("text", "")).strip():
                out.append({"start": round(ch["start"], 2), "end": round(ch["end"], 2), "text": " " + ch["text"].strip(),
                            "engine": alt_engine, "timing": "chunk"})
        used = alt_engine
    elif result is not None:
        words, agreement, notes = align_text_to_segments(segments, str(result.get("text", "")))
        replace = engine != "whisper"
        for seg, w, a, n in zip(segments, words, agreement, notes):
            s = dict(seg)
            if replace and w:
                s["whisper_text"] = s["text"]
                s["text"] = " " + " ".join(w)
            s["engine"] = alt_engine if replace else "whisper"
            s["agreement"] = a
            if not replace:
                s["crosscheck_text"] = " ".join(w)
            s["flags"] = "; ".join(segment_flags(seg, a, n))
            out.append(s)
        used = alt_engine if replace else "whisper+voxtral-crosscheck"
    else:
        for seg in segments:
            s = dict(seg)
            s["engine"] = "whisper"
            s["flags"] = "; ".join(segment_flags(seg))
            out.append(s)
    if note:
        for s in out:
            s["engine_note"] = note
    try:
        header = f"Low-confidence spots for {stem} (engine: {used})" + (f"\nNOTE: {note}" if note else "")
        write_flag_summary(os.path.join(flags_dir, f"{stem}.flags.txt"), out, header)
    except Exception as exc:
        print(f"Could not write flag summary: {exc}", flush=True)
    return out
