import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sfo.textutil import clean_filename, decode_bytes, human_size, sha256_file, tokenize
from tutil import cleanup, make_temp_dir


class TextUtilTest(unittest.TestCase):
    def test_clean_filename_strips_noise(self):
        self.assertEqual(clean_filename("2026.06.05 设备采购合同 最终版(1).docx"), "设备采购合同")
        self.assertEqual(clean_filename("扫描件001.pdf"), "扫描件001")
        self.assertIn("验收", clean_filename("一期竣工验收报告 V2.docx"))

    def test_tokenize_mixed(self):
        tokens = tokenize("一期竣工验收报告 PDF")
        self.assertIn("验收", tokens)
        self.assertIn("pdf", tokens)
        self.assertTrue(all(token.strip() for token in tokens))

    def test_decode_gbk(self):
        raw = "中文内容".encode("gbk")
        self.assertEqual(decode_bytes(raw), "中文内容")

    def test_sha_and_size(self):
        tmp = make_temp_dir("text")
        try:
            path = os.path.join(str(tmp), "a.txt")
            Path(path).write_text("hello", encoding="utf-8")
            self.assertEqual(len(sha256_file(path)), 64)
        finally:
            cleanup(tmp)
        self.assertEqual(human_size(2048), "2.0 KB")


if __name__ == "__main__":
    unittest.main()
