"""Rescue speech Whisper skipped (v1.5.1).

Bug fixed: with the 1.4.2 speech VAD (min silence 1000 ms, pad 400 ms) the VAD still finds a quiet
opening phrase (about 0:01-0:06), but the merged chunk layout lets Whisper decode the louder speech
after it and silently skip the quiet phrase, so the transcript started at the second sentence.
After the main pass we compare the VAD speech spans with the transcribed segments. Any stretch of
VAD speech no segment covers (at least ``min_len`` seconds) is cut out on its own, peak-normalised
and transcribed again, and the result is merged back in time order.

Pure helpers here are unit-tested without a model; nothing in this module may fail a job.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Iterable, Sequence

SAMPLE_RATE = 16000
MIN_GAP_SECONDS = 1.5  # VAD pads each span by 0.4 s; smaller gaps are just padding
MAX_RESCUES = 20


def uncovered_spans(speech: Iterable[tuple[float, float]], segments: Sequence[Any],
                    min_len: float = MIN_GAP_SECONDS) -> list[tuple[float, float]]:
    """Parts of the VAD speech spans that no transcribed segment overlaps.

    ``segments`` may be dicts with start/end or objects with .start/.end.
    """
    covered = sorted((_get(s, "start"), _get(s, "end")) for s in segments)
    out: list[tuple[float, float]] = []
    for s0, e0 in speech:
        cur = float(s0)
        for cs, ce in covered:
            if ce <= cur or cs >= e0:
                continue
            if cs - cur >= min_len:
                out.append((round(cur, 2), round(cs, 2)))
            cur = max(cur, ce)
            if cur >= e0:
                break
        if e0 - cur >= min_len:
            out.append((round(cur, 2), round(float(e0), 2)))
    return out[:MAX_RESCUES]


def _get(seg: Any, key: str) -> float:
    return float(seg[key] if isinstance(seg, dict) else getattr(seg, key))


def shift_segment(seg: Any, offset: float) -> SimpleNamespace:
    words = getattr(seg, "words", None)
    shifted_words = None
    if words:
        shifted_words = [SimpleNamespace(start=w.start + offset, end=w.end + offset, word=w.word,
                                         probability=getattr(w, "probability", None)) for w in words]
    return SimpleNamespace(start=seg.start + offset, end=seg.end + offset, text=seg.text,
                           avg_logprob=seg.avg_logprob, no_speech_prob=seg.no_speech_prob,
                           compression_ratio=seg.compression_ratio, words=shifted_words, rescued=True)


def keep_rescued(seg: Any, no_speech_threshold: float = 0.6, log_prob_threshold: float = -1.0,
                 compression_ratio_threshold: float = 2.4) -> bool:
    if not str(getattr(seg, "text", "")).strip():
        return False
    if seg.compression_ratio > compression_ratio_threshold:
        return False
    if seg.no_speech_prob > no_speech_threshold and seg.avg_logprob < log_prob_threshold:
        return False
    return True


def rescue_kwargs(run_kwargs: dict) -> dict:
    """Same decoding options, but on a short clip: no VAD, no priming prompt, no silence skipping."""
    kw = {k: v for k, v in run_kwargs.items() if k not in ("vad_parameters", "batch_size")}
    kw.update(vad_filter=False, initial_prompt=None, hallucination_silence_threshold=None)
    return kw


def rescue(audio, speech: list[tuple[float, float]], segments: Sequence[Any],
           transcribe: Callable[..., tuple[Iterable[Any], Any]], run_kwargs: dict,
           min_len: float = MIN_GAP_SECONDS) -> list[SimpleNamespace]:
    """Transcribe uncovered VAD speech; returns shifted segments to merge (may be empty)."""
    out: list[SimpleNamespace] = []
    kw = rescue_kwargs(run_kwargs)
    for start, end in uncovered_spans(speech, segments, min_len):
        clip = audio[int(start * SAMPLE_RATE): int(end * SAMPLE_RATE)]
        if len(clip) < int(min_len * SAMPLE_RATE):
            continue
        peak = float(abs(clip).max()) if len(clip) else 0.0
        if peak > 1e-4:
            clip = clip * (0.9 / peak)
        try:
            found, _ = transcribe(clip, **kw)
            for seg in found:
                if keep_rescued(seg, run_kwargs.get("no_speech_threshold") or 0.6,
                                run_kwargs.get("log_prob_threshold") or -1.0,
                                run_kwargs.get("compression_ratio_threshold") or 2.4):
                    s = shift_segment(seg, start)
                    s.end = min(s.end, end)
                    out.append(s)
        except Exception as exc:  # a rescue must never fail the job
            print(f"Speech rescue skipped for {start:.2f}-{end:.2f}s: {type(exc).__name__}: {exc}", flush=True)
    return out


def vad_speech_spans(audio, vad_parameters: dict | None) -> list[tuple[float, float]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    ts = get_speech_timestamps(audio, VadOptions(**(vad_parameters or {})))
    return [(t["start"] / SAMPLE_RATE, t["end"] / SAMPLE_RATE) for t in ts]
