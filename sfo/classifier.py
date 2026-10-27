"""分类器：TF-IDF（默认，零模型下载）与 sentence-transformers（可选）。

训练与推理共用 build_feature_text()，避免 v6 那种「训练只用文件名、
推理却想用正文」的分布错位。

TF-IDF 采用稀疏倒排表实现，内存远低于稠密矩阵：几千份文档只需几十 MB，
10 万份也能撑住；预测是纯 Python 的稀疏点积，单次约 1ms 量级。
"""

from __future__ import annotations

import math
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .textutil import clean_filename, tokenize

MAX_VOCAB = 60000
SINGLE_CHAR_WEIGHT = 0.5


def build_feature_text(title: str, ext: str = "", excerpt: str = "") -> str:
    """训练和推理必须调用同一个函数。

    注意：不要把「文件当前所在目录」放进特征——扫描资料库时它就是标签本身，
    会造成标签泄漏；推理时又拿不到目标目录，分布会错位。
    """
    parts = [clean_filename(title)]
    if ext:
        parts.append(ext.lstrip(".").lower())
    if excerpt:
        parts.append(excerpt)
    return "\n".join(p for p in parts if p)


@dataclass
class Prediction:
    target: str
    score: float
    doc_id: Optional[int] = None
    centroid: float = 0.0
    matched_title: str = ""

    @property
    def label(self) -> str:
        return f"{self.target} ({self.score:.2f})"


@dataclass
class ModelInfo:
    backend: str = "tfidf"
    docs: int = 0
    labels: int = 0
    targets: int = 0
    vocab: int = 0
    index_seconds: float = 0.0
    extra: dict = field(default_factory=dict)


class TfidfClassifier:
    name = "tfidf"

    def __init__(self) -> None:
        self.postings: list[dict[int, float]] = []
        self.doc_norm: list[float] = []
        self.doc_target: list[str] = []
        self.doc_id: list[Optional[int]] = []
        self.doc_title: list[str] = []
        self.vocab: dict[str, int] = {}
        self.idf: list[float] = []
        self.centroids: dict[str, dict[int, float]] = {}
        self.info = ModelInfo(backend="tfidf")

    # ------------------------------------------------------------------ fit
    def fit(self, samples: list[dict]) -> None:
        if not samples:
            raise ValueError("没有训练样本")
        prepared: list[tuple[Counter, str, Optional[int], str]] = []
        df: Counter[str] = Counter()
        for sample in samples:
            text = build_feature_text(
                sample.get("title", ""),
                sample.get("ext", ""),
                sample.get("excerpt", ""),
            )
            counts = Counter(tokenize(text))
            if not counts:
                continue
            prepared.append(
                (counts, str(sample["target"]), sample.get("doc_id"), sample.get("title", ""))
            )
            df.update(counts.keys())

        if not prepared:
            raise ValueError("样本没有可用词元")

        # 词表按文档频率降序截断；极高频的虚词由 idf 自动降权
        vocab_tokens = sorted(df.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_VOCAB]
        self.vocab = {token: idx for idx, (token, _) in enumerate(vocab_tokens)}
        n_docs = len(prepared)
        self.idf = [
            math.log((1 + n_docs) / (1 + df[token])) + 1.0 for token, _ in vocab_tokens
        ]

        self.postings = [dict() for _ in range(len(self.vocab))]
        self.doc_norm = []
        self.doc_target = []
        self.doc_id = []
        self.doc_title = []
        self.centroids = {}
        centroid_sums: dict[str, dict[int, float]] = defaultdict(dict)

        for counts, target, doc_id, title in prepared:
            weights: dict[int, float] = {}
            for token, count in counts.items():
                idx = self.vocab.get(token)
                if idx is None:
                    continue
                weight = (1.0 + math.log(count)) * self.idf[idx]
                if len(token) == 1 and not token.isascii():
                    weight *= SINGLE_CHAR_WEIGHT
                weights[idx] = weights.get(idx, 0.0) + weight
            if not weights:
                continue
            norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
            doc_index = len(self.doc_norm)
            self.doc_norm.append(norm)
            self.doc_target.append(target)
            self.doc_id.append(doc_id)
            self.doc_title.append(title)
            bucket = centroid_sums[target]
            for idx, weight in weights.items():
                normalized = weight / norm
                self.postings[idx][doc_index] = normalized
                bucket[idx] = bucket.get(idx, 0.0) + normalized

        for target, bucket in centroid_sums.items():
            norm = math.sqrt(sum(v * v for v in bucket.values())) or 1.0
            self.centroids[target] = {idx: v / norm for idx, v in bucket.items()}

        self.info = ModelInfo(
            backend="tfidf",
            docs=len(self.doc_norm),
            labels=len(samples),
            targets=len(self.centroids),
            vocab=len(self.vocab),
            extra={"avg_norm": round(sum(self.doc_norm) / max(len(self.doc_norm), 1), 3)},
        )

    # -------------------------------------------------------------- predict
    def _query_vector(self, text: str) -> tuple[dict[int, float], float]:
        counts = Counter(token for token in tokenize(text) if token in self.vocab)
        if not counts:
            return {}, 0.0
        weights: dict[int, float] = {}
        for token, count in counts.items():
            idx = self.vocab[token]
            weight = (1.0 + math.log(count)) * self.idf[idx]
            if len(token) == 1 and not token.isascii():
                weight *= SINGLE_CHAR_WEIGHT
            weights[idx] = weights.get(idx, 0.0) + weight
        norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
        return weights, norm

    def predict(self, text: str, top_k: int = 5) -> list[Prediction]:
        query, q_norm = self._query_vector(text)
        if not query or not self.postings:
            return []
        scores: dict[int, float] = defaultdict(float)
        for idx, weight in query.items():
            for doc_index, doc_weight in self.postings[idx].items():
                scores[doc_index] += weight * doc_weight
        scores = {d: s / q_norm for d, s in scores.items()}

        best_per_target: dict[str, tuple[float, int]] = {}
        for doc_index, score in scores.items():
            target = self.doc_target[doc_index]
            current = best_per_target.get(target)
            if current is None or score > current[0]:
                best_per_target[target] = (score, doc_index)

        ranked = sorted(best_per_target.items(), key=lambda kv: kv[1][0], reverse=True)
        results: list[Prediction] = []
        for target, (score, doc_index) in ranked[:top_k]:
            centroid_score = 0.0
            centroid = self.centroids.get(target, {})
            if centroid:
                dot = sum(weight * centroid.get(idx, 0.0) for idx, weight in query.items())
                centroid_score = dot / q_norm
            results.append(
                Prediction(
                    target=target,
                    score=round(float(score), 6),
                    doc_id=self.doc_id[doc_index],
                    centroid=round(float(centroid_score), 6),
                    matched_title=self.doc_title[doc_index],
                )
            )
        return results


class SentenceTransformerClassifier:
    """可选后端：效果通常更好，但需要 torch + 模型文件。"""

    name = "st"

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.environ.get(
            "SFO_ST_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.model = None
        self.matrix = None
        self.targets: list[str] = []
        self.doc_ids: list[Optional[int]] = []
        self.centroids: dict[str, "object"] = {}
        self.info = ModelInfo(backend="st")

    def _encode(self, texts: list[str]):
        import numpy as np

        vectors = self.model.encode(
            texts, batch_size=16, show_progress_bar=False, normalize_embeddings=True
        )
        return np.asarray(vectors, dtype="float32")

    def fit(self, samples: list[dict]) -> None:
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(self.model_name)
        texts = [
            build_feature_text(
                sample.get("title", ""),
                sample.get("ext", ""),
                sample.get("excerpt", ""),
            )
            for sample in samples
        ]
        self.matrix = self._encode(texts)
        self.targets = [str(sample["target"]) for sample in samples]
        self.doc_ids = [sample.get("doc_id") for sample in samples]

        buckets: dict[str, list] = defaultdict(list)
        for target, vector in zip(self.targets, self.matrix):
            buckets[target].append(vector)
        self.centroids = {}
        for target, vectors in buckets.items():
            centroid = np.mean(np.stack(vectors), axis=0)
            norm = float(np.linalg.norm(centroid)) or 1.0
            self.centroids[target] = centroid / norm
        self.info = ModelInfo(
            backend="st",
            docs=len(texts),
            labels=len(samples),
            targets=len(self.centroids),
            extra={"model": self.model_name},
        )

    def predict(self, text: str, top_k: int = 5) -> list[Prediction]:
        if self.matrix is None:
            return []
        vector = self._encode([text])[0]
        sims = self.matrix @ vector
        best: dict[str, tuple[float, int]] = {}
        for index, score in enumerate(sims):
            target = self.targets[index]
            current = best.get(target)
            if current is None or float(score) > current[0]:
                best[target] = (float(score), index)
        ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:top_k]
        results = []
        for target, (score, index) in ranked:
            centroid = self.centroids.get(target)
            centroid_score = float(centroid @ vector) if centroid is not None else 0.0
            results.append(
                Prediction(
                    target=target,
                    score=round(score, 6),
                    doc_id=self.doc_ids[index],
                    centroid=round(centroid_score, 6),
                )
            )
        return results


def build_classifier(backend: str = "tfidf"):
    if backend == "st":
        return SentenceTransformerClassifier()
    return TfidfClassifier()


# ---------------------------------------------------------------- 评测
def split_holdout(
    samples: list[dict], holdout: float = 0.2, seed: int = 42
) -> tuple[list[dict], list[dict]]:
    """按目标目录分层留出；小类全部进训练，避免留出样本为 0。"""
    rng = random.Random(seed)
    by_target: dict[str, list[dict]] = defaultdict(list)
    for sample in samples:
        by_target[str(sample["target"])].append(sample)

    train: list[dict] = []
    test: list[dict] = []
    for target, items in by_target.items():
        items = list(items)
        rng.shuffle(items)
        if len(items) >= 5:
            n_test = max(1, int(round(len(items) * holdout)))
        elif len(items) >= 3:
            n_test = 1
        else:
            n_test = 0
        test.extend(items[:n_test])
        train.extend(items[n_test:])
    return train, test


def evaluate(
    samples: list[dict],
    backend: str = "tfidf",
    holdout: float = 0.2,
    seed: int = 42,
    target_precision: float = 0.95,
    margin_threshold: float = 0.05,
) -> dict:
    """留出集评测 + 阈值扫描，回答「自动归档能覆盖多少、错多少」。"""
    if len(samples) < 5:
        return {"ok": False, "error": "样本太少（至少 5 条）"}
    train, test = split_holdout(samples, holdout, seed)
    if len(train) < 2 or not test:
        return {"ok": False, "error": "留出后训练/测试样本不足（每类至少 3 条）"}

    classifier = build_classifier(backend)
    classifier.fit(train)
    rows = []
    confusion: Counter[tuple[str, str]] = Counter()
    for sample in test:
        text = build_feature_text(
            sample.get("title", ""),
            sample.get("ext", ""),
            sample.get("excerpt", ""),
        )
        predictions = classifier.predict(text, top_k=3)
        truth = str(sample["target"])
        top1 = predictions[0] if predictions else None
        margin = 0.0
        if len(predictions) >= 2:
            margin = predictions[0].score - predictions[1].score
        correct = bool(top1 and top1.target == truth)
        top3 = any(p.target == truth for p in predictions[:3])
        if top1 and not correct:
            confusion[(truth, top1.target)] += 1
        rows.append(
            {
                "truth": truth,
                "pred": top1.target if top1 else "",
                "score": top1.score if top1 else 0.0,
                "margin": margin,
                "correct": correct,
                "top3": top3,
            }
        )

    total = len(rows)
    top1_acc = sum(1 for r in rows if r["correct"]) / total
    top3_recall = sum(1 for r in rows if r["top3"]) / total

    curve = []
    best = None
    for step in range(10, 96, 2):
        threshold = step / 100
        covered = [r for r in rows if r["score"] >= threshold and r["margin"] >= margin_threshold]
        if not covered:
            curve.append({"threshold": threshold, "coverage": 0.0, "precision": None})
            continue
        correct = sum(1 for r in covered if r["correct"])
        precision = correct / len(covered)
        coverage = len(covered) / total
        # 小样本下 4/4 全对并不等于 95% 精度，用 Laplace 平滑后再判断
        smoothed = (correct + 1) / (len(covered) + 2)
        curve.append(
            {
                "threshold": threshold,
                "coverage": round(coverage, 3),
                "precision": round(precision, 3),
                "effective_precision": round(smoothed, 3),
            }
        )
        if len(covered) < 5 or smoothed < target_precision:
            continue
        candidate = {
            "threshold": threshold,
            "coverage": round(coverage, 3),
            "precision": round(precision, 3),
        }
        if (
            best is None
            or candidate["coverage"] > best["coverage"]
            or (
                abs(candidate["coverage"] - best["coverage"]) < 1e-9
                and candidate["threshold"] > best["threshold"]
            )
        ):
            best = candidate

    return {
        "ok": True,
        "backend": backend,
        "train": len(train),
        "test": len(test),
        "classes": len({s["target"] for s in samples}),
        "top1": round(top1_acc, 4),
        "top3": round(top3_recall, 4),
        "recommended": best
        or {
            "threshold": None,
            "coverage": 0.0,
            "precision": None,
            "note": "留出样本不足 5 条或无法在目标精度下给出阈值，建议扩大资料库样本",
        },
        "curve": curve,
        "confusion": [
            {"truth": truth, "pred": pred, "count": count}
            for (truth, pred), count in confusion.most_common(10)
        ],
        "errors": [
            {"truth": r["truth"], "pred": r["pred"], "score": round(r["score"], 3)}
            for r in rows
            if not r["correct"]
        ][:20],
    }


def model_info(classifier) -> dict:
    if classifier is None:
        return {"backend": "", "docs": 0, "targets": 0, "vocab": 0}
    info = classifier.info
    return {
        "backend": info.backend,
        "docs": info.docs,
        "labels": info.labels,
        "targets": info.targets,
        "vocab": info.vocab,
    }
