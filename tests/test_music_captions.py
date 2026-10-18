import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import bulk_transcribe_youtube_videos_from_playlist as bt
import coverage_check
import youtube_captions as yc

FIX = Path(__file__).parent / "fixtures"


def load_srt(path: Path) -> list[dict]:
    t = path.read_text(encoding="utf-8-sig")

    def ts(x: str) -> float:
        h, m, s = x.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    return [{"start": ts(a), "end": ts(b), "text": c}
            for a, b, c in re.findall(r"(\d\d:\d\d:\d\d,\d+) --> (\d\d:\d\d:\d\d,\d+)\n(.*)", t)]


class ProfileKwargsTest(unittest.TestCase):
    def test_music(self) -> None:
        k = bt.transcribe_kwargs_for_profile("music", True)
        self.assertFalse(k["vad_filter"])  # overrides robust's forced VAD
        self.assertIsNone(k["hallucination_silence_threshold"])
        self.assertIsNone(k["initial_prompt"])
        self.assertEqual(k["no_speech_threshold"], 0.9)
        self.assertEqual(k["beam_size"], 5)
        self.assertEqual(k["temperature"], [0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
        self.assertFalse(k["condition_on_previous_text"])
        self.assertTrue(k["word_timestamps"])
        self.assertNotIn("vad_parameters", k)

    def test_speech_auto_robust_match(self) -> None:
        r = bt.transcribe_kwargs_for_profile("robust", False)
        self.assertTrue(r["vad_filter"])
        self.assertEqual(r["no_speech_threshold"], 0.6)
        self.assertEqual(bt.transcribe_kwargs_for_profile("speech", False), r)
        self.assertEqual(bt.transcribe_kwargs_for_profile("auto", False), r)

    def test_legacy_unchanged(self) -> None:
        self.assertEqual(bt.transcribe_kwargs_for_profile("legacy", False),
                         {"beam_size": 5, "vad_filter": False, "language": "en", "task": "transcribe"})

    def test_attempts_keep_vad_off_for_music(self) -> None:
        for use_gpu in (True, False):
            self.assertTrue(all(a[3] is False for a in bt.build_whisper_attempts(use_gpu, "large-v3", "medium", "music")))
        auto = bt.build_whisper_attempts(True, "large-v3", "medium", "auto")
        self.assertEqual(auto[0][:4], ("large-v3", "cuda", "float16", True))

    def test_resolve(self) -> None:
        with patch.dict("os.environ", {"WHISPER_DECODE_PROFILE": ""}):
            self.assertEqual(bt.resolve_decode_profile({}), "auto")
            self.assertEqual(bt.resolve_decode_profile({"whisper_decode_profile": "speech"}), "robust")
            self.assertEqual(bt.resolve_decode_profile({"whisper_decode_profile": "music"}), "music")
            self.assertEqual(bt.resolve_decode_profile({"whisper_decode_profile": "bogus"}), "auto")


class CoverageTest(unittest.TestCase):
    def test_bad_srt(self) -> None:
        segs = load_srt(FIX / "TmX3i2n9YEo_whisper_vad.srt")
        self.assertEqual(len(segs), 34)
        w = coverage_check.coverage_warnings(segs, 1991.7)
        self.assertEqual(len(w), 11)
        self.assertEqual(coverage_check.describe(w[1]), "Possibly missed audio at 0:56\u20139:05")
        kinds = [x["kind"] for x in w]
        self.assertEqual(kinds.count("gap"), 2)
        # a longer real duration adds the end-of-file gap
        self.assertEqual(len(coverage_check.coverage_warnings(segs, 2040)), 12)

    def test_start_and_clean(self) -> None:
        self.assertEqual(coverage_check.coverage_warnings([{"start": 90, "end": 95}])[0],
                         {"kind": "gap", "start": 0.0, "end": 90})
        ok = [{"start": i * 5, "end": i * 5 + 4} for i in range(20)]
        self.assertEqual(coverage_check.coverage_warnings(ok, 100), [])

    def test_stored_warnings(self) -> None:
        data = [{"start": 0, "end": 1, "coverage_warnings": [{"kind": "gap", "start": 1, "end": 99}]}]
        self.assertEqual(len(coverage_check.warnings_from_json_data(data)), 1)
        self.assertEqual(coverage_check.mean_no_speech([{"no_speech_prob": 0.9}, {"no_speech_prob": 0.7}]), 0.8)


class CaptionsTest(unittest.TestCase):
    def test_json3(self) -> None:
        data = json.loads((FIX / "captions_small.json3").read_text(encoding="utf-8"))
        segs = yc.json3_to_segments(data, yc.SOURCE_MANUAL)
        self.assertEqual([s["text"].strip() for s in segs], ["Your love is my quiet shelter", "when every light begins to fade"])
        self.assertEqual((segs[0]["start"], segs[0]["end"]), (1.0, 4.0))  # cut at the next line
        self.assertEqual(segs[1]["source"], "youtube_manual")

    def _f(self, code_url):
        return [{"ext": "vtt", "url": code_url + "&fmt=vtt"}, {"ext": "json3", "url": code_url}]

    def test_track_order(self) -> None:
        info = {"subtitles": {"en": self._f("m-en")},
                "automatic_captions": {"en-orig": self._f("a-orig"), "en": self._f("a-en")}}
        self.assertEqual(yc.pick_track(info), ("youtube_manual", "en", "m-en"))
        info["subtitles"] = {"de": self._f("m-de")}
        self.assertEqual(yc.pick_track(info), ("youtube_auto", "en-orig", "a-orig"))
        del info["automatic_captions"]["en-orig"]
        self.assertEqual(yc.pick_track(info), ("youtube_auto", "en", "a-en"))

    def test_never_translated(self) -> None:
        info = {"automatic_captions": {"en": self._f("x?v=1&tlang=en"), "de-orig": self._f("d")}}
        self.assertIsNone(yc.pick_track(info))
        self.assertIsNone(yc.pick_track({"automatic_captions": {"en-de": self._f("z")}}))

    def test_429_retry(self) -> None:
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("HTTP Error 429: Too Many Requests")
            return "ok"
        with patch.object(yc, "MIN_INTERVAL_S", 0):
            self.assertEqual(yc.with_429_retry(fn, sleep=lambda s: None), "ok")
        self.assertEqual(len(calls), 3)

    def test_fallbacks(self) -> None:
        logs = []
        self.assertIsNone(yc.try_captions("u", "vid", {}, fetcher=lambda *a: None, log=logs.append))
        def boom(*a):
            raise RuntimeError("HTTP Error 429")
        self.assertIsNone(yc.try_captions("u", "vid", {}, fetcher=boom, log=logs.append))
        gappy = ("youtube_auto", "en", [{"start": 0, "end": 2, "text": " hi", "source": "youtube_auto"}], {"duration": 600})
        self.assertIsNone(yc.try_captions("u", "vid", {}, fetcher=lambda *a: gappy, log=logs.append))
        self.assertEqual(len(logs), 3)

    def test_writes_outputs(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dirs = {"completed_transcripts": f"{d}/t", "completed_json": f"{d}/j", "completed_csv": f"{d}/c"}
            segs = [{"start": i * 4.0, "end": i * 4.0 + 3, "text": f" line {i}", "source": "youtube_manual"} for i in range(10)]
            got = ("youtube_manual", "en", segs, {"duration": 40, "title": "Song: A/B"})
            with patch("transcript_search.index_transcript"):
                res = yc.try_captions("u", "abcdefghijk", dirs, fetcher=lambda *a: got, publish=False, log=lambda m: None)
            self.assertEqual(res["source"], "youtube_manual")
            self.assertEqual(res["stem"], "Song_ A_B [abcdefghijk]")
            for k in ("txt", "json", "srt", "vtt", "csv"):
                self.assertTrue(Path(res["paths"][k]).is_file(), k)
            js = json.loads(Path(res["paths"]["json"]).read_text(encoding="utf-8"))
            self.assertEqual(js[0]["source"], "youtube_manual")
            self.assertEqual(js[0]["coverage_warnings"], [])



class AutoRetryTest(unittest.TestCase):
    def test_auto_reruns_with_music(self) -> None:
        import asyncio
        import tempfile
        from types import SimpleNamespace as NS
        calls = []

        class FakeModel:
            def transcribe(self, path, **kw):
                calls.append(kw)
                if kw["vad_filter"]:  # speech pass: one stretched line, like the bad SRT
                    segs = [NS(start=0.0, end=300.0, text=" one line", avg_logprob=-0.3, no_speech_prob=0.2, compression_ratio=1.1)]
                else:
                    segs = [NS(start=i * 5.0, end=i * 5.0 + 4, text=f" line {i}", avg_logprob=-0.3, no_speech_prob=0.2,
                               compression_ratio=1.1) for i in range(60)]
                return iter(segs), NS(language="en", language_probability=0.99)

        with tempfile.TemporaryDirectory() as d:
            async def dur(_p):
                return 300.0
            with patch.object(bt, "COMPLETED_TRANSCRIPTS_DIR", d), patch.object(bt, "COMPLETED_CSV_DIR", d), \
                    patch.object(bt, "COMPLETED_JSON_DIR", d), patch.object(bt, "use_openai_api_for_transcription", False), \
                    patch.object(bt, "gpu_available_for_whisper", lambda: False), \
                    patch.object(bt, "get_audio_duration", dur), \
                    patch.object(bt, "get_whisper_model", lambda *a: FakeModel()), \
                    patch.object(bt, "resolve_decode_profile", lambda *a: "auto"), \
                    patch.object(bt, "resolve_batched_mode", lambda: (False, 8)), \
                    patch.object(bt.asr_engines, "apply_engine_pipeline", lambda a, n, segs, *r, **k: segs), \
                    patch.object(bt.transcript_search, "after_transcript", lambda *a: None):
                _t, segs, _s = asyncio.run(bt.compute_transcript_with_whisper_from_audio_func("x.m4a", "song", 1.0))
            # CPU attempts pass vad False, but the speech profile forces the filter on; music keeps it off
            self.assertEqual([c["vad_filter"] for c in calls], [True, False])
            self.assertEqual(len(segs), 60)
            self.assertEqual(segs[0]["profile"], "music")
            self.assertEqual(segs[0]["source"], "whisper")
            js = json.loads((Path(d) / "song.json").read_text(encoding="utf-8"))
            self.assertEqual(js[0]["coverage_warnings"], [])


if __name__ == "__main__":
    unittest.main()
