"""判定策略：把「模型分数」翻译成「动不动文件」。

三条硬规则：
1. 观察模式永不移动；
2. 建议模式永不自动移动（必须人工确认）；
3. 自动模式要求「高分 + 与第二名有间隔」，宁可少动，不可错动。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classifier import Prediction
from .config import Config


@dataclass
class Decision:
    action: str                      # auto | suggest | observe | unknown
    target: str | None = None
    score: float = 0.0
    margin: float = 0.0
    reason: str = ""
    alternatives: list[tuple[str, float]] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.action in ("suggest", "observe", "unknown")


def decide(mode: str, predictions: list[Prediction], cfg: Config) -> Decision:
    if not predictions:
        return Decision(action="unknown", reason="没有可用的训练样本，无法给建议")

    top = predictions[0]
    margin = 0.0
    if len(predictions) > 1:
        margin = top.score - predictions[1].score
    alternatives = [(p.target, p.score) for p in predictions[:4]]

    if top.score < cfg.suggest_threshold:
        return Decision(
            action="unknown",
            target=top.target,
            score=top.score,
            margin=margin,
            reason=f"最高相似度 {top.score:.2f} 低于建议阈值 {cfg.suggest_threshold:.2f}",
            alternatives=alternatives,
        )

    if mode == "observe":
        return Decision(
            action="observe",
            target=top.target,
            score=top.score,
            margin=margin,
            reason="观察模式：仅记录建议，不移动文件",
            alternatives=alternatives,
        )

    if mode == "auto" and top.score >= cfg.auto_threshold and margin >= cfg.margin:
        return Decision(
            action="auto",
            target=top.target,
            score=top.score,
            margin=margin,
            reason=f"相似度 {top.score:.2f}，领先第二名 {margin:.2f}",
            alternatives=alternatives,
        )

    if mode == "auto":
        return Decision(
            action="suggest",
            target=top.target,
            score=top.score,
            margin=margin,
            reason=(
                f"自动条件不足（相似度 {top.score:.2f}/{cfg.auto_threshold:.2f}，"
                f"间隔 {margin:.2f}/{cfg.margin:.2f}），转人工确认"
            ),
            alternatives=alternatives,
        )

    return Decision(
        action="suggest",
        target=top.target,
        score=top.score,
        margin=margin,
        reason=f"建议模式：相似度 {top.score:.2f}，等待确认",
        alternatives=alternatives,
    )
