"""配置加载 / 保存 / 校验。

设计要点（借鉴 paperless-ngx 的存储路径与 organize 的规则配置）：
- 配置与数据库放在 LOCALAPPDATA，不放在被同步的 Documents 里；
- 所有路径可配置，不写死任何一台机器的盘符；
- 写盘使用临时文件 + os.replace，避免半截 JSON。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "SmartFileOrganizer"
MODES = ("observe", "suggest", "auto")
MODE_LABELS = {
    "observe": "仅观察（只给建议，不动文件）",
    "suggest": "先确认（进待确认队列，确认后才移动）",
    "auto": "自动归档（高置信度直接移动，可撤销）",
}

DEFAULT_IGNORE_SUFFIXES = [
    ".crdownload", ".part", ".opdownload", ".!ut", ".download",
    ".tmp", ".temp", ".lock", ".swp", ".sfo_tmp",
]
DEFAULT_IGNORE_NAMES = ["desktop.ini", "thumbs.db", ".ds_store"]
DEFAULT_INTERNAL_DIRS = ["重复文件", "未分类", "_隔离", "_重复", ".sfo"]


def default_data_dir() -> Path:
    env = os.environ.get("SFO_DATA_DIR")
    if env:
        return Path(env).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_NAME
    return Path.home() / ".smartfileorganizer"


@dataclass
class Config:
    data_dir: str = ""
    watch_dirs: list[str] = field(default_factory=list)
    library_root: str = ""
    mode: str = "suggest"

    # 判定阈值（TF-IDF 余弦，0~1）
    auto_threshold: float = 0.55
    suggest_threshold: float = 0.30
    margin: float = 0.05
    target_precision: float = 0.95

    # 内容抽取
    include_content: bool = True
    max_content_chars: int = 1500
    extract_max_file_mb: int = 50

    # 模型
    model_backend: str = "tfidf"          # tfidf | st
    max_samples_per_class: int = 200
    min_train_samples: int = 2
    model_refresh_seconds: float = 10.0   # 增量学习的最小重建间隔（防止每移一个文件就重训）

    # 监听
    poll_interval: float = 2.0
    min_file_age: float = 1.0
    process_existing: bool = True

    # 安全 / 去重
    duplicate_action: str = "quarantine"  # quarantine | ignore
    quarantine_dir: str = ""
    max_dup_mb: int = 2048
    keep_undo_days: int = 30

    # 扫描
    max_scan_files: int = 20000

    # 任务
    task_reminder_days: list[int] = field(default_factory=lambda: [7, 3, 1, 0])
    task_min_confidence: float = 0.35

    ignore_suffixes: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_SUFFIXES))
    ignore_names: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_NAMES))
    internal_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_INTERNAL_DIRS))

    # ---------- 派生路径 ----------
    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "sfo.db"

    @property
    def config_path(self) -> Path:
        return Path(self.data_dir) / "config.json"

    @property
    def log_dir(self) -> Path:
        return Path(self.data_dir) / "logs"

    @property
    def report_dir(self) -> Path:
        return Path(self.data_dir) / "reports"

    @property
    def default_quarantine(self) -> Path:
        if self.quarantine_dir:
            return Path(self.quarantine_dir)
        if self.library_root:
            return Path(self.library_root) / "重复文件"
        return Path(self.data_dir) / "quarantine"

    # ---------- 持久化 ----------
    def ensure_dirs(self) -> None:
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.ensure_dirs()
        data = asdict(self)
        # 不用 tempfile.mkstemp：它在 Windows 上会创建 0600 权限的文件，
        # 某些受限环境/漫游配置下会导致后续读取失败。
        tmp = self.config_path.with_name(self.config_path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.config_path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    @classmethod
    def load(cls, data_dir: str | Path | None = None) -> "Config":
        base = Path(data_dir) if data_dir else default_data_dir()
        cfg = cls(data_dir=str(base))
        path = base / "config.json"
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                raw = {}
            known = {f.name for f in fields(cls)}
            for key, value in raw.items():
                if key in known and key != "data_dir":
                    setattr(cfg, key, value)
        cfg.data_dir = str(base)
        cfg.normalize()
        cfg.ensure_dirs()
        return cfg

    def normalize(self) -> None:
        """容错：类型不对、越界、枚举非法时回退到安全默认值。"""
        self.watch_dirs = _clean_paths(self.watch_dirs)
        self.library_root = str(self.library_root or "").strip()
        self.quarantine_dir = str(self.quarantine_dir or "").strip()
        if self.mode not in MODES:
            self.mode = "suggest"
        self.auto_threshold = _clamp(self.auto_threshold, 0.05, 0.99)
        self.suggest_threshold = _clamp(self.suggest_threshold, 0.01, self.auto_threshold)
        self.margin = _clamp(self.margin, 0.0, 0.5)
        self.target_precision = _clamp(self.target_precision, 0.5, 1.0)
        self.max_content_chars = int(_clamp(self.max_content_chars, 200, 20000))
        self.extract_max_file_mb = int(_clamp(self.extract_max_file_mb, 1, 4096))
        self.poll_interval = _clamp(self.poll_interval, 0.5, 60)
        self.min_file_age = _clamp(self.min_file_age, 0, 600)
        self.max_samples_per_class = int(_clamp(self.max_samples_per_class, 5, 20000))
        self.min_train_samples = int(_clamp(self.min_train_samples, 2, 100))
        self.model_refresh_seconds = _clamp(self.model_refresh_seconds, 0, 3600)
        self.max_scan_files = int(_clamp(self.max_scan_files, 10, 500000))
        self.max_dup_mb = int(_clamp(self.max_dup_mb, 1, 1024 * 1024))
        if self.duplicate_action not in ("quarantine", "ignore"):
            self.duplicate_action = "quarantine"
        if self.model_backend not in ("tfidf", "st"):
            self.model_backend = "tfidf"
        self.ignore_suffixes = _clean_suffixes(self.ignore_suffixes)
        self.ignore_names = _clean_paths(self.ignore_names, lower=True)
        self.internal_dirs = _clean_paths(self.internal_dirs)
        try:
            self.task_reminder_days = sorted({int(x) for x in self.task_reminder_days}, reverse=True)
        except (TypeError, ValueError):
            self.task_reminder_days = [7, 3, 1, 0]

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.library_root:
            errors.append("资料库根目录未设置")
        elif not os.path.isdir(self.library_root):
            errors.append(f"资料库不存在：{self.library_root}")
        if not self.watch_dirs:
            errors.append("至少需要一个监听目录")
        for d in self.watch_dirs:
            if not os.path.isdir(d):
                errors.append(f"监听目录不存在：{d}")
        if self.library_root and self.watch_dirs and os.path.isdir(self.library_root):
            lib = _real(self.library_root)
            for d in self.watch_dirs:
                if not os.path.isdir(d):
                    continue
                w = _real(d)
                if _inside(lib, w) or _inside(w, lib):
                    errors.append(f"监听目录与资料库不能互相包含：{d}")
        if self.data_dir and self.library_root and os.path.isdir(self.library_root):
            if _inside(_real(self.data_dir), _real(self.library_root)):
                errors.append("数据目录不能放在资料库内部")
        if self.data_dir:
            for d in self.watch_dirs:
                if os.path.isdir(d) and _inside(_real(d), _real(self.data_dir)):
                    errors.append(f"监听目录不能放在数据目录内部：{d}")
        return errors


def _clamp(value, low, high):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, v))


def _clean_paths(items, lower: bool = False) -> list[str]:
    out: list[str] = []
    for item in items or []:
        s = str(item).strip()
        if not s:
            continue
        if lower:
            s = s.lower()
        if s not in out:
            out.append(s)
    return out


def _clean_suffixes(items) -> list[str]:
    out: list[str] = []
    for item in items or []:
        s = str(item).strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        if s not in out:
            out.append(s)
    return out


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _inside(child: str, parent: str) -> bool:
    child = os.path.normcase(os.path.realpath(child))
    parent = os.path.normcase(os.path.realpath(parent))
    if child == parent:
        return True
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False
