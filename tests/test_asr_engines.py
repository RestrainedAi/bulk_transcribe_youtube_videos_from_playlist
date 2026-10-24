import os
import tempfile
import unittest
from unittest.mock import patch

import asr_engines as ae

SEGS = [
    {"start": 0.0, "end": 2.0, "text": " The phone made six thousand connections.", "avg_logprob": -0.2, "no_speech_prob": 0.01, "compression_ratio": 1.2},
    {"start": 2.0, "end": 4.0, "text": " Your data is sent out.", "avg_logprob": -0.9, "no_speech_prob": 0.02, "compression_ratio": 1.3},
]


class EngineSelectionTest(unittest.TestCase):
    def test_default_is_whisper(self):
        with patch.dict(os.environ, {"ASR_ENGINE": "", "ASR_CROSSCHECK": ""}):
            self.assertEqual(ae.resolve_engine({}), "whisper")
            self.assertFalse(ae.resolve_crosscheck({}))

    def test_unknown_engine_falls_back(self):
        with patch.dict(os.environ, {"ASR_ENGINE": ""}):
            self.assertEqual(ae.resolve_engine({"asr_engine": "nope"}), "whisper")
            self.assertEqual(ae.resolve_engine({"asr_engine": "Granite-2B"}), "granite-2b")

    def test_removed_granite_8b_maps_to_whisper(self):
        with patch.dict(os.environ, {"ASR_ENGINE": ""}):
            self.assertNotIn("granite-8b", ae.ENGINES)
            self.assertEqual(ae.resolve_engine({"asr_engine": "granite-8b"}), "whisper")

    def test_saved_granite_8b_setting_is_migrated(self):
        import tempfile, pathlib, json as _json
        from watcher_settings import load_settings
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "watcher_settings.json").write_text(_json.dumps({"asr_engine": "granite-8b"}), encoding="utf-8")
            self.assertEqual(load_settings(pathlib.Path(d))["asr_engine"], "whisper")

    def test_env_overrides(self):
        with patch.dict(os.environ, {"ASR_ENGINE": "voxtral-mini-3b", "ASR_CROSSCHECK": "1"}):
            self.assertEqual(ae.resolve_engine({"asr_engine": "whisper"}), "voxtral-mini-3b")
            self.assertTrue(ae.resolve_crosscheck({"asr_crosscheck": False}))


class VramCheckTest(unittest.TestCase):
    def test_low_vram_gives_clear_reason(self):
        with tempfile.TemporaryDirectory() as d:
            py = os.path.join(d, "python.exe"); open(py, "w").close()
            os.makedirs(os.path.join(d, "Voxtral-Mini-3B-2507")); open(os.path.join(d, "Voxtral-Mini-3B-2507", "config.json"), "w").close()
            s = {"asr_eval_python": py, "asr_models_dir": d}
            self.assertIsNone(ae.check_engine_ready("voxtral-mini-3b", s, free_gb=20.0))
            msg = ae.check_engine_ready("voxtral-mini-3b", s, free_gb=4.0)
            self.assertIn("free GPU memory", msg)
            self.assertIn("4.0 GB", msg)

    def test_missing_venv_and_model(self):
        self.assertIn("venv", ae.check_engine_ready("granite-2b", {"asr_eval_python": r"Z:\none\python.exe"}, 20.0))


class AlignmentTest(unittest.TestCase):
    def test_alignment_keeps_timestamps_and_scores(self):
        words, agree, notes = ae.align_text_to_segments(SEGS, "The phone made six thousand connections. Your debt is sent out.")
        self.assertEqual(" ".join(words[0]), "The phone made six thousand connections.")
        self.assertIn("debt", " ".join(words[1]))
        self.assertEqual(agree[0], 1.0)
        self.assertLess(agree[1], 1.0)
        self.assertTrue(notes[1])

    def test_empty_inputs(self):
        self.assertEqual(ae.align_text_to_segments([], "x")[0], [])
        w, a, _ = ae.align_text_to_segments(SEGS, "")
        self.assertEqual(a, [0.0, 0.0])


class PipelineTest(unittest.TestCase):
    def _run(self, settings, runner, free=30.0):
        with tempfile.TemporaryDirectory() as d, patch.object(ae, "check_engine_ready", lambda e, s, f: None if f >= 12 else "not enough GPU memory"):
            out = ae.apply_engine_pipeline("a.wav", "stem", SEGS, settings, d, runner=runner, free_gb_func=lambda: free)
            summary = open(os.path.join(d, "stem.flags.txt"), encoding="utf-8").read()
        return out, summary

    def test_whisper_only_adds_flags(self):
        with patch.dict(os.environ, {"ASR_ENGINE": "", "ASR_CROSSCHECK": ""}):
            out, summary = self._run({}, runner=lambda *a: self.fail("worker must not run"))
        self.assertEqual([s["start"] for s in out], [0.0, 2.0])
        self.assertIn("low avg_logprob", out[1]["flags"])
        self.assertIn("Low-confidence spots", summary)

    def test_alt_engine_replaces_text_keeps_times(self):
        with patch.dict(os.environ, {"ASR_ENGINE": "granite-2b", "ASR_CROSSCHECK": ""}):
            out, _ = self._run({}, runner=lambda e, a, s: {"text": "The phone made six thousand connections. Your data is sent out!", "chunks": []})
        self.assertEqual(out[1]["engine"], "granite-2b")
        self.assertEqual(out[1]["end"], 4.0)
        self.assertIn("out!", out[1]["text"])
        self.assertIn("whisper_text", out[1])

    def test_alt_engine_disagreement_keeps_whisper_and_flags(self):
        with patch.dict(os.environ, {"ASR_ENGINE": "granite-2b", "ASR_CROSSCHECK": ""}):
            out, summary = self._run({}, runner=lambda e, a, s: {"text": "The phone made six thousand connections. Your debt is sent out.", "chunks": []})
        self.assertEqual(out[0]["engine"], "granite-2b")  # full agreement: alt text allowed
        self.assertEqual(out[1]["text"], SEGS[1]["text"])  # disagreement: Whisper kept
        self.assertEqual(out[1]["engine"], "whisper")
        self.assertIn("debt", out[1]["alt_text"])
        self.assertIn("kept Whisper's words", out[1]["flags"])
        self.assertIn("models disagree", out[1]["flags"])
        self.assertIn("kept Whisper", summary)

    def test_alt_engine_dropped_clause_keeps_whisper(self):
        with patch.dict(os.environ, {"ASR_ENGINE": "granite-2b", "ASR_CROSSCHECK": ""}):
            out, _ = self._run({}, runner=lambda e, a, s: {"text": "The phone made connections. Your data is sent out.", "chunks": []})
        self.assertEqual(out[0]["text"], SEGS[0]["text"])
        self.assertIn("kept Whisper's words", out[0]["flags"])

    def test_crosscheck_keeps_whisper_text(self):
        with patch.dict(os.environ, {"ASR_ENGINE": "", "ASR_CROSSCHECK": "1"}):
            out, summary = self._run({}, runner=lambda e, a, s: {"text": "The phone made six thousand connections. Your debt is sent out.", "chunks": []})
        self.assertEqual(out[1]["text"], SEGS[1]["text"])
        self.assertLess(out[1]["agreement"], 1.0)
        self.assertIn("models disagree", out[1]["flags"])
        self.assertIn("whisper+voxtral-crosscheck", summary)

    def test_low_vram_falls_back_without_crash(self):
        with patch.dict(os.environ, {"ASR_ENGINE": "voxtral-mini-3b", "ASR_CROSSCHECK": ""}):
            out, summary = self._run({}, runner=lambda *a: self.fail("worker must not run"), free=3.0)
        self.assertEqual(out[0]["engine"], "whisper")
        self.assertIn("Kept the faster-whisper result", out[0]["engine_note"])
        self.assertIn("NOTE:", summary)

    def test_worker_failure_falls_back(self):
        def boom(*a):
            raise RuntimeError("CUDA out of memory")
        with patch.dict(os.environ, {"ASR_ENGINE": "granite-2b", "ASR_CROSSCHECK": ""}):
            out, _ = self._run({}, runner=boom)
        self.assertEqual(out[1]["text"], SEGS[1]["text"])
        self.assertIn("out of memory", out[1]["engine_note"])

    def test_chunk_timing_when_whisper_empty(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"ASR_ENGINE": "granite-2b", "ASR_CROSSCHECK": ""}), \
                patch.object(ae, "check_engine_ready", lambda e, s, f: None):
            out = ae.apply_engine_pipeline("a.wav", "s", [], {}, d, runner=lambda *a: {"text": "a b", "chunks": [{"start": 0, "end": 30, "text": "a"}, {"start": 30, "end": 45, "text": "b"}]}, free_gb_func=lambda: 30)
        self.assertEqual([(s["start"], s["end"]) for s in out], [(0, 30), (30, 45)])


if __name__ == "__main__":
    unittest.main()
