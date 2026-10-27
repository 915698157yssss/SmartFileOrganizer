"""测试用临时目录。

不用 tempfile：Python 3.12 在 Windows 上以 0o700 创建临时目录，
在受限环境（ACL/沙箱）里会导致后续写入被拒绝。这里用普通 mkdir。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent / ".tmp"


def make_temp_dir(prefix: str = "t") -> Path:
    BASE.mkdir(parents=True, exist_ok=True)
    path = BASE / f"{prefix}_{uuid.uuid4().hex[:10]}"
    path.mkdir()
    return path


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
