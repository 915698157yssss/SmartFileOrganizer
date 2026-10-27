import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sfo.classifier import (
    Prediction,
    TfidfClassifier,
    build_feature_text,
    evaluate,
    split_holdout,
)


def _sample(doc_id, target, title, excerpt):
    return {"doc_id": doc_id, "target": target, "title": title, "ext": ".txt", "excerpt": excerpt}


def make_samples():
    groups = {
        "合同/供应商": [
            ("设备采购合同", "甲方 乙方 合同 条款 违约 签署 供货 采购 付款"),
            ("技术服务协议", "甲方 乙方 协议 条款 服务 期限 违约 签署"),
            ("供货合同补充协议", "甲方 乙方 合同 补充 条款 供货 签署"),
            ("设备安装合同", "甲方 乙方 合同 安装 验收 条款 签署"),
        ],
        "财务/发票": [
            ("服务费发票", "发票 税额 价税合计 开票 发票号 报销"),
            ("增值税专用发票", "发票 税额 价税合计 开票 发票号 抵扣"),
            ("运输费发票", "发票 税额 价税合计 开票 运输"),
            ("开票申请", "发票 开票 申请 税额 报销"),
        ],
        "项目/验收": [
            ("一期竣工验收报告", "验收 竣工 试运行 整改 缺陷 移交"),
            ("设备验收单", "验收 设备 合格 缺陷 整改"),
            ("验收意见", "验收 意见 竣工 整改 移交"),
            ("试运行报告", "试运行 验收 竣工 缺陷"),
        ],
    }
    samples = []
    doc_id = 1
    for target, docs in groups.items():
        for title, excerpt in docs:
            samples.append(_sample(doc_id, target, title, excerpt))
            doc_id += 1
    return samples


class ClassifierTest(unittest.TestCase):
    def test_feature_excludes_folder(self):
        text = build_feature_text("合同.pdf", ".pdf", "正文内容")
        self.assertIn("合同", text)
        self.assertIn("正文内容", text)

    def test_fit_and_predict(self):
        model = TfidfClassifier()
        model.fit(make_samples())
        preds = model.predict(build_feature_text("新设备采购合同", ".docx", "甲方 乙方 合同 条款 供货"))
        self.assertTrue(preds)
        self.assertEqual(preds[0].target, "合同/供应商")
        self.assertGreater(preds[0].score, 0.0)
        self.assertEqual(preds[0].score, max(p.score for p in preds))

    def test_predict_empty_model(self):
        model = TfidfClassifier()
        self.assertEqual(model.predict("任意文本"), [])

    def test_evaluate(self):
        result = evaluate(make_samples(), holdout=0.25, target_precision=0.9)
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["top1"], 0.6)
        self.assertIn("recommended", result)
        self.assertIn("curve", result)

    def test_split_keeps_small_classes(self):
        samples = [_sample(i, "only", f"f{i}", "内容") for i in range(2)]
        train, test = split_holdout(samples)
        self.assertEqual(len(train), 2)
        self.assertEqual(test, [])


if __name__ == "__main__":
    unittest.main()
