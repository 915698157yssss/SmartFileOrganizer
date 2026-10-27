"""移动、校验、撤销。

v6 的 `shutil.move` 在跨盘/网络盘失败时会留下半截文件；这里改成：
临时文件 → 校验 SHA256 → 同卷 os.replace 原子改名 → 记 journal → 再删源。
任何一步失败都不破坏源文件。
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .db import Database
from .textutil import human_size, sha256_file


class MoveError(Exception):
    pass


@dataclass
class MoveResult:
    src: str
    dst: str
    sha256: str
    journal_id: int
    source_left: bool = False


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for index in range(1, 10000):
        candidate = f"{stem}_{index}{ext}"
        if not os.path.exists(candidate):
            return candidate
    raise MoveError(f"无法为 {path} 生成不冲突的文件名")


def is_within(root: str, path: str) -> bool:
    root_real = os.path.normcase(os.path.realpath(root))
    path_real = os.path.normcase(os.path.realpath(path))
    if root_real == path_real:
        return True
    try:
        return os.path.commonpath([root_real, path_real]) == root_real
    except ValueError:
        return False


def validate_rel(root: str, rel: str) -> str:
    """只允许资料库内的相对子目录，拒绝绝对路径、盘符、.. 与符号链接逃逸。"""
    rel = (rel or "").strip().strip("\\/")
    if not rel:
        raise MoveError("目标目录为空")
    if os.path.isabs(rel) or (len(rel) > 1 and rel[1] == ":"):
        raise MoveError(f"不允许绝对路径：{rel}")
    normalized = os.path.normpath(rel)
    if normalized == ".." or normalized.startswith(".." + os.sep):
        raise MoveError(f"不允许越出资料库：{rel}")
    target = os.path.realpath(os.path.join(root, normalized))
    if not is_within(root, target):
        raise MoveError(f"目标不在资料库内：{rel}")
    return target


def safe_move(
    src: str,
    dst_dir: str,
    db: Database,
    op: str = "move",
    payload: Optional[dict] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> MoveResult:
    log = on_log or (lambda _msg: None)
    if not os.path.isfile(src):
        raise MoveError(f"源文件不存在：{src}")
    os.makedirs(dst_dir, exist_ok=True)

    size = os.path.getsize(src)
    digest = sha256_file(src)
    dst = unique_path(os.path.join(dst_dir, os.path.basename(src)))
    tmp = os.path.join(dst_dir, f".sfo_tmp_{os.getpid()}_{time.time_ns()}_{os.path.basename(dst)}")

    try:
        shutil.copy2(src, tmp)
        if os.path.getsize(tmp) != size:
            raise MoveError("复制后大小不一致")
        if sha256_file(tmp) != digest:
            raise MoveError("复制后 SHA256 校验失败")
        os.replace(tmp, dst)
    except Exception as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        if isinstance(exc, MoveError):
            raise
        raise MoveError(f"移动失败：{type(exc).__name__}: {exc}") from exc

    journal_id = db.journal_add(op, src, dst, digest, payload or {})
    log(f"已移动 {os.path.basename(src)} → {os.path.basename(dst_dir)}（{human_size(size)}）")

    source_left = False
    try:
        os.remove(src)
    except OSError as exc:
        source_left = True
        log(f"警告：目标已就位，但源文件删除失败（{exc}），请手工清理：{src}")

    return MoveResult(src=src, dst=dst, sha256=digest, journal_id=journal_id, source_left=source_left)


def undo_last(db: Database, on_log: Optional[Callable[[str], None]] = None) -> tuple[bool, str, dict]:
    """撤销最近一次移动；只处理 journal 中 status=done 的记录。"""
    log = on_log or (lambda _msg: None)
    row = db.journal_last_done("move")
    if row is None:
        return False, "没有可撤销的移动记录", {}
    src = row["src"]
    dst = row["dst"]
    if not dst or not os.path.exists(dst):
        return False, f"目标文件已不存在，无法撤销：{dst}", {}
    if row["sha256"] and sha256_file(dst) != row["sha256"]:
        return False, "文件内容已变化（SHA256 不一致），为安全起见不自动撤销", {}

    parent = os.path.dirname(src)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        return False, f"原目录不可写，无法撤销：{exc}", {}

    restore = unique_path(src)
    tmp = restore + f".sfo_undo_{os.getpid()}"
    try:
        shutil.copy2(dst, tmp)
        os.replace(tmp, restore)
        os.remove(dst)
    except Exception as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, f"撤销失败：{type(exc).__name__}: {exc}", {}

    db.journal_set_status(int(row["id"]), "undone")
    payload = {}
    try:
        import json

        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        payload = {}
    payload["restored"] = restore
    payload["journal_id"] = int(row["id"])
    log(f"已撤销：{os.path.basename(dst)} → {restore}")
    return True, f"已恢复到 {restore}", payload


def quarantine(path: str, db: Database, quarantine_root: str, on_log=None) -> MoveResult:
    """重复文件不删除，移入隔离区（按日期分目录），保留人工恢复入口。"""
    day = time.strftime("%Y%m%d")
    dst_dir = os.path.join(quarantine_root, day)
    return safe_move(path, dst_dir, db, op="quarantine", payload={"reason": "duplicate"}, on_log=on_log)
