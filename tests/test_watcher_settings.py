import json
import tempfile
import unittest
from pathlib import Path

from watcher_settings import load_settings, save_settings


class WatcherSettingsTest(unittest.TestCase):
    def test_completed_video_dir_syncs_legacy_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_settings(root, {"completed_video_dir": "completed_videos_custom"})
            loaded = load_settings(root)
            self.assertEqual(loaded["completed_video_dir"], "completed_videos_custom")
            self.assertEqual(loaded["completed_dir"], "completed_videos_custom")

    def test_completed_dir_syncs_canonical_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_settings(root, {"completed_dir": "legacy_completed"})
            loaded = load_settings(root)
            self.assertEqual(loaded["completed_dir"], "legacy_completed")
            self.assertEqual(loaded["completed_video_dir"], "legacy_completed")

    def test_whisper_model_defaults_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loaded = load_settings(root)
            self.assertEqual(loaded["whisper_gpu_model"], "large-v3")
            self.assertEqual(loaded["whisper_cpu_model"], "medium")
            save_settings(root, {"whisper_gpu_model": "small", "whisper_cpu_model": "tiny"})
            loaded = load_settings(root)
            self.assertEqual(loaded["whisper_gpu_model"], "small")
            self.assertEqual(loaded["whisper_cpu_model"], "tiny")


if __name__ == "__main__":
    unittest.main()
