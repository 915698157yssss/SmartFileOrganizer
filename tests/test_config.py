import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sfo.config import Config
from tutil import cleanup, make_temp_dir


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.base = make_temp_dir("config")
        self.library = self.base / "library"
        self.inbox = self.base / "inbox"
        self.data = self.base / "data"
        for directory in (self.library, self.inbox, self.data):
            directory.mkdir()

    def tearDown(self):
        cleanup(self.base)

    def _cfg(self, **kwargs):
        cfg = Config(
            data_dir=str(self.data),
            watch_dirs=[str(self.inbox)],
            library_root=str(self.library),
            **kwargs,
        )
        cfg.normalize()
        return cfg

    def test_valid_config(self):
        self.assertEqual(self._cfg().validate(), [])

    def test_watch_inside_library_rejected(self):
        inside = self.library / "inbox"
        inside.mkdir()
        cfg = self._cfg()
        cfg.watch_dirs = [str(inside)]
        self.assertTrue(any("不能互相包含" in error for error in cfg.validate()))

    def test_watch_inside_data_rejected(self):
        inside = self.data / "inbox"
        inside.mkdir()
        cfg = self._cfg()
        cfg.watch_dirs = [str(inside)]
        self.assertTrue(any("数据目录" in error for error in cfg.validate()))

    def test_missing_paths_reported(self):
        cfg = self._cfg()
        cfg.library_root = str(self.base / "nope")
        cfg.watch_dirs = [str(self.base / "nope2")]
        errors = cfg.validate()
        self.assertTrue(any("资料库不存在" in error for error in errors))
        self.assertTrue(any("监听目录不存在" in error for error in errors))

    def test_normalize_clamps_thresholds(self):
        cfg = self._cfg(auto_threshold=2, suggest_threshold=3, margin=-1)
        self.assertLessEqual(cfg.auto_threshold, 0.99)
        self.assertLessEqual(cfg.suggest_threshold, cfg.auto_threshold)
        self.assertEqual(cfg.margin, 0.0)

    def test_save_and_load_roundtrip(self):
        cfg = self._cfg(mode="observe")
        cfg.save()
        loaded = Config.load(self.data)
        self.assertEqual(loaded.mode, "observe")
        self.assertEqual(loaded.library_root, str(self.library))


if __name__ == "__main__":
    unittest.main()
