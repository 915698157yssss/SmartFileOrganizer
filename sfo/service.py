"""业务编排：监听 → 抽取 → 判定 → 移动/待确认 → 任务卡。

所有耗时操作（抽取、建索引、移动、扫描）都在单个后台工作线程里串行执行，
UI 只通过事件队列接收结果，避免 v6 那种 Tk 主线程被编码/写盘卡死的问题。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import queue
import threading
import time
import traceback
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .classifier import build_classifier, build_feature_text, evaluate as evaluate_samples, model_info
from .config import Config
from .db import Database
from .extract import SUPPORTED_EXTS, extract_text
from .mover import MoveError, is_within, quarantine, safe_move, undo_last as mover_undo, validate_rel
from .policy import decide
from .tasks import export_ics as write_ics, extract_drafts
from .textutil import human_size, is_hidden, looks_like_internal, now_iso, sha256_file
from .watcher import PollingWatcher

APP_LOG = "sfo"


class Service:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.db = Database(cfg.db_path)
        self.events: queue.Queue = queue.Queue()
        self.jobs: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._closed = False
        self.classifier = None
        self.model_dirty = True
        self.model_info: dict = {}
        self._last_build = 0.0
        self._last_label_change = 0.0
        self.watcher: Optional[PollingWatcher] = None
        self.stats = {
            "processed": 0,
            "auto": 0,
            "suggest": 0,
            "observe": 0,
            "unknown": 0,
            "duplicates": 0,
            "errors": 0,
        }
        self._setup_logging()
        self._thread = threading.Thread(target=self._loop, name="sfo-worker", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ 基础
    def _setup_logging(self) -> None:
        logger = logging.getLogger(APP_LOG)
        logger.setLevel(logging.INFO)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        try:
            handler = logging.handlers.RotatingFileHandler(
                self.cfg.log_dir / "app.log",
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:
            pass

    def log(self, message: str, level: str = "info") -> None:
        getattr(logging.getLogger(APP_LOG), level, logging.getLogger(APP_LOG).info)(message)
        self.emit("log", msg=message, level=level)

    def emit(self, kind: str, **payload) -> None:
        payload["type"] = kind
        payload["ts"] = time.time()
        self.events.put(payload)

    def submit(self, func, *args, **kwargs) -> None:
        if not self._closed:
            self.jobs.put((func, args, kwargs))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                func, args, kwargs = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if func is None:
                break
            try:
                func(*args, **kwargs)
            except Exception as exc:  # 单个任务失败不能拖垮服务
                self.stats["errors"] += 1
                self.log(f"任务失败：{type(exc).__name__}: {exc}", "error")
                self.log(traceback.format_exc(limit=3), "debug")
                self.emit("error", msg=str(exc))

    def close(self) -> None:
        self.stop_watching()
        self._closed = True
        self._stop.set()
        self.jobs.put((None, (), {}))
        try:
            self._thread.join(timeout=8)
        except RuntimeError:
            pass
        self.db.close()

    # -------------------------------------------------------------- 监听生命周期
    @property
    def is_watching(self) -> bool:
        return bool(self.watcher and self.watcher.is_alive())

    def start_watching(self) -> tuple[bool, list[str]]:
        errors = self.cfg.validate()
        if errors:
            for error in errors:
                self.log(f"无法启动：{error}", "error")
            return False, errors
        self.stop_watching()
        self.watcher = PollingWatcher(
            directories=list(self.cfg.watch_dirs),
            interval=self.cfg.poll_interval,
            min_age=self.cfg.min_file_age,
            ignore_suffixes=self.cfg.ignore_suffixes,
            ignore_names=self.cfg.ignore_names,
            on_ready=lambda path: self.submit(self._process_file, path, self.cfg.mode, "watch"),
            on_log=self.log,
            process_existing=self.cfg.process_existing,
        )
        self.watcher.start()
        self.log(f"监听已启动：{len(self.cfg.watch_dirs)} 个目录，模式 {self.cfg.mode}")
        self.submit(self._rebuild_model, True)
        self.emit("watching", active=True)
        return True, []

    def stop_watching(self) -> None:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
            self.log("监听已停止")
            self.emit("watching", active=False)

    def apply_config(self, cfg: Config) -> tuple[bool, list[str]]:
        was_watching = self.is_watching
        self.stop_watching()
        self.cfg = cfg
        cfg.save()
        if was_watching:
            return self.start_watching()
        return True, []

    # ------------------------------------------------------------------ 归档主流程
    def _skip_reason(self, path: str) -> str:
        name = os.path.basename(path)
        if is_hidden(path):
            return "隐藏/临时文件"
        lowered = name.lower()
        if lowered in {n.lower() for n in self.cfg.ignore_names}:
            return "系统文件"
        if any(lowered.endswith(suffix) for suffix in self.cfg.ignore_suffixes):
            return "下载临时文件"
        try:
            parts = Path(path).parts
        except (TypeError, ValueError):
            parts = ()
        if any(looks_like_internal(part, self.cfg.internal_dirs) for part in parts):
            return "程序内部目录"
        for managed in (self.cfg.library_root, self.cfg.data_dir):
            if managed and os.path.isdir(managed) and is_within(managed, path):
                return "位于资料库/数据目录内"
        return ""

    def _target_dir(self, rel: str) -> str:
        root = self.cfg.library_root
        if not root or not os.path.isdir(root):
            raise MoveError("资料库根目录不存在")
        if rel in ("", ".", "根目录"):
            return root
        return validate_rel(root, rel)

    def _quarantine_root(self) -> str:
        root = str(self.cfg.default_quarantine)
        for watch in self.cfg.watch_dirs:
            if os.path.isdir(watch) and is_within(watch, root):
                return str(Path(self.cfg.data_dir) / "quarantine")
        return root

    def _process_file(self, path: str, mode: Optional[str] = None, origin: str = "watch") -> dict:
        mode = mode or self.cfg.mode
        name = os.path.basename(path)
        result = {"file": path, "name": name, "action": "skip", "target": None, "score": 0.0}

        reason = self._skip_reason(path)
        if reason:
            self.log(f"跳过 {name}：{reason}")
            return result
        try:
            stat = os.stat(path)
        except OSError as exc:
            self.log(f"无法读取 {name}：{exc}", "error")
            return result
        if not os.path.isfile(path):
            return result
        if stat.st_size <= 0:
            self.log(f"跳过空文件：{name}")
            return result

        self.stats["processed"] += 1
        self.log(f"处理文件：{name}（{human_size(stat.st_size)}）")

        # 1) 去重（先比大小，再算哈希；大于阈值的文件跳过哈希）
        digest = None
        if stat.st_size <= self.cfg.max_dup_mb * 1024 * 1024:
            try:
                digest = sha256_file(path)
            except OSError as exc:
                self.log(f"哈希计算失败：{exc}", "warning")
        if digest:
            duplicate = self.db.find_doc_by_sha(digest, exclude_path=path)
            if duplicate and duplicate["path"] and os.path.exists(duplicate["path"]):
                if self.cfg.duplicate_action == "quarantine":
                    try:
                        move = quarantine(path, self.db, self._quarantine_root(), on_log=self.log)
                        self.stats["duplicates"] += 1
                        self.log(
                            f"重复文件：{name} 与 {os.path.basename(duplicate['path'])} 相同，已移入隔离区"
                        )
                        self.emit("duplicate", name=name, original=duplicate["path"], dst=move.dst)
                        result.update(action="duplicate", target=move.dst)
                    except Exception as exc:
                        self.log(f"隔离重复文件失败：{exc}", "error")
                        result.update(action="error")
                    return result
                self.log(f"重复文件（未移动）：{name}")
                result.update(action="duplicate")
                return result

        # 2) 正文抽取
        ext = os.path.splitext(name)[1].lower()
        text, text_source = "", "none"
        if self.cfg.include_content:
            extracted = extract_text(
                path,
                max_chars=self.cfg.max_content_chars,
                max_file_mb=self.cfg.extract_max_file_mb,
            )
            text = extracted.text
            text_source = extracted.source
            if extracted.warning:
                self.log(f"抽取提示（{name}）：{extracted.warning}", "warning")
        excerpt = text[: self.cfg.max_content_chars]

        # 3) 判定
        classifier = self._ensure_model()
        predictions = []
        if classifier is not None:
            feature = build_feature_text(name, ext=ext, excerpt=excerpt)
            predictions = classifier.predict(feature, top_k=5)
        decision = decide(mode, predictions, self.cfg)
        result.update(
            action=decision.action,
            target=decision.target,
            score=round(decision.score, 4),
            margin=round(decision.margin, 4),
            reason=decision.reason,
        )

        # 4) 先落 doc（拿到 doc_id），再决定是否移动
        doc_id, _new = self.db.upsert_doc(
            path=path,
            title=name,
            ext=ext,
            folder="",
            size=stat.st_size,
            mtime=stat.st_mtime,
            sha256=digest,
            excerpt=excerpt,
            text_source=text_source,
        )

        if decision.action == "auto":
            target = decision.target or ""
            try:
                destination = self._target_dir(target)
                move = safe_move(
                    path,
                    destination,
                    self.db,
                    payload={"doc_id": doc_id, "target": target, "source": "auto"},
                    on_log=self.log,
                )
            except (MoveError, OSError) as exc:
                self.stats["errors"] += 1
                self.log(f"自动归档失败：{name}：{exc}", "error")
                self.db.pending_add(
                    doc_id, path, name, target, decision.score,
                    decision.alternatives, f"自动移动失败：{exc}", mode, "waiting",
                )
                self.emit("error", msg=f"{name} 自动归档失败：{exc}")
                result.update(action="error")
                return result

            self.db.set_doc_path(doc_id, move.dst, target)
            self.db.add_label(doc_id, target, "auto", 1.0)
            self._mark_dirty()
            self.stats["auto"] += 1
            self._maybe_create_task(doc_id, name, ext, excerpt, target)
            self.log(f"自动归档：{name} → {target or '（资料库根）'}（{decision.score:.2f}）")
            self.emit(
                "classified",
                name=name,
                target=target,
                dst=move.dst,
                score=round(decision.score, 3),
                mode=mode,
            )
            result.update(action="auto", dst=move.dst)
            self.emit("stats", **self.stats)
            return result

        status = "observed" if decision.action == "observe" else "waiting"
        self.db.pending_add(
            doc_id,
            path,
            name,
            decision.target,
            decision.score,
            decision.alternatives,
            decision.reason,
            mode,
            status,
        )
        if decision.action == "observe":
            self.stats["observe"] += 1
        elif decision.action == "suggest":
            self.stats["suggest"] += 1
        else:
            self.stats["unknown"] += 1
        if decision.action in ("suggest", "observe"):
            self._maybe_create_task(doc_id, name, ext, excerpt, decision.target or "")
        self.log(
            f"待处理：{name} → {decision.target or '无建议'}（{decision.score:.2f}，{decision.action}）"
        )
        self.emit("pending", name=name, target=decision.target, score=round(decision.score, 3))
        self.emit("stats", **self.stats)
        return result

    def classify_now(self, path: str, mode: Optional[str] = None) -> dict:
        """CLI/测试用同步接口；GUI 请用 submit。"""
        return self._process_file(path, mode, "cli")

    def _mark_dirty(self) -> None:
        self.model_dirty = True
        self._last_label_change = time.time()

    # ------------------------------------------------------------------ 待确认
    def resolve_pending(self, pending_id: int, target: str) -> None:
        self.submit(self._resolve_pending, pending_id, target)

    def _resolve_pending(self, pending_id: int, target: str) -> None:
        row = self.db.pending_get(pending_id)
        if row is None:
            return
        path = row["path"]
        if not os.path.exists(path):
            self.db.pending_resolve(pending_id, "missing")
            self.log(f"文件已不在原位置，无法移动：{path}", "warning")
            self.emit("pending_resolved", id=pending_id, ok=False)
            return
        doc_id = row["doc_id"]
        if not doc_id:
            stat = os.stat(path)
            doc_id, _ = self.db.upsert_doc(
                path, os.path.basename(path), os.path.splitext(path)[1].lower(), "",
                stat.st_size, stat.st_mtime, None, "", "none",
            )
        source = "accept" if target == (row["suggestion"] or "") else "correct"
        try:
            destination = self._target_dir(target)
            move = safe_move(
                path,
                destination,
                self.db,
                payload={"doc_id": doc_id, "target": target, "source": source},
                on_log=self.log,
            )
        except (MoveError, OSError) as exc:
            self.log(f"确认归档失败：{exc}", "error")
            self.emit("error", msg=str(exc))
            return
        self.db.set_doc_path(doc_id, move.dst, target)
        weight = 1.0 if source == "accept" else 1.3
        self.db.add_label(doc_id, target, source, weight)
        self.db.pending_resolve(pending_id, "resolved")
        self._mark_dirty()
        doc = self.db.get_doc(doc_id)
        if doc:
            self._maybe_create_task(doc["id"], doc["title"], doc["ext"], doc["excerpt"], target)
        self.log(f"已确认：{row['filename']} → {target or '（资料库根）'}")
        self.emit("pending_resolved", id=pending_id, ok=True, target=target)
        self.emit("stats", **self.stats)

    def dismiss_pending(self, pending_id: int) -> None:
        self.submit(self._dismiss_pending, pending_id)

    def _dismiss_pending(self, pending_id: int) -> None:
        row = self.db.pending_get(pending_id)
        if row:
            self.db.pending_resolve(pending_id, "dismissed")
            self.log(f"已忽略：{row['filename']}")
            self.emit("pending_resolved", id=pending_id, ok=False)

    # ------------------------------------------------------------------ 撤销
    def undo_last(self) -> None:
        self.submit(self._undo_last)

    def undo_now(self) -> tuple[bool, str]:
        return self._undo_last()

    def _undo_last(self) -> tuple[bool, str]:
        ok, message, payload = mover_undo(self.db, self.log)
        if not ok:
            self.log(message, "warning")
            self.emit("undo", ok=False, msg=message)
            return False, message
        doc_id = payload.get("doc_id")
        if doc_id:
            target = payload.get("target", "")
            source = payload.get("source", "auto")
            self.db.delete_label_for(int(doc_id), target, source)
            restored = payload.get("restored")
            if restored:
                self.db.set_doc_path(int(doc_id), restored, "")
        self._mark_dirty()
        self.log(message)
        self.emit("undo", ok=True, msg=message, payload=payload)
        return True, message

    # ------------------------------------------------------------------ 模型
    def _ensure_model(self):
        if self.classifier is not None and not self.model_dirty:
            return self.classifier
        if self.classifier is not None and self.model_dirty:
            # 增量学习做节流：10 秒内的多次纠正只触发一次重建
            if time.time() - self._last_build < self.cfg.model_refresh_seconds:
                return self.classifier
        return self._rebuild_model(force=self.classifier is None)

    def _rebuild_model(self, force: bool = False):
        if not force and not self.model_dirty:
            return self.classifier
        rows = self.db.training_rows()
        if len(rows) < self.cfg.min_train_samples:
            self.classifier = None
            self.model_dirty = False
            self.model_info = {"backend": "", "docs": 0, "targets": 0, "vocab": 0}
            self.emit("model", info=self.model_info)
            return None

        selected = []
        per_target: Counter = Counter()
        for row in rows:  # 已按 label id 倒序，保留每类最新样本
            target = row["target"]
            if per_target[target] >= self.cfg.max_samples_per_class:
                continue
            per_target[target] += 1
            selected.append(row)

        started = time.time()
        backend = self.cfg.model_backend
        classifier = build_classifier(backend)
        try:
            classifier.fit(selected)
        except Exception as exc:
            if backend != "tfidf":
                self.log(f"{backend} 后端不可用（{exc}），回退 TF-IDF", "warning")
                classifier = build_classifier("tfidf")
                classifier.fit(selected)
            else:
                self.classifier = None
                self.model_dirty = False
                self.log(f"训练失败：{exc}", "error")
                return None

        self.classifier = classifier
        self.model_dirty = False
        self._last_build = time.time()
        info = model_info(classifier)
        info["seconds"] = round(time.time() - started, 2)
        self.model_info = info
        self.log(
            f"模型就绪：{info['docs']} 条样本 / {info['targets']} 个目录 / "
            f"{info['vocab']} 词元（{info['seconds']}s）"
        )
        self.emit("model", info=info)
        return classifier

    def rebuild_model_now(self) -> dict:
        self._rebuild_model(force=True)
        return self.model_info

    # ------------------------------------------------------------------ 训练与评测
    def scan_library(self, sync: bool = False) -> dict:
        if sync:
            return self._scan_library()
        self.submit(self._scan_library)
        return {}

    def _scan_library(self) -> dict:
        root = self.cfg.library_root
        if not root or not os.path.isdir(root):
            message = f"资料库不存在：{root}"
            self.log(message, "error")
            self.emit("scan_done", ok=False, error=message)
            return {"ok": False, "error": message}

        started = time.time()
        scanned = 0
        added = 0
        skipped = 0
        self.log(f"开始扫描资料库：{root}")
        self.emit("scan_start")
        aborted = False
        for dirpath, dirnames, filenames in os.walk(root):
            if self._stop.is_set():
                aborted = True
                break
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and not looks_like_internal(d, self.cfg.internal_dirs)
            ]
            for filename in filenames:
                if self._stop.is_set():
                    aborted = True
                    break
                if scanned >= self.cfg.max_scan_files:
                    break
                if filename.startswith(".") or filename.startswith("~$"):
                    continue
                if filename.lower() in {n.lower() for n in self.cfg.ignore_names}:
                    continue
                full = os.path.join(dirpath, filename)
                if os.path.splitext(filename)[1].lower() in self.cfg.ignore_suffixes:
                    continue
                try:
                    stat = os.stat(full)
                except OSError:
                    skipped += 1
                    continue
                if stat.st_size <= 0:
                    skipped += 1
                    continue
                relative = os.path.relpath(dirpath, root)
                folder = "" if relative in (".", "") else relative
                ext = os.path.splitext(filename)[1].lower()

                existing = self.db.doc_by_path(full)
                if (
                    existing
                    and int(existing["size"] or 0) == stat.st_size
                    and abs(float(existing["mtime"] or 0) - stat.st_mtime) < 1
                    and existing["excerpt"]
                ):
                    excerpt = existing["excerpt"]
                    source = existing["text_source"]
                else:
                    excerpt, source = "", "none"
                    if self.cfg.include_content:
                        extracted = extract_text(
                            full,
                            max_chars=self.cfg.max_content_chars,
                            max_file_mb=self.cfg.extract_max_file_mb,
                        )
                        excerpt, source = extracted.text, extracted.source
                doc_id, created = self.db.upsert_doc(
                    full, filename, ext, folder, stat.st_size, stat.st_mtime,
                    None, excerpt, source,
                )
                self.db.add_label(doc_id, folder, "scan", 1.0)
                scanned += 1
                added += 1 if created else 0
                if scanned % 25 == 0:
                    self.emit("scan_progress", scanned=scanned, current=filename)
                    time.sleep(0.001)

        self._mark_dirty()
        self._rebuild_model(force=True)
        elapsed = round(time.time() - started, 2)
        if aborted:
            self.log(f"扫描已中止：已扫描 {scanned} 个文件", "warning")
            self.emit("scan_done", ok=False, error="扫描已中止")
            return {"ok": False, "error": "扫描已中止", "scanned": scanned}
        self.log(f"扫描完成：{scanned} 个文件，新增 {added}，跳过 {skipped}，用时 {elapsed}s")
        self.emit("scan_done", ok=True, scanned=scanned, added=added, skipped=skipped, seconds=elapsed)
        return {"ok": True, "scanned": scanned, "added": added, "skipped": skipped, "seconds": elapsed}

    def run_eval(self, sync: bool = False) -> dict:
        if sync:
            return self._run_eval()
        self.submit(self._run_eval)
        return {}

    def _run_eval(self) -> dict:
        rows = self.db.training_rows()
        self.log(f"开始评测：{len(rows)} 条样本")
        result = evaluate_samples(
            rows,
            backend=self.cfg.model_backend,
            target_precision=self.cfg.target_precision,
            margin_threshold=self.cfg.margin,
        )
        if not result.get("ok"):
            self.log(f"评测失败：{result.get('error')}", "warning")
            self.emit("eval_done", result=result)
            return result

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_dir = Path(self.cfg.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"eval_{stamp}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "SmartFileOrganizer 评测报告",
            f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
            f"训练/测试：{result['train']} / {result['test']}",
            f"目录数：{result['classes']}",
            f"Top-1：{result['top1']:.1%}",
            f"Top-3：{result['top3']:.1%}",
            f"建议自动阈值：{result['recommended'].get('threshold')}",
            f"该阈值覆盖率：{result['recommended'].get('coverage')}",
            f"该阈值精度：{result['recommended'].get('precision')}",
            "",
            "混淆对（真实 → 误判）：",
        ]
        for item in result["confusion"]:
            lines.append(f"  {item['truth']} → {item['pred']}：{item['count']}")
        (report_dir / f"eval_{stamp}.txt").write_text("\n".join(lines), encoding="utf-8")
        self.log(f"评测完成：Top-1 {result['top1']:.1%}，Top-3 {result['top3']:.1%}")
        self.emit("eval_done", result=result, report=str(report_dir / f"eval_{stamp}.txt"))
        return result

    def import_v6(self, path: str, sync: bool = False) -> dict:
        if sync:
            return self._import_v6(path)
        self.submit(self._import_v6, path)
        return {}

    def _import_v6(self, path: str) -> dict:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            self.log(f"读取 v6 数据失败：{exc}", "error")
            self.emit("import_done", ok=False, error=str(exc))
            return {"ok": False, "error": str(exc)}
        memory = raw.get("memory") if isinstance(raw, dict) else raw
        if not isinstance(memory, list):
            self.log("v6 数据格式不正确（缺少 memory 列表）", "error")
            self.emit("import_done", ok=False, error="格式不正确")
            return {"ok": False, "error": "格式不正确"}

        imported = 0
        for item in memory:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            target = str(item.get("path", "")).strip()
            if not text or not target:
                continue
            doc_id = self.db.add_virtual_doc(text, target)
            self.db.add_label(doc_id, target, "import", 1.0)
            imported += 1
        self._mark_dirty()
        self._rebuild_model(force=True)
        self.log(f"v6 数据导入完成：{imported} 条")
        self.emit("import_done", ok=True, count=imported)
        return {"ok": True, "count": imported}

    # ------------------------------------------------------------------ 任务
    def _maybe_create_task(
        self, doc_id: int, title: str, ext: str, excerpt: str, folder: str
    ) -> None:
        drafts = extract_drafts(
            {"id": doc_id, "title": title, "ext": ext, "excerpt": excerpt, "folder": folder},
            min_confidence=self.cfg.task_min_confidence,
        )
        for draft in drafts:
            task_id = self.db.task_add(
                doc_id,
                draft.title,
                draft.dtype,
                draft.due,
                draft.priority,
                draft.action,
                draft.confidence,
                draft.note,
                draft.dedupe,
            )
            if task_id:
                self.log(f"生成任务卡：{draft.title}（{draft.due or '无期限'}）")
                self.emit("task", id=task_id, title=draft.title, due=draft.due)

    def set_task_status(self, task_id: int, status: str) -> None:
        self.submit(self._set_task_status, task_id, status)

    def _set_task_status(self, task_id: int, status: str) -> None:
        self.db.task_set_status(task_id, status)
        self.log(f"任务状态：#{task_id} → {status}")
        self.emit("task_changed", id=task_id, status=status)

    def delete_task(self, task_id: int) -> None:
        self.submit(self._delete_task, task_id)

    def _delete_task(self, task_id: int) -> None:
        self.db.task_delete(task_id)
        self.emit("task_changed", id=task_id, deleted=True)

    def export_ics(self, path: str) -> int:
        tasks = self.db.task_rows("open")
        count = write_ics(tasks, path)
        self.log(f"已导出 {count} 个任务到 {path}")
        return count

    # ------------------------------------------------------------------ 查询
    def pending_rows(self) -> list[dict]:
        return self.db.pending_rows()

    def task_rows(self, scope: str = "all") -> list[dict]:
        return self.db.task_rows(scope)

    def journal_rows(self, limit: int = 200) -> list[dict]:
        return self.db.journal_rows(limit)

    def counts(self) -> dict:
        data = self.db.counts()
        data.update({"processed": self.stats["processed"], "watching": self.is_watching})
        return data

    def date_label(self) -> str:
        return date.today().isoformat()
