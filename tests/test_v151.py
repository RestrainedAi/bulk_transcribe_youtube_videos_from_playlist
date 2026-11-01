"""v1.5.1: regression tests for the dropped first phrase (about 0:01-0:06)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

import speech_rescue as sr

ROOT = Path(__file__).resolve().parents[1]


def _seg(start, end, text, ns=0.01, lp=-0.1, cr=1.3, words=None):
    return SimpleNamespace(start=start, end=end, text=text, no_speech_prob=ns, avg_logprob=lp,
                           compression_ratio=cr, words=words)


def test_version_is_151() -> None:
    import app_version
    assert tuple(map(int, app_version.APP_VERSION.split("."))) >= (1, 5, 1)


def test_quiet_opening_is_reported_uncovered() -> None:
    # The exact 1.5.0 failure shape: VAD finds 0.72-5.55 s, Whisper's first line starts at 5.3 s.
    speech = [(0.72, 5.55), (7.02, 22.71)]
    segs = [{"start": 5.3, "end": 12.13}, {"start": 12.13, "end": 14.41}, {"start": 15.11, "end": 21.57}]
    assert sr.uncovered_spans(speech, segs) == [(0.72, 5.3)]


def test_dropped_tail_and_short_gaps() -> None:
    speech = [(0.72, 5.42), (7.02, 22.71)]
    segs = [{"start": 0.72, "end": 4.6}]
    assert sr.uncovered_spans(speech, segs) == [(7.02, 22.71)]  # 4.6-5.42 is under 1.5 s (VAD padding), ignored
    assert sr.uncovered_spans([(0.0, 10.0)], [{"start": 0.5, "end": 9.5}]) == []


def test_rescue_prepends_the_first_phrase_with_real_times() -> None:
    audio = np.zeros(23 * sr.SAMPLE_RATE, dtype="float32")
    audio[int(1.0 * sr.SAMPLE_RATE): int(5.0 * sr.SAMPLE_RATE)] = 0.01  # quiet opening
    calls = []

    def fake_transcribe(clip, **kw):
        calls.append((len(clip), kw))
        assert abs(float(np.abs(clip).max()) - 0.9) < 1e-3  # clip is peak-normalised
        return iter([_seg(0.1, 3.9, " Okay, before we start, can everyone hear me?")]), None

    main = [{"start": 5.3, "end": 12.13, "text": " Today we are going to talk."}]
    run_kwargs = {"vad_filter": True, "vad_parameters": {"threshold": 0.3}, "initial_prompt": "Hello",
                  "hallucination_silence_threshold": 2.0, "word_timestamps": True, "batch_size": 8}
    out = sr.rescue(audio, [(0.72, 5.55), (7.02, 12.2)], main, fake_transcribe, run_kwargs)
    assert len(out) == 1 and out[0].rescued
    assert round(out[0].start, 2) == 0.82 and round(out[0].end, 2) == 4.62
    kw = calls[0][1]
    assert kw["vad_filter"] is False and kw["initial_prompt"] is None
    assert kw["hallucination_silence_threshold"] is None and "batch_size" not in kw and "vad_parameters" not in kw
    assert kw["word_timestamps"] is True


def test_rescue_drops_noise_and_never_raises() -> None:
    audio = np.full(10 * sr.SAMPLE_RATE, 0.05, dtype="float32")
    assert not sr.keep_rescued(_seg(0, 1, "  "))
    assert not sr.keep_rescued(_seg(0, 1, " uh", ns=0.9, lp=-1.5))
    assert not sr.keep_rescued(_seg(0, 1, " la la la", cr=3.0))

    def boom(clip, **kw):
        raise RuntimeError("gpu gone")
    assert sr.rescue(audio, [(0.0, 5.0)], [], boom, {"vad_filter": True}) == []


def test_word_times_are_shifted_for_speaker_labels() -> None:
    w = [SimpleNamespace(start=0.1, end=0.5, word=" Okay", probability=0.9)]
    s = sr.shift_segment(_seg(0.1, 0.5, " Okay", words=w), 2.0)
    assert (round(s.words[0].start, 2), round(s.words[0].end, 2)) == (2.1, 2.5)


def test_pipeline_wires_the_rescue_in() -> None:
    src = (ROOT / "bulk_transcribe_youtube_videos_from_playlist.py").read_text(encoding="utf-8")
    assert "speech_rescue.rescue(" in src and "whisper_segment_metadata(" in src
    assert 'run_kwargs.get("vad_filter")' in src  # music profile (no VAD) is untouched
