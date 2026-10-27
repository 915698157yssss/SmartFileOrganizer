"""从文档中抽取「工作任务」信号：类型、期限、金额、主体、动作。

这一步不依赖大模型：中文公文/合同/通知里，期限和金额的表达高度模式化，
规则 + 上下文窗口已经能覆盖大部分场景。后续可以在 `extract_drafts`
之后接一个可选的本地 LLM 做摘要与补全（见 README）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

from .textutil import clean_filename

FULL_DATE_RE = re.compile(r"(20\d{2})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})\s*日?")
MD_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
DOC_NO_RE = re.compile(r"[\u4e00-\u9fa5]{1,12}〔\s*20\d{2}\s*〕\s*\d{1,4}\s*号")
AMOUNT_RE = re.compile(
    r"(?:¥|￥)?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(万元|亿元|元)"
)

DEADLINE_HINTS = (
    "前", "之前", "截止", "期限", "有效期", "到期", "于", "至",
    "反馈", "报送", "提交", "完成", "整改", "回复", "签订", "验收", "办理",
)

DOC_TYPE_RULES: dict[str, tuple[str, ...]] = {
    "合同协议": ("合同", "协议", "甲方", "乙方", "签订", "条款", "违约", "盖章"),
    "发票票据": ("发票", "税额", "价税合计", "开票", "发票号", "票据"),
    "验收资料": ("验收", "竣工验收", "验收报告", "验收意见", "试运行", "移交"),
    "结算付款": ("结算", "付款", "请款", "对账", "支付", "货款", "工程款"),
    "通知函件": ("通知", "函", "请示", "批复", "通报", "公告", "督办"),
    "会议纪要": ("会议纪要", "纪要", "参会", "议程", "议定事项"),
    "报告总结": ("报告", "总结", "汇报", "情况说明", "自查"),
    "方案图纸": ("方案", "图纸", "设计", "施工图", "平面图", "布置图"),
    "证照资质": ("营业执照", "资质", "许可证", "证书", "认证"),
}

ACTION_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("验收", "竣工验收"), "组织验收"),
    (("付款", "结算", "请款", "支付"), "办理结算/付款"),
    (("回复", "反馈", "复函"), "书面反馈"),
    (("报送", "提交", "上报"), "按时报送"),
    (("整改", "纠正"), "落实整改"),
    (("签订", "签署", "盖章"), "审阅并签订"),
    (("会议", "参会"), "准备会议材料"),
    (("审核", "审批"), "审核审批"),
    (("备案",), "办理备案"),
)

URGENT_WORDS = ("紧急", "特急", "加急", "督办", "限期", "务必", "立即")


@dataclass
class TaskDraft:
    title: str
    dtype: str
    due: str | None
    priority: int
    action: str
    confidence: float
    note: str
    dedupe: str

    def to_row(self) -> dict:
        return asdict(self)


def detect_doc_type(title: str, text: str) -> tuple[str, int]:
    """标题命中权重更高：公文标题通常直接写明文种。"""
    body = (text or "")[:2000]
    best_type = "文档"
    best_score = 0
    for name, keywords in DOC_TYPE_RULES.items():
        score = 0
        for keyword in keywords:
            if keyword in title:
                score += 3
            elif keyword in body:
                score += 2 if len(keyword) >= 3 else 1
        if score > best_score:
            best_type, best_score = name, score
    return best_type, best_score


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _add_business_days(base: date, days: int) -> date:
    current = base
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def _month_end(base: date) -> date:
    if base.month == 12:
        return date(base.year, 12, 31)
    return date(base.year, base.month + 1, 1) - timedelta(days=1)


def find_due(text: str, base: date) -> tuple[date | None, str]:
    """返回 (到期日, 证据片段)。优先未来最近的期限；全部过期则取最近的一个。"""
    compact = unicodedata.normalize("NFKC", text or "")
    candidates: list[tuple[date, str]] = []

    for match in FULL_DATE_RE.finditer(compact):
        window = compact[max(0, match.start() - 20): match.end() + 20]
        if not any(hint in window for hint in DEADLINE_HINTS):
            continue
        value = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if value:
            candidates.append((value, window.strip()))

    for match in MD_DATE_RE.finditer(compact):
        window = compact[max(0, match.start() - 20): match.end() + 20]
        if not any(hint in window for hint in DEADLINE_HINTS):
            continue
        month, day = int(match.group(1)), int(match.group(2))
        year = base.year
        value = _safe_date(year, month, day)
        if value and value < base - timedelta(days=90):
            value = _safe_date(year + 1, month, day)
        if value:
            candidates.append((value, window.strip()))

    for match in re.finditer(
        r"(?:自[^。；\n]{0,30}?起)?\s*(?<!\d)(\d{1,3})\s*个?\s*(工作日|天|日)\s*(?:内|以内)",
        compact,
    ):
        count = int(match.group(1))
        unit = match.group(2)
        value = _add_business_days(base, count) if unit == "工作日" else base + timedelta(days=count)
        candidates.append((value, match.group(0).strip()))

    for match in re.finditer(r"(?:一周|1周|七个工作日|7个工作日)\s*(?:内|以内)", compact):
        value = _add_business_days(base, 7) if "工作日" in match.group(0) else base + timedelta(days=7)
        candidates.append((value, match.group(0).strip()))

    if re.search(r"本?月底前", compact):
        candidates.append((_month_end(base), "月底前"))
    next_month = re.search(r"下月\s*(\d{1,2})\s*[日号]前", compact)
    if next_month:
        first = date(base.year + (1 if base.month == 12 else 0), 1 if base.month == 12 else base.month + 1, 1)
        value = _safe_date(first.year, first.month, int(next_month.group(1)))
        if value:
            candidates.append((value, next_month.group(0)))

    if not candidates:
        return None, ""

    future = [c for c in candidates if c[0] >= base - timedelta(days=1)]
    chosen = min(future, key=lambda c: c[0]) if future else min(candidates, key=lambda c: c[0])
    evidence = " ".join(chosen[1].split())
    return chosen[0], evidence[:120]


def find_amount(text: str) -> tuple[float | None, str]:
    for match in AMOUNT_RE.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        unit = match.group(2)
        if unit == "万元":
            value *= 10000
        elif unit == "亿元":
            value *= 100000000
        if value >= 100:
            return value, match.group(0).strip()
    return None, ""


def find_parties(text: str) -> list[str]:
    parties = []
    for match in re.finditer(r"(甲方|乙方|采购人|供应商|承包方|发包方|承租方|出租方)\s*[:：]\s*([^\n，。；]{2,40})", text or ""):
        parties.append(f"{match.group(1)}：{match.group(2).strip()}")
        if len(parties) >= 4:
            break
    return parties


def find_doc_no(text: str) -> str:
    match = DOC_NO_RE.search(text or "")
    return match.group(0).replace(" ", "") if match else ""


def _action_for(dtype: str, text: str) -> str:
    haystack = text or ""
    for keywords, action in ACTION_RULES:
        if any(keyword in haystack for keyword in keywords):
            return action
    if dtype == "合同协议":
        return "审阅并签订"
    if dtype == "验收资料":
        return "组织验收"
    if dtype == "通知函件":
        return "按通知要求办理"
    return "处理"


def _priority(due: date | None, amount: float | None, text: str, dtype: str, base: date) -> int:
    score = 1
    if due is not None:
        days = (due - base).days
        if days <= 1:
            score = 3
        elif days <= 3:
            score = 3
        elif days <= 7:
            score = 2
    elif dtype in ("合同协议", "验收资料", "结算付款", "发票票据"):
        score = 2
    if amount is not None:
        if amount >= 1_000_000:
            score += 1
        elif amount >= 100_000:
            score += 1
    if any(word in (text or "") for word in URGENT_WORDS):
        score += 1
    return max(1, min(3, score))


def extract_drafts(
    doc: dict, base: date | None = None, min_confidence: float = 0.35
) -> list[TaskDraft]:
    """doc: {id,title,ext,folder,excerpt}；最多产出一张任务卡，避免噪声。"""
    base = base or date.today()
    title = doc.get("title", "") or ""
    text = doc.get("excerpt", "") or ""
    dtype, type_score = detect_doc_type(title, text)
    due, due_evidence = find_due(text, base)
    amount, amount_evidence = find_amount(text)
    parties = find_parties(text)
    doc_no = find_doc_no(text)

    confidence = 0.35
    if due:
        confidence += 0.25
    if dtype != "文档":
        confidence += 0.20
    if amount:
        confidence += 0.10
    if doc_no:
        confidence += 0.05
    confidence = round(min(0.95, confidence), 2)

    # 没有期限、也不是明确公文类型的普通文件，不生成任务卡
    if due is None and (dtype == "文档" or type_score < 2):
        return []
    if confidence < min_confidence:
        return []

    action = _action_for(dtype, text)
    subject = clean_filename(title)[:48] or "未命名文件"
    task_title = f"{action}：{subject}"
    priority = _priority(due, amount, text, dtype, base)

    note_parts = []
    if due_evidence:
        note_parts.append(f"期限依据：{due_evidence}")
    if amount_evidence:
        note_parts.append(f"金额：{amount_evidence}")
    if doc_no:
        note_parts.append(f"文号：{doc_no}")
    if parties:
        note_parts.append("；".join(parties))
    if doc.get("folder"):
        note_parts.append(f"目录：{doc['folder']}")
    note = "；".join(note_parts)[:500]

    dedupe = hashlib.sha1(
        f"{doc.get('id')}|{due or ''}|{dtype}|{task_title}".encode("utf-8")
    ).hexdigest()[:20]

    return [
        TaskDraft(
            title=task_title,
            dtype=dtype,
            due=due.isoformat() if due else None,
            priority=priority,
            action=action,
            confidence=confidence,
            note=note,
            dedupe=dedupe,
        )
    ]


def reminder_texts(tasks: list[dict], base: date | None = None) -> list[str]:
    """给 UI 用的到期提醒文案（D-7/3/1/0 与逾期）。"""
    base = base or date.today()
    messages = []
    for task in tasks:
        if task.get("status") not in ("todo", "doing") or not task.get("due"):
            continue
        try:
            due = date.fromisoformat(task["due"])
        except ValueError:
            continue
        days = (due - base).days
        if days < 0:
            messages.append(f"已逾期 {-days} 天：{task['title']}")
        elif days == 0:
            messages.append(f"今天到期：{task['title']}")
        elif days in (1, 3, 7):
            messages.append(f"还有 {days} 天：{task['title']}")
    return messages


def export_ics(tasks: list[dict], path: str) -> int:
    """导出为 .ics，可直接用 Outlook / 手机日历打开。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SmartFileOrganizer//Task Center//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    count = 0
    for task in tasks:
        if not task.get("due"):
            continue
        try:
            day = date.fromisoformat(task["due"]).strftime("%Y%m%d")
        except ValueError:
            continue
        summary = _ics_escape(task.get("title", "任务"))
        description = _ics_escape(task.get("note", ""))
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:sfo-task-{task.get('id')}@local",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{day}",
                f"DTEND;VALUE=DATE:{day}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:{description}",
                "CATEGORIES:SmartFileOrganizer",
                "END:VEVENT",
            ]
        )
        count += 1
    lines.append("END:VCALENDAR")
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return count


def _ics_escape(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )
