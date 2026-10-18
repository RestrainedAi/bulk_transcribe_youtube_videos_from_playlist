import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import transcript_search as ts

SEGS = [
    {"start": 0.0, "end": 2.5, "text": " Hello and welcome to the family court video."},
    {"start": 2.5, "end": 3661.2, "text": " Narcissistic parents often exploit the court system."},
    {"start": 3661.2, "end": 3661.2, "text": "   "},
]


class SubtitleTest(unittest.TestCase):
    def test_srt_format(self):
        srt = ts.to_srt(SEGS)
        self.assertTrue(srt.startswith("1\n00:00:00,000 --> 00:00:02,500\nHello and welcome"))
        self.assertIn("2\n00:00:02,500 --> 01:01:01,200\nNarcissistic", srt)
        self.assertNotIn("3\n", srt)  # empty segments are skipped

    def test_vtt_format(self):
        vtt = ts.to_vtt(SEGS)
        self.assertTrue(vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:02.500\n"))

    def test_zero_length_cue_gets_minimum_duration(self):
        srt = ts.to_srt([{"start": 5, "end": 5, "text": "x"}])
        self.assertIn("00:00:05,000 --> 00:00:05,200", srt)


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.db = self.d / "idx.sqlite"
        (self.d / "json").mkdir()
        (self.d / "txt").mkdir()
        (self.d / "vid").mkdir()
        import json
        (self.d / "json" / "Ramani talk.json").write_text(json.dumps(SEGS), encoding="utf-8")
        (self.d / "json" / "Other.json").write_text(json.dumps([{"start": 1, "end": 2, "text": "Ohio State research on aggression"}]), encoding="utf-8")
        (self.d / "json" / "broken.json").write_text("{not json", encoding="utf-8")
        (self.d / "vid" / "Ramani talk.mp4").write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_backfill_writes_subtitles_and_indexes(self):
        r = ts.backfill(self.d / "json", self.d / "txt", [self.d / "vid"], db_path=self.db)
        self.assertEqual(r, {"indexed": 2, "subtitles": 2, "failed": 1})
        self.assertTrue((self.d / "txt" / "Ramani talk.srt").is_file())
        self.assertTrue((self.d / "txt" / "Ramani talk.vtt").is_file())
        hits = ts.search("court", db_path=self.db)
        self.assertEqual({h["stem"] for h in hits}, {"Ramani talk"})
        self.assertEqual(hits[0]["video_path"], str(self.d / "vid" / "Ramani talk.mp4"))
        self.assertIn("[", hits[0]["context"])

    def test_phrase_prefix_and_reindex(self):
        ts.backfill(self.d / "json", self.d / "txt", [], db_path=self.db)
        self.assertEqual(len(ts.search('"court system"', db_path=self.db)), 1)
        self.assertEqual(len(ts.search("narciss", db_path=self.db)), 1)  # prefix match
        self.assertEqual(ts.search("aggression ohio", db_path=self.db)[0]["stem"], "Other")
        ts.backfill(self.d / "json", self.d / "txt", [], db_path=self.db)  # re-run must not duplicate rows
        self.assertEqual(len(ts.search("court", db_path=self.db)), 2)
        self.assertEqual(ts.stats(self.db)[0], 2)

    def test_hostile_queries_do_not_crash(self):
        ts.backfill(self.d / "json", self.d / "txt", [], db_path=self.db)
        for q in ['"', "AND OR NOT", "(", "*", "court\"; DROP TABLE x", ""]:
            ts.search(q, db_path=self.db)

    def test_after_transcript_never_raises(self):
        ts.after_transcript("missing", self.d / "json", self.d / "txt")


class PlayerTest(unittest.TestCase):
    def test_player_commands(self):
        self.assertEqual(ts.player_command("vlc", "vlc.exe", "a.mp4", 61.0), ["vlc.exe", "--start-time=60.0", "a.mp4"])
        self.assertEqual(ts.player_command("mpc", "mpc.exe", "a.mp4", 61.0), ["mpc.exe", "a.mp4", "/start", "60000"])
        self.assertEqual(ts.player_command("mpv", "mpv.exe", "a.mp4", 0.5), ["mpv.exe", "--start=0.0", "a.mp4"])

    def test_default_player_fallback(self):
        with patch.object(ts, "find_player", return_value=None), patch.object(ts.os, "startfile", create=True) as sf:
            self.assertIn("default", ts.open_media_at("a.mp4", 10))
            sf.assert_called_once()


if __name__ == "__main__":
    unittest.main()
