import json
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sfo.config import Config
from sfo.service import Service
from tutil import cleanup, make_temp_dir

LIBRARY = {
    "合同/供应商A/设备采购合同.txt": "设备采购合同\n甲方：眉山苏伊士\n乙方：成都设备公司\n合同金额：120万元\n签订日期：2026年6月1日",
    "合同/供应商A/技术服务协议.txt": "技术服务协议\n甲方：眉山苏伊士\n乙方：四川环保科技\n合同金额：35万元\n服务期限：2026年7月1日至2027年6月30日",
    "合同/供应商B/供货合同补充协议.txt": "供货合同补充协议\n甲方：眉山苏伊士\n乙方：重庆机电\n合同条款：供货范围调整\n签订日期：2026年8月1日",
    "财务/发票/服务费发票.txt": "增值税专用发票\n价税合计：12,000.00元\n开票日期：2026年6月30日\n发票号码：12345678",
    "财务/发票/运输费发票.txt": "增值税发票\n价税合计：3,500.00元\n开票日期：2026年7月2日\n发票号码：87654321",
    "财务/结算/一期工程结算单.txt": "工程结算单\n结算金额：286,500.00元\n请于2026年11月15日前完成对账并付款",
    "项目/验收/一期竣工验收报告.txt": "竣工验收报告\n验收意见：各单体运行正常\n存在问题：加药间标识缺失\n请于2026年12月31日前完成整改并书面反馈",
    "项目/验收/设备验收单.txt": "设备验收单\n设备名称：鼓风机\n验收结论：合格\n请于2026年12月20日前归档",
    "项目/验收/试运行报告.txt": "试运行报告\n试运行期间设备运行稳定\n验收前需完成缺陷整改",
    "会议纪要/第5次例会.txt": "会议纪要\n时间：2026年10月20日\n参会人员：张三、李四\n议定事项：完成二期可研",
    "会议纪要/安全专题会.txt": "会议纪要\n安全专题会\n参会人员：王五\n议定事项：开展安全检查",
    "行政/通知/安全检查通知.txt": "关于开展安全生产检查的通知\n请于2026年11月10日前完成自查并报送自查报告。特急",
}


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.base = make_temp_dir("service")
        self.library = self.base / "library"
        self.inbox = self.base / "inbox"
        self.data = self.base / "data"
        for directory in (self.library, self.inbox, self.data):
            directory.mkdir()
        for rel, content in LIBRARY.items():
            path = self.library / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.cfg = Config(
            data_dir=str(self.data),
            watch_dirs=[str(self.inbox)],
            library_root=str(self.library),
            mode="suggest",
        )
        self.cfg.normalize()
        self.cfg.save()
        self.service = Service(self.cfg)

    def tearDown(self):
        self.service.close()
        cleanup(self.base)

    def test_scan_builds_model(self):
        result = self.service.scan_library(sync=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["scanned"], len(LIBRARY))
        self.assertGreaterEqual(self.service.model_info.get("docs", 0), len(LIBRARY) - 1)
        self.assertEqual(self.service.counts()["targets"], 7)

    def test_suggest_resolve_undo_and_task(self):
        self.service.scan_library(sync=True)
        new_file = self.inbox / "关于一期验收整改的通知.txt"
        new_file.write_text(
            "关于一期验收整改的通知\n各参建单位：\n请于2026年12月31日前完成整改并书面反馈。\n特急",
            encoding="utf-8",
        )
        result = self.service.classify_now(str(new_file))
        self.assertEqual(result["action"], "suggest")
        self.assertTrue(new_file.exists(), "建议模式不得移动文件")
        pending = self.service.pending_rows()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["suggestion"], "项目\\验收")

        tasks = self.service.task_rows("all")
        self.assertTrue(tasks)
        self.assertEqual(tasks[0]["due"], "2026-12-31")

        self.service._resolve_pending(pending[0]["id"], "项目\\验收")
        moved = self.library / "项目/验收/关于一期验收整改的通知.txt"
        self.assertTrue(moved.exists())
        self.assertFalse(new_file.exists())
        self.assertEqual(self.service.pending_rows(), [])
        records = self.service.journal_rows()
        self.assertEqual(records[0]["status"], "done")

        ok, message = self.service.undo_now()
        self.assertTrue(ok, message)
        self.assertTrue(new_file.exists())
        self.assertFalse(moved.exists())
        self.assertEqual(self.service.journal_rows()[0]["status"], "undone")

    def test_duplicate_is_quarantined(self):
        self.service.scan_library(sync=True)
        first = self.inbox / "报告.txt"
        first.write_text("竣工验收报告\n验收意见：合格\n请于2026年12月31日前完成整改", encoding="utf-8")
        self.service.classify_now(str(first))
        second = self.inbox / "报告副本.txt"
        shutil.copyfile(first, second)
        result = self.service.classify_now(str(second))
        self.assertEqual(result["action"], "duplicate")
        self.assertFalse(second.exists())
        quarantined = list((self.library / "重复文件").rglob("报告副本.txt"))
        self.assertTrue(quarantined)

    def test_observe_mode_never_moves(self):
        self.service.scan_library(sync=True)
        self.cfg.mode = "observe"
        path = self.inbox / "试运行情况说明.txt"
        path.write_text("试运行报告\n设备运行稳定\n验收前完成整改", encoding="utf-8")
        result = self.service.classify_now(str(path))
        self.assertEqual(result["action"], "observe")
        self.assertTrue(path.exists())
        self.assertEqual(self.service.pending_rows()[0]["status"], "observed")

    def test_auto_mode_moves_and_undoes(self):
        self.service.scan_library(sync=True)
        self.cfg.mode = "auto"
        self.cfg.auto_threshold = 0.2
        self.cfg.margin = 0.0
        path = self.inbox / "设备验收单.txt"
        path.write_text("设备验收单\n设备名称：鼓风机\n验收结论：合格\n请于2026年12月20日前归档", encoding="utf-8")
        result = self.service.classify_now(str(path))
        self.assertEqual(result["action"], "auto", result)
        self.assertFalse(path.exists())
        self.assertTrue((self.library / result["target"] / "设备验收单.txt").exists())
        ok, message = self.service.undo_now()
        self.assertTrue(ok, message)
        self.assertTrue(path.exists())

    def test_import_v6(self):
        payload = {
            "version": "6.2",
            "memory": [
                {"text": "设备采购合同", "path": "合同/供应商A"},
                {"text": "服务费发票", "path": "财务/发票"},
            ],
        }
        path = self.base / "learning_data.db"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        result = self.service.import_v6(str(path), sync=True)
        self.assertEqual(result["count"], 2)
        self.assertGreaterEqual(self.service.counts()["labels"], 2)


if __name__ == "__main__":
    unittest.main()
