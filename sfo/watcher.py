"""目录监听（轮询实现）。

为什么不用 watchdog：
- watchdog 在 Windows 网络盘上会漏事件，且回调里不能做耗时操作；
- 轮询逻辑简单、可测，配合「大小/修改时间连续两次不变」可以稳定避开
  下载中的 .crdownload、复制到一半的大文件；
- 以后需要更低延迟，可以再加一个 watchdog 适配器，接口不变。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

from .textutil import human_size


class PollingWatcher(threading.Thread):
    def __init__(
        self,
        directories: list[str],
        interval: float,
        min_age: float,
        ignore_suffixes: list[str],
        ignore_names: list[str],
        on_ready: Callable[[str], None],
        on_log: Callable[[str], None] | None = None,
        process_existing: bool = True,
    ) -> None:
        super().__init__(name="sfo-watcher", daemon=True)
        self.directories = [d for d in directories if d]
        self.interval = max(0.5, interval)
        self.min_age = max(0.0, min_age)
        self.ignore_suffixes = tuple(s.lower() for s in ignore_suffixes)
        self.ignore_names = {n.lower() for n in ignore_names}
        self.on_ready = on_ready
        self.on_log = on_log or (lambda _msg: None)
        self.process_existing = process_existing
        self._stop = threading.Event()
        # path -> [signature, stable_count, queued]
        self._seen: dict[str, list] = {}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.scan_once()
            except Exception as exc:  # 监听线程不能死
                self.on_log(f"监听异常：{type(exc).__name__}: {exc}")

    def scan_once(self) -> int:
        ready = 0
        alive: set[str] = set()
        for directory in self.directories:
            if not os.path.isdir(directory):
                continue
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                self.on_log(f"无法读取监听目录 {directory}：{exc}")
                continue
            for entry in entries:
                try:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                path = entry.path
                alive.add(path)
                if self._ignored(entry.name):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                signature = (stat.st_size, int(stat.st_mtime))
                record = self._seen.get(path)
                if record is None:
                    self._seen[path] = [signature, 0, False]
                    continue
                if record[0] != signature:
                    record[0] = signature
                    record[1] = 0
                    continue
                if record[2]:
                    continue
                record[1] += 1
                if record[1] < 1:
                    continue
                if stat.st_size <= 0:
                    record[2] = True
                    continue
                age = time.time() - stat.st_mtime
                if age < self.min_age:
                    continue
                if not self._can_rename(path):
                    continue
                record[2] = True
                ready += 1
                self.on_ready(path)
        # 清理已经消失或已处理的记录
        for path in list(self._seen.keys()):
            if path not in alive:
                self._seen.pop(path, None)
        return ready

    def _ignored(self, name: str) -> bool:
        lowered = name.lower()
        if lowered in self.ignore_names:
            return True
        if name.startswith(".") or name.startswith("~$"):
            return True
        return any(lowered.endswith(suffix) for suffix in self.ignore_suffixes)

    @staticmethod
    def _can_rename(path: str) -> bool:
        """判断文件是否已经写完、没有被独占。

        同路径改名在 Windows 上可以检测独占；某些网络盘不支持同路径改名时，
        再尝试以读写方式打开。两者都失败才继续等待。
        """
        try:
            os.rename(path, path)
            return True
        except OSError:
            pass
        try:
            with open(path, "rb+"):
                return True
        except OSError:
            return False


class OneShotScan:
    """CLI/测试用：手动对目录做一次就绪扫描。"""

    @staticmethod
    def list_ready(directory: str, ignore_suffixes: list[str], ignore_names: list[str]) -> list[str]:
        watcher = PollingWatcher([directory], 1.0, 0.0, ignore_suffixes, ignore_names, lambda _p: None)
        result: list[str] = []
        watcher.on_ready = result.append
        for _ in range(2):
            watcher.scan_once()
        return result


def describe(path: str) -> str:
    try:
        return f"{os.path.basename(path)}（{human_size(os.path.getsize(path))}）"
    except OSError:
        return os.path.basename(path)
