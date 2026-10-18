import os
import unittest
from unittest.mock import patch

import bulk_transcribe_youtube_videos_from_playlist as bt


class WhisperAttemptPlanTest(unittest.TestCase):
    def test_gpu_plan_uses_selected_models(self) -> None:
        attempts = bt.build_whisper_attempts(True, "small", "tiny")
        self.assertEqual(
            [a[:3] for a in attempts],
            [
                ("small", "cuda", "int8_float16"),
                ("small", "cuda", "int8_float16"),
                ("tiny", "cpu", "int8"),
                ("tiny", "cpu", "auto"),
            ],
        )

    def test_robust_plan_prefers_fp16(self) -> None:
        attempts = bt.build_whisper_attempts(True, "large-v3", "medium", "robust")
        self.assertEqual(attempts[0][:3], ("large-v3", "cuda", "float16"))

    def test_profiles(self) -> None:
        legacy = bt.transcribe_kwargs_for_profile("legacy", False)
        self.assertEqual(legacy, {"beam_size": 5, "vad_filter": False, "language": "en", "task": "transcribe"})
        robust = bt.transcribe_kwargs_for_profile("robust", False)
        self.assertTrue(robust["vad_filter"])
        self.assertFalse(robust["condition_on_previous_text"])
        self.assertEqual(bt.normalize_whisper_model("large-v3-turbo", "medium"), "large-v3-turbo")
        self.assertIsNone(bt.transcribe_kwargs_for_profile("robust", True, None)["language"])

    def test_language_setting(self) -> None:
        with patch.dict(os.environ, {"WHISPER_LANGUAGE": ""}):
            self.assertIsNone(bt.resolve_whisper_language({"whisper_language": "auto"}))
            self.assertEqual(bt.resolve_whisper_language({"whisper_language": "EN"}), "en")

    def test_cpu_plan_skips_cuda(self) -> None:
        attempts = bt.build_whisper_attempts(False, "large-v3", "medium")
        self.assertTrue(all(device == "cpu" for _, device, *_ in attempts))
        self.assertEqual({model for model, *_ in attempts}, {"medium"})

    def test_invalid_model_falls_back(self) -> None:
        self.assertEqual(bt.normalize_whisper_model("nope", "large-v3"), "large-v3")
        self.assertEqual(bt.normalize_whisper_model("LARGE-V3", "medium"), "large-v3")

    def test_resolve_prefers_env(self) -> None:
        with patch.dict(os.environ, {"WHISPER_GPU_MODEL": "base", "WHISPER_CPU_MODEL": "tiny"}):
            gpu, cpu = bt.resolve_whisper_models({"whisper_gpu_model": "large-v3", "whisper_cpu_model": "medium"})
            self.assertEqual((gpu, cpu), ("base", "tiny"))
