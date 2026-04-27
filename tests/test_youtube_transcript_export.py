import unittest

from youtube_transcript_export import (
    escape_vtt_text,
    extract_playlist_id,
    extract_video_id,
    sanitize_filename,
    segments_to_vtt,
)


class ParseTest(unittest.TestCase):
    def test_video_watch(self) -> None:
        self.assertEqual(
            extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_video_short(self) -> None:
        self.assertEqual(extract_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_playlist(self) -> None:
        u = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        self.assertEqual(extract_playlist_id(u), "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")


class VttTest(unittest.TestCase):
    def test_escape(self) -> None:
        self.assertEqual(escape_vtt_text("a & b < c"), "a &amp; b &lt; c")

    def test_segments_to_vtt(self) -> None:
        segs = [{"text": "Hi", "start": 1.0, "duration": 2.0}]
        v = segments_to_vtt(segs, note="t")
        self.assertIn("WEBVTT", v)
        self.assertIn("00:00:01.000 --> 00:00:03.000", v)
        self.assertIn("Hi", v)

    def test_sanitize(self) -> None:
        self.assertNotIn(":", sanitize_filename('a:b"c'))


if __name__ == "__main__":
    unittest.main()
