import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sfo.classifier import Prediction
from sfo.config import Config
from sfo.db import Database
from sfo.mover import MoveError, safe_move, undo_last, unique_path, validate_rel
from sfo.policy import decide
from tutil import cleanup, make_temp_dir


class MoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir("mover")
        self.library = self.tmp / "library"
        self.inbox = self.tmp / "inbox"
        self.library.mkdir()
        self.inbox.mkdir()
        self.db = Database(self.tmp / "test.db")

    def tearDown(self):
        self.db.close()
        cleanup(self.tmp)

    def test_validate_rel(self):
        self.assertTrue(validate_rel(str(self.library), "合同/2026"))
        with self.assertRaises(MoveError):
            validate_rel(str(self.library), "..\\outside")
        with self.assertRaises(MoveError):
            validate_rel(str(self.library), "C:\\Windows")
        with self.assertRaises(MoveError):
            validate_rel(str(self.library), "")

    def test_unique_path(self):
        first = self.library / "a.txt"
        first.write_text("1", encoding="utf-8")
        second = Path(unique_path(str(first)))
        self.assertNotEqual(first, second)

    def test_safe_move_and_undo(self):
        source = self.inbox / "合同.txt"
        source.write_text("设备采购合同", encoding="utf-8")
        target_dir = self.library / "合同"
        result = safe_move(str(source), str(target_dir), self.db, payload={"doc_id": 1, "target": "合同"})
        self.assertFalse(source.exists())
        self.assertTrue(Path(result.dst).exists())
        self.assertEqual(len(self.db.journal_rows()), 1)

        ok, message, payload = undo_last(self.db)
        self.assertTrue(ok, message)
        self.assertTrue(source.exists())
        self.assertFalse(Path(result.dst).exists())
        self.assertEqual(payload["restored"], str(source))
        self.assertEqual(self.db.journal_rows()[0]["status"], "undone")

    def test_move_missing_source(self):
        with self.assertRaises(MoveError):
            safe_move(str(self.inbox / "nope.txt"), str(self.library), self.db)


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(data_dir="x", auto_threshold=0.6, suggest_threshold=0.3, margin=0.05)

    def _preds(self, scores):
        return [Prediction(target=f"t{i}", score=score) for i, score in enumerate(scores)]

    def test_auto_when_confident(self):
        decision = decide("auto", self._preds([0.8, 0.4]), self.cfg)
        self.assertEqual(decision.action, "auto")
        self.assertEqual(decision.target, "t0")

    def test_auto_falls_back_when_margin_small(self):
        decision = decide("auto", self._preds([0.62, 0.60]), self.cfg)
        self.assertEqual(decision.action, "suggest")

    def test_suggest_mode_never_auto(self):
        decision = decide("suggest", self._preds([0.99, 0.1]), self.cfg)
        self.assertEqual(decision.action, "suggest")

    def test_observe_mode(self):
        decision = decide("observe", self._preds([0.99, 0.1]), self.cfg)
        self.assertEqual(decision.action, "observe")

    def test_low_score_unknown(self):
        decision = decide("auto", self._preds([0.1]), self.cfg)
        self.assertEqual(decision.action, "unknown")

    def test_no_predictions(self):
        decision = decide("auto", [], self.cfg)
        self.assertEqual(decision.action, "unknown")


if __name__ == "__main__":
    unittest.main()
