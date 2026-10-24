"""v1.4.2: transcript accuracy fix (VAD + keep Whisper on disagreement)."""
from __future__ import annotations

import bulk_transcribe_youtube_videos_from_playlist as bt


def test_robust_vad_settings() -> None:
    k = bt.transcribe_kwargs_for_profile("robust", True)
    assert k["vad_parameters"] == {"threshold": 0.3, "min_silence_duration_ms": 1000, "speech_pad_ms": 400}
    assert bt.transcribe_kwargs_for_profile("auto", True)["vad_parameters"]["threshold"] == 0.3


def test_legacy_and_music_unchanged() -> None:
    assert "vad_parameters" not in bt.transcribe_kwargs_for_profile("legacy", True)
    assert "vad_parameters" not in bt.transcribe_kwargs_for_profile("music", True)
