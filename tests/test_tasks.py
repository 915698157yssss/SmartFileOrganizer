import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sfo.tasks import (
    detect_doc_type,
    export_ics,
    extract_drafts,
    find_amount,
    find_due,
)
from tutil import cleanup, make_temp_dir

BASE = date(2026, 10, 27)


class TaskDateTest(unittest.TestCase):
    def test_full_date(self):
        due, evidence = find_due("请于2026年12月31日前完成整改并书面反馈", BASE)
        self.assertEqual(due, date(2026, 12, 31))
        self.assertIn("2026", evidence)

    def test_month_day(self):
        due, _ = find_due("请于11月10日前报送自查报告", BASE)
        self.assertEqual(due, date(2026, 11, 10))

    def test_relative_days(self):
        due, _ = find_due("请自收到之日起30日内反馈", BASE)
        self.assertEqual(due, date(2026, 11, 26))

    def test_business_days(self):
        due, _ = find_due("3个工作日内提交材料", BASE)
        self.assertEqual(due, date(2026, 10, 30))

    def test_month_end(self):
        due, _ = find_due("本月底前完成结算", BASE)
        self.assertEqual(due, date(2026, 10, 31))

    def test_no_deadline(self):
        due, evidence = find_due("本次会议讨论了二期提标改造方案", BASE)
        self.assertIsNone(due)
        self.assertEqual(evidence, "")


class TaskExtractTest(unittest.TestCase):
    def test_doc_type(self):
        self.assertEqual(detect_doc_type("设备采购合同", "甲方 乙方 签订")[0], "合同协议")
        self.assertEqual(detect_doc_type("增值税发票", "价税合计 税额")[0], "发票票据")
        self.assertEqual(detect_doc_type("竣工验收报告", "验收意见")[0], "验收资料")

    def test_amount(self):
        value, _ = find_amount("合同金额：120万元")
        self.assertEqual(value, 1_200_000)
        value, _ = find_amount("价税合计：12,000.50元")
        self.assertEqual(value, 12_000.5)

    def test_draft_created(self):
        drafts = extract_drafts(
            {
                "id": 1,
                "title": "关于开展安全检查的通知",
                "ext": ".docx",
                "excerpt": "各部门：请于2026年11月10日前完成自查并报送自查报告。特急",
                "folder": "行政/通知",
            },
            base=BASE,
        )
        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        self.assertEqual(draft.due, "2026-11-10")
        self.assertEqual(draft.dtype, "通知函件")
        self.assertEqual(draft.priority, 2)
        self.assertIn("2026年11月10日", draft.note)

    def test_plain_file_no_task(self):
        drafts = extract_drafts(
            {"id": 2, "title": "随手记", "ext": ".txt", "excerpt": "今天天气不错", "folder": ""},
            base=BASE,
        )
        self.assertEqual(drafts, [])

    def test_ics(self):
        drafts = extract_drafts(
            {"id": 3, "title": "验收整改通知", "ext": ".txt",
             "excerpt": "请于2026年12月31日前完成整改", "folder": "项目/验收"},
            base=BASE,
        )
        row = {
            "id": 7,
            "title": drafts[0].title,
            "due": drafts[0].due,
            "note": drafts[0].note,
            "status": "todo",
        }
        tmp = make_temp_dir("ics")
        try:
            path = str(tmp / "tasks.ics")
            count = export_ics([row], path)
            content = Path(path).read_text(encoding="utf-8")
        finally:
            cleanup(tmp)
        self.assertEqual(count, 1)
        self.assertIn("BEGIN:VEVENT", content)
        self.assertIn("20261231", content)


if __name__ == "__main__":
    unittest.main()
