"""Optional speaker labels ("Speaker 1:", "Speaker 2:") for transcripts (v1.5.0).

Runs fully locally with sherpa-onnx (Apache-2.0) and two permissively licensed ONNX models,
downloaded once from the sherpa-onnx GitHub releases (no Hugging Face token, no gated model):

* segmentation: sherpa-onnx-pyannote-segmentation-3-0 (pyannote segmentation-3.0, MIT)
* speaker embedding: wespeaker_en_voxceleb_resnet34_LM.onnx (WeSpeaker ResNet34-LM; code Apache-2.0,
  weights CC-BY-4.0 because they were trained on VoxCeleb2)

See docs/SPEAKER_LABELS.md for licenses and how to rename speakers.
Nothing here may ever fail a transcription: every entry point catches and reports.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parent
SEGMENTATION_DIR = "sherpa-onnx-pyannote-segmentation-3-0"
SEGMENTATION_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
                    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMBEDDING_FILE = "wespeaker_en_voxceleb_resnet34_LM.onnx"
EMBEDDING_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
                 + EMBEDDING_FILE)
DEFAULT_THRESHOLD = 0.5  # cosine clustering threshold when the speaker count is unknown

Turn = tuple[float, float, int]


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def enabled(settings: dict | None = None) -> bool:
    env = os.environ.get("LABEL_SPEAKERS")
    if env not in (None, ""):
        return _truthy(env)
    return bool((settings or {}).get("label_speakers", False))


def models_dir(settings: dict | None = None) -> Path:
    raw = str(os.environ.get("DIARIZATION_MODELS_DIR") or (settings or {}).get("diarization_models_dir") or "").strip()
    return Path(raw) if raw else REPO_ROOT / "models" / "diarization"


def model_paths(settings: dict | None = None) -> tuple[Path, Path]:
    d = models_dir(settings)
    seg = d / SEGMENTATION_DIR / "model.onnx"
    emb = str((settings or {}).get("diarization_embedding_model") or EMBEDDING_FILE)
    return seg, d / emb


def check_ready(settings: dict | None = None) -> str | None:
    """None when diarization can run, else a plain-language reason."""
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return "Speaker labels need the sherpa-onnx package (pip install sherpa-onnx)."
    seg, emb = model_paths(settings)
    if not seg.is_file() or not emb.is_file():
        return (f"Speaker-label models are missing in {models_dir(settings)}. "
                "Run: python speaker_labels.py download")
    return None


def download_models(settings: dict | None = None) -> Path:
    d = models_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    seg, emb = model_paths(settings)
    if not seg.is_file():
        tar = d / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
        urllib.request.urlretrieve(SEGMENTATION_URL, tar)
        import tarfile
        with tarfile.open(tar, "r:bz2") as t:
            t.extractall(d, filter="data")
    if not emb.is_file():
        urllib.request.urlretrieve(EMBEDDING_URL, emb)
    return d


def _read_wav16k(audio_path: str):
    import numpy as np
    with tempfile.TemporaryDirectory(prefix="diar_") as tmp:
        wav = os.path.join(tmp, "a16k.wav")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000", wav],
                       check=True, timeout=1800)
        with wave.open(wav, "rb") as w:
            raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0


def diarize(audio_path: str, settings: dict | None = None, num_speakers: int | None = None) -> list[Turn]:
    """Return (start, end, speaker_index) turns, sorted by start."""
    import sherpa_onnx
    seg, emb = model_paths(settings)
    n = int(num_speakers or (settings or {}).get("speaker_count") or 0)
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg)),
            num_threads=2,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb), num_threads=2),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=n if n > 0 else -1,
                                                    threshold=float((settings or {}).get("speaker_threshold") or DEFAULT_THRESHOLD)),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not cfg.validate():
        raise RuntimeError("speaker-label config is invalid (check the model files)")
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    samples = _read_wav16k(audio_path)
    result = sd.process(samples).sort_by_start_time()
    return merge_minor_speakers([(float(r.start), float(r.end), int(r.speaker)) for r in result])


def merge_minor_speakers(turns: list[Turn], min_share: float = 0.05, min_seconds: float = 3.0) -> list[Turn]:
    """Fold tiny clusters (a few short blips, usually noise or overlap) into the nearest real speaker."""
    if not turns:
        return turns
    total: dict[int, float] = {}
    for s, e, spk in turns:
        total[spk] = total.get(spk, 0.0) + (e - s)
    whole = sum(total.values()) or 1.0
    major = {spk for spk, d in total.items() if d >= min_seconds and d / whole >= min_share}
    if not major or len(major) == len(total):
        return turns
    keep = [t for t in turns if t[2] in major]
    out = []
    for s, e, spk in turns:
        if spk not in major:
            near = min(keep, key=lambda t: max(0.0, t[0] - e, s - t[1]))
            spk = near[2]
        out.append((s, e, spk))
    return out


def _speaker_at(t0: float, t1: float, turns: list[Turn]) -> int | None:
    best, best_ov = None, 0.0
    for s, e, spk in turns:
        ov = min(t1, e) - max(t0, s)
        if ov > best_ov:
            best, best_ov = spk, ov
    if best is None and turns:  # no overlap (gap): nearest turn
        mid = (t0 + t1) / 2
        best = min(turns, key=lambda tr: min(abs(mid - tr[0]), abs(mid - tr[1])))[2]
    return best


def assign_speakers(segments: list[dict], turns: list[Turn]) -> list[dict]:
    """Give every segment a 'speaker' ("Speaker 1", ...), numbered by first appearance.

    When a segment carries word timings ('_words': [(start, end, word), ...]) and the speaker
    changes inside it, the segment is split at the change so each line has one speaker.
    """
    out: list[dict] = []
    for seg in segments:
        words = seg.get("_words") or []
        base = {k: v for k, v in seg.items() if k != "_words"}
        if not turns:
            out.append(base)
            continue
        if not words:
            base["_spk"] = _speaker_at(float(seg["start"]), float(seg["end"]), turns)
            out.append(base)
            continue
        groups: list[tuple[int | None, list]] = []
        for w in words:
            spk = _speaker_at(float(w[0]), float(w[1]), turns)
            if groups and groups[-1][0] == spk:
                groups[-1][1].append(w)
            else:
                groups.append((spk, [w]))
        # glue one-word islands onto the previous group (usually a timing wobble, not a real turn)
        merged: list[tuple[int | None, list]] = []
        for spk, ws in groups:
            if merged and len(ws) == 1 and len(groups) > 1:
                merged[-1][1].extend(ws)
            elif merged and merged[-1][0] == spk:
                merged[-1][1].extend(ws)
            else:
                merged.append((spk, list(ws)))
        if len(merged) == 1:
            base["_spk"] = merged[0][0]
            out.append(base)
            continue
        for spk, ws in merged:
            part = dict(base)
            part["start"] = round(float(ws[0][0]), 2)
            part["end"] = round(float(ws[-1][1]), 2)
            part["text"] = " " + "".join(str(w[2]) for w in ws).strip()
            part["_spk"] = spk
            part["split_by_speaker"] = True
            out.append(part)
    order: dict[int, str] = {}
    for s in out:
        spk = s.pop("_spk", None)
        if spk is None:
            continue
        if spk not in order:
            order[spk] = f"Speaker {len(order) + 1}"
        s["speaker"] = order[spk]
    return out


def strip_word_timings(segments: Iterable[dict]) -> None:
    for s in segments:
        s.pop("_words", None)


def speakers_file(json_dir: str | Path, stem: str) -> Path:
    return Path(json_dir) / f"{stem}.speakers.json"


def write_speakers_file(json_dir: str | Path, stem: str, segments: list[dict]) -> Path | None:
    labels: list[str] = []
    for s in segments:
        sp = s.get("speaker")
        if sp and sp not in labels:
            labels.append(sp)
    if not labels:
        return None
    p = speakers_file(json_dir, stem)
    data = {"_help": "Type a real name next to each label, save, then run: python speaker_labels.py rename "
                     f"\"{stem}\"  (or keep the label as is).",
            "names": {lab: lab for lab in labels}}
    if p.is_file():  # never throw away names the user already typed
        try:
            old = json.loads(p.read_text(encoding="utf-8")).get("names", {})
            data["names"].update({k: v for k, v in old.items() if k in data["names"]})
        except Exception:
            pass
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def label_segments(audio_path: str, segments: list[dict], settings: dict | None = None,
                   diarizer: Callable[..., list[Turn]] | None = None) -> tuple[list[dict], str | None]:
    """Returns (segments, note). On any problem returns the input segments unchanged and a note."""
    if not segments:
        return segments, None
    reason = None if diarizer else check_ready(settings)
    if reason:
        strip_word_timings(segments)
        return segments, reason
    try:
        print("Speaker labels: finding who speaks when ...", flush=True)
        turns = (diarizer or diarize)(audio_path, settings)
        labeled = assign_speakers(segments, turns)
        n = len({s.get("speaker") for s in labeled if s.get("speaker")})
        print(f"Speaker labels: {n} speaker(s) found.", flush=True)
        return labeled, None
    except Exception as exc:
        strip_word_timings(segments)
        return segments, f"Speaker labels skipped: {type(exc).__name__}: {exc}"


def render_txt(segments: list[dict]) -> str:
    """Readable transcript; with speakers, consecutive lines of one speaker form one paragraph."""
    blocks: list[str] = []
    cur_spk: str | None = None
    cur: list[str] = []
    for seg in segments:
        text = " ".join(str(seg.get("text", "")).split()).strip()
        if not text:
            continue
        spk = seg.get("speaker")
        if not spk:
            if cur:
                blocks.append(f"{cur_spk}: " + " ".join(cur)); cur, cur_spk = [], None
            blocks.append(text)
            continue
        if spk != cur_spk and cur:
            blocks.append(f"{cur_spk}: " + " ".join(cur)); cur = []
        cur_spk = spk
        cur.append(text)
    if cur:
        blocks.append(f"{cur_spk}: " + " ".join(cur))
    return "\n\n".join(blocks)


def rename(stem: str, json_dir: str | Path, transcript_dir: str | Path, csv_dir: str | Path) -> int:
    """Apply <stem>.speakers.json names to the json/csv/txt/srt/vtt. Keeps a one-time backup of the JSON."""
    names = json.loads(speakers_file(json_dir, stem).read_text(encoding="utf-8")).get("names", {})
    jp = Path(json_dir) / f"{stem}.json"
    data = json.loads(jp.read_text(encoding="utf-8"))
    backup = Path(json_dir) / f"{stem}.before-rename.json"
    if not backup.exists():
        backup.write_text(jp.read_text(encoding="utf-8"), encoding="utf-8")
    changed = 0
    for s in data:
        if isinstance(s, dict) and s.get("speaker"):
            label = s.get("speaker_label") or s["speaker"]
            s["speaker_label"] = label
            new = str(names.get(label) or label).strip() or label
            if new != s["speaker"]:
                changed += 1
            s["speaker"] = new
    jp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    segs = [s for s in data if isinstance(s, dict)]
    Path(transcript_dir, f"{stem}.txt").write_text(render_txt(segs), encoding="utf-8")
    cp = Path(csv_dir) / f"{stem}.csv"
    if segs:
        keys: list[str] = []
        for s in segs:
            keys.extend(k for k in s if k not in keys)
        with open(cp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(segs)
    import transcript_search
    transcript_search.after_transcript(stem, json_dir, transcript_dir)
    return changed


def _cli(argv: list[str]) -> int:
    if not argv or argv[0] not in ("download", "rename", "check"):
        print("usage: python speaker_labels.py download | check | rename <stem>")
        return 2
    from watcher_settings import load_settings
    settings = load_settings(REPO_ROOT)
    if argv[0] == "download":
        print(f"Models ready in {download_models(settings)}")
        return 0
    if argv[0] == "check":
        r = check_ready(settings)
        print(r or "Speaker labels are ready.")
        return 0 if r is None else 1
    from output_paths import ensure_output_dirs
    d = ensure_output_dirs(REPO_ROOT, settings)
    n = rename(argv[1], d["completed_json"], d["completed_transcripts"], d["completed_csv"])
    print(f"Renamed {n} line(s).")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
