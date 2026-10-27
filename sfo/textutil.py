"""文本工具：文件名清洗、分词、哈希、大小格式化。

中文没有空格，这里用「单字 + 二元组」做轻量分词，配合 TF-IDF 在几千份
文档上已经足够稳定，而且零模型下载、零 GPU 依赖。
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import datetime

# 常见虚词，单字层面去掉可以显著降噪（二元组仍保留上下文）
_STOP_CHARS = set(
    "的了和与及或在为是对将把被从到由以之其这那有个我们你他她它们"
    "上中下前后内外时年月日第号份件次项条页面页共"
)

_CJK = r"\u4e00-\u9fff"
_TOKEN_RE = re.compile(rf"[A-Za-z][A-Za-z0-9_+#.\-]{{0,30}}|\d+(?:\.\d+)?|[{_CJK}]+")

_DATE_PATTERNS = [
    re.compile(r"\d{4}\s*[年\-/\.]\s*\d{1,2}\s*[月\-/\.]\s*\d{1,2}\s*日?"),
    re.compile(r"(?<!\d)\d{8}(?!\d)"),
    re.compile(r"(?<!\d)\d{6}(?!\d)"),
]
_VERSION_RE = re.compile(r"(?i)\b(?:v|ver|version|rev)\s*\d+(?:\.\d+)*\b")
_COPY_RE = re.compile(r"(?i)(副本|复件|复印件|扫描件|最终版|终版|定稿|修改版|最新版|copy|final|draft)")
_BRACKET_NOISE_RE = re.compile(r"[\(（\[【]\s*(?:\d{1,3}|副本|copy|1)\s*[\)）\]】]", re.I)
_SEP_RE = re.compile(r"[_\-\s·•~]+")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def collapse_ws(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def clean_filename(name: str) -> str:
    """去掉日期、版本、副本、扫描件编号等噪声，保留语义主体。"""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = unicodedata.normalize("NFKC", stem)
    text = stem
    for pattern in _DATE_PATTERNS:
        text = pattern.sub(" ", text)
    text = _VERSION_RE.sub(" ", text)
    text = _COPY_RE.sub(" ", text)
    text = _BRACKET_NOISE_RE.sub(" ", text)
    text = _SEP_RE.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" .-_")
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", text):
        # 清洗后只剩编号（如「扫描件001」），退回原始文件名更有信息量
        return stem.strip() or text
    return text


def tokenize(text: str) -> list[str]:
    """单字 + 二元组 + 英文/数字词；纯数字只保留 4 位以上（年份、编号）。"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        piece = match.group(0)
        if piece.isdigit():
            if len(piece) >= 4:
                tokens.append(piece)
            continue
        if piece[0].isascii():
            if len(piece) >= 2:
                tokens.append(piece)
            continue
        chars = [c for c in piece if c not in _STOP_CHARS]
        tokens.extend(chars)
        tokens.extend(a + b for a, b in zip(chars, chars[1:]))
    return tokens


def decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "big5"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def looks_like_internal(name: str, internal_dirs: list[str]) -> bool:
    lowered = name.lower()
    return any(lowered == d.lower() for d in internal_dirs)


def is_hidden(path: str) -> bool:
    name = os.path.basename(path)
    return name.startswith(".") or name.startswith("~$")
