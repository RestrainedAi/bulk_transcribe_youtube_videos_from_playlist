"""
GPU / CUDA smoke tests.

These tests are skipped automatically when no CUDA-capable GPU is detected,
so they run safely in CI or on CPU-only machines without failing the suite.
A model load test is included but no audio is processed — it just confirms
that faster-whisper can initialise on the GPU without a native crash.
"""

import unittest


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


@unittest.skipUnless(_cuda_available(), "No CUDA GPU detected — skipping GPU tests")
class GpuSmokeTest(unittest.TestCase):

    def test_large_v3_gpu_model_loads(self) -> None:
        """Confirm large-v3 loads on GPU without crashing (Python 3.12 + ctranslate2 compat check)."""
        from faster_whisper import WhisperModel
        model = WhisperModel("large-v3", device="cuda", compute_type="int8_float16")
        self.assertIsNotNone(model)
        del model

    def test_medium_gpu_model_loads(self) -> None:
        """Confirm medium loads on GPU — used as the lighter fallback."""
        from faster_whisper import WhisperModel
        model = WhisperModel("medium", device="cuda", compute_type="int8_float16")
        self.assertIsNotNone(model)
        del model

    def test_cuda_device_count_positive(self) -> None:
        import ctranslate2
        self.assertGreater(ctranslate2.get_cuda_device_count(), 0)


class CpuSmokeTest(unittest.TestCase):

    def test_medium_cpu_model_loads(self) -> None:
        """Confirm medium loads on CPU — the primary fallback path."""
        from faster_whisper import WhisperModel
        model = WhisperModel("medium", device="cpu", compute_type="int8")
        self.assertIsNotNone(model)
        del model


if __name__ == "__main__":
    unittest.main()
