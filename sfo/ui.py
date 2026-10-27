"""Tkinter 界面：待确认 / 任务 / 归档记录 / 训练评测 / 设置 / 日志。

界面只做展示与交互，所有耗时操作都通过 Service 投递给后台线程，
因此拖入大文件、扫描几万份资料时窗口不会假死。
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import __version__
from .config import Config, MODE_LABELS, MODES
from .service import Service
from .tasks import reminder_texts

PRIORITY_MARK = {1: "低", 2: "中", 3: "高"}
STATUS_LABEL = {
    "candidate": "候选",
    "todo": "待办",
    "doing": "进行中",
    "done": "已完成",
    "snoozed": "已搁置",
    "dismissed": "已忽略",
}


class FolderPicker(tk.Toplevel):
    def __init__(self, parent, root_path: str, initial: str = ""):
        super().__init__(parent)
        self.title("选择资料库内的目标目录")
        self.geometry("620x520")
        self.transient(parent)
        self.grab_set()
        self.root_path = root_path
        self.result: str | None = None
        self.paths: dict[str, str] = {}

        self.tree = ttk.Treeview(self, show="tree")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.tree.bind("<<TreeviewOpen>>", self._on_open)

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(buttons, text="选择此目录", command=self._ok).pack(side=tk.LEFT)
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.LEFT, padx=6)
        ttk.Label(buttons, text="（根节点＝资料库根目录）", foreground="gray").pack(side=tk.RIGHT)

        root_id = self.tree.insert("", "end", text="资料库（根）", open=True)
        self.paths[root_id] = root_path
        self._fill(root_id, root_path)
        self.tree.selection_set(root_id)
        self.tree.focus(root_id)

        start = initial
        if start:
            full = os.path.normpath(os.path.join(root_path, start))
            if os.path.isdir(full):
                node = root_id
                current = root_path
                for part in ([] if start in ("", ".") else start.split(os.sep)):
                    current = os.path.join(current, part)
                    self._fill(node, self.paths[node])
                    child = self._find_child(node, current)
                    if child is None:
                        break
                    self.tree.item(node, open=True)
                    node = child
                self.tree.selection_set(node)
                self.tree.see(node)

    def _fill(self, node: str, path: str) -> None:
        children = self.tree.get_children(node)
        if children:
            if not (len(children) == 1 and self.tree.item(children[0], "text") == "…"):
                return
            self.tree.delete(*children)
        try:
            entries = sorted(
                (e for e in os.scandir(path) if e.is_dir(follow_symlinks=False)),
                key=lambda e: e.name.lower(),
            )
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name.startswith("~$"):
                continue
            child = self.tree.insert(node, "end", text=entry.name)
            self.paths[child] = entry.path
            self.tree.insert(child, "end", text="…")

    def _find_child(self, node: str, path: str) -> str | None:
        for child in self.tree.get_children(node):
            if os.path.normcase(self.paths.get(child, "")) == os.path.normcase(path):
                return child
        return None

    def _on_open(self, _event=None) -> None:
        node = self.tree.focus()
        if not node:
            return
        path = self.paths.get(node)
        if not path:
            return
        for child in self.tree.get_children(node):
            if self.tree.item(child, "text") == "…":
                self.tree.delete(child)
        self._fill(node, path)

    def _ok(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请选择一个目录", parent=self)
            return
        path = self.paths.get(selection[0], self.root_path)
        relative = os.path.relpath(path, self.root_path)
        self.result = "" if relative in (".", "") else relative
        self.destroy()


class App(tk.Tk):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.service = Service(cfg)
        self.title(f"SmartFileOrganizer v{__version__} —— 智能归档与任务助手")
        self.geometry("1180x820")
        self.minsize(980, 680)
        self._dirty = {"pending", "tasks", "records", "header", "model"}
        self._pending: dict[int, dict] = {}
        self._tasks: dict[int, dict] = {}
        self._records: dict[int, dict] = {}
        self._last_eval: dict = {}
        self._last_scan = ""
        self._last_reminders: set[str] = set()

        self._build_menu()
        self._build_header()
        self._build_tabs()
        self._build_statusbar()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self._tick)
        self.after(1500, self._remind)
        self._refresh_header()

        if not cfg.library_root or not cfg.watch_dirs:
            self.notebook.select(self.tab_settings)
            self.after(400, lambda: messagebox.showinfo(
                "首次使用",
                "请先在「设置」中选择资料库根目录和至少一个监听目录，然后保存并启动监听。",
                parent=self,
            ))

    # ------------------------------------------------------------------ 布局
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开数据目录", command=lambda: self._open_path(self.cfg.data_dir))
        file_menu.add_command(label="打开日志文件", command=lambda: self._open_path(str(self.cfg.log_dir / "app.log")))
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="关于", command=self._about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.config(menu=menubar)

    def _build_header(self) -> None:
        frame = ttk.LabelFrame(self, text="运行状态", padding=8)
        frame.pack(fill=tk.X, padx=10, pady=(8, 4))

        ttk.Label(frame, text="资料库：").grid(row=0, column=0, sticky=tk.W)
        self.library_var = tk.StringVar(value=self.cfg.library_root or "（未设置）")
        ttk.Label(frame, textvariable=self.library_var, foreground="#1a4d8f").grid(
            row=0, column=1, columnspan=3, sticky=tk.W, padx=(4, 0)
        )

        ttk.Label(frame, text="模式：").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.mode_var = tk.StringVar()
        self.mode_box = ttk.Combobox(
            frame,
            textvariable=self.mode_var,
            values=[MODE_LABELS[m] for m in MODES],
            state="readonly",
            width=42,
        )
        self.mode_box.grid(row=1, column=1, sticky=tk.W, padx=(4, 10), pady=(6, 0))
        self.mode_box.set(MODE_LABELS.get(self.cfg.mode, MODE_LABELS["suggest"]))
        self.mode_box.bind("<<ComboboxSelected>>", self._on_mode_change)

        self.watch_btn = ttk.Button(frame, text="▶ 启动监听", command=self._toggle_watch)
        self.watch_btn.grid(row=1, column=2, padx=6, pady=(6, 0))

        self.counts_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.counts_var, foreground="#555").grid(
            row=1, column=3, sticky=tk.E, padx=(10, 0), pady=(6, 0)
        )

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        self.tab_pending = ttk.Frame(self.notebook)
        self.tab_tasks = ttk.Frame(self.notebook)
        self.tab_records = ttk.Frame(self.notebook)
        self.tab_training = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)
        self.tab_log = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_pending, text="待确认")
        self.notebook.add(self.tab_tasks, text="任务中心")
        self.notebook.add(self.tab_records, text="归档记录")
        self.notebook.add(self.tab_training, text="训练与评测")
        self.notebook.add(self.tab_settings, text="设置")
        self.notebook.add(self.tab_log, text="日志")

        self._build_pending()
        self._build_tasks()
        self._build_records()
        self._build_training()
        self._build_settings()
        self._build_log()
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._refresh_all())

    def _build_statusbar(self) -> None:
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(
            fill=tk.X, side=tk.BOTTOM
        )

    # ------------------------------------------------------------------ 待确认
    def _build_pending(self) -> None:
        toolbar = ttk.Frame(self.tab_pending)
        toolbar.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(toolbar, text="采纳建议", command=self._adopt_suggestion).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="改到其他目录…", command=self._change_pending).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="忽略", command=self._dismiss_pending).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="打开文件", command=self._open_pending).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="刷新", command=lambda: self._refresh_pending()).pack(side=tk.RIGHT)

        columns = ("time", "file", "mode", "suggestion", "score", "status")
        self.pending_tree = ttk.Treeview(self.tab_pending, columns=columns, show="headings", height=14)
        headings = {
            "time": ("时间", 70),
            "file": ("文件名", 300),
            "mode": ("来源", 70),
            "suggestion": ("建议目录", 280),
            "score": ("相似度", 70),
            "status": ("状态", 70),
        }
        for key, (text, width) in headings.items():
            self.pending_tree.heading(key, text=text)
            self.pending_tree.column(key, width=width, anchor=tk.W)
        self.pending_tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.pending_tree.bind("<Double-1>", lambda _e: self._open_pending())

        ttk.Label(
            self.tab_pending,
            text="说明：「先确认」模式下文件不会被移动；点「采纳建议」或「改到其他目录」后才归档，并作为学习样本。",
            foreground="gray",
        ).pack(anchor=tk.W, padx=8, pady=(0, 6))

    # ------------------------------------------------------------------ 任务
    def _build_tasks(self) -> None:
        toolbar = ttk.Frame(self.tab_tasks)
        toolbar.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(toolbar, text="筛选：").pack(side=tk.LEFT)
        self.task_scope = tk.StringVar(value="open")
        scope_box = ttk.Combobox(
            toolbar,
            textvariable=self.task_scope,
            state="readonly",
            width=10,
            values=["open", "today", "week", "overdue", "candidate", "todo", "doing", "done", "all"],
        )
        scope_box.pack(side=tk.LEFT, padx=(0, 10))
        scope_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_tasks())

        ttk.Button(toolbar, text="转为待办", command=lambda: self._set_task_status("todo")).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="开始", command=lambda: self._set_task_status("doing")).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="完成", command=lambda: self._set_task_status("done")).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="搁置", command=lambda: self._set_task_status("snoozed")).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="删除", command=self._delete_task).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="导出日历(.ics)", command=self._export_ics).pack(side=tk.LEFT, padx=(12, 3))
        ttk.Button(toolbar, text="打开来源文件", command=self._open_task_source).pack(side=tk.LEFT, padx=3)

        columns = ("due", "priority", "type", "title", "status")
        self.task_tree = ttk.Treeview(self.tab_tasks, columns=columns, show="headings", height=14)
        headings = {
            "due": ("截止日期", 100),
            "priority": ("优先级", 60),
            "type": ("类型", 100),
            "title": ("任务", 520),
            "status": ("状态", 80),
        }
        for key, (text, width) in headings.items():
            self.task_tree.heading(key, text=text)
            self.task_tree.column(key, width=width, anchor=tk.W)
        self.task_tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.task_tree.bind("<Double-1>", lambda _e: self._open_task_source())

        ttk.Label(
            self.tab_tasks,
            text="任务卡由正文中的期限/文号/金额/动作自动生成；确认后才进入待办，逾期与临近到期会在启动时提示。",
            foreground="gray",
        ).pack(anchor=tk.W, padx=8, pady=(0, 6))

    # ------------------------------------------------------------------ 记录
    def _build_records(self) -> None:
        toolbar = ttk.Frame(self.tab_records)
        toolbar.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(toolbar, text="撤销最近一次", command=self._undo).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="打开目标文件", command=self._open_record_target).pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="刷新", command=lambda: self._refresh_records()).pack(side=tk.RIGHT)

        columns = ("time", "op", "src", "dst", "status")
        self.record_tree = ttk.Treeview(self.tab_records, columns=columns, show="headings", height=14)
        headings = {
            "time": ("时间", 130),
            "op": ("操作", 70),
            "src": ("源路径", 360),
            "dst": ("目标路径", 360),
            "status": ("状态", 70),
        }
        for key, (text, width) in headings.items():
            self.record_tree.heading(key, text=text)
            self.record_tree.column(key, width=width, anchor=tk.W)
        self.record_tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.record_tree.bind("<Double-1>", lambda _e: self._open_record_target())

    # ------------------------------------------------------------------ 训练
    def _build_training(self) -> None:
        toolbar = ttk.Frame(self.tab_training)
        toolbar.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(toolbar, text="扫描资料库并训练", command=self._scan).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="重建模型", command=self._rebuild).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="留出集评测", command=self._eval).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="应用评测阈值", command=self._apply_eval_thresholds).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="导入 v6 数据", command=self._import_v6).pack(side=tk.LEFT, padx=(16, 4))
        ttk.Button(toolbar, text="打开数据目录", command=lambda: self._open_path(self.cfg.data_dir)).pack(side=tk.RIGHT)

        self.progress = ttk.Progressbar(self.tab_training, mode="indeterminate")
        self.progress.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.progress_label = ttk.Label(self.tab_training, text="")
        self.progress_label.pack(anchor=tk.W, padx=8)

        self.training_text = scrolledtext.ScrolledText(
            self.tab_training, height=22, font=("Consolas", 9)
        )
        self.training_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.training_text.insert(
            "1.0",
            "这里显示模型信息与评测结果。\n"
            "建议流程：\n"
            "  1) 点击「扫描资料库并训练」，程序会读取文件名 + 正文并建立索引；\n"
            "  2) 点击「留出集评测」，查看 Top-1/Top-3 与推荐的自动阈值；\n"
            "  3) 若自动阈值覆盖率过低，先确认目标目录是否过细或命名是否混乱。\n",
        )
        self.training_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ 设置
    def _build_settings(self) -> None:
        frame = ttk.Frame(self.tab_settings, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="资料库根目录：").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.library_entry = ttk.Entry(frame)
        self.library_entry.grid(row=0, column=1, sticky=tk.EW, padx=4, pady=4)
        self.library_entry.insert(0, self.cfg.library_root)
        ttk.Button(frame, text="浏览…", command=self._browse_library).grid(row=0, column=2, pady=4)

        ttk.Label(frame, text="监听目录：").grid(row=1, column=0, sticky=tk.NW, pady=4)
        watch_frame = ttk.Frame(frame)
        watch_frame.grid(row=1, column=1, sticky=tk.EW, padx=4, pady=4)
        self.watch_list = tk.Listbox(watch_frame, height=5)
        self.watch_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for item in self.cfg.watch_dirs:
            self.watch_list.insert(tk.END, item)
        buttons = ttk.Frame(watch_frame)
        buttons.pack(side=tk.LEFT, padx=6, fill=tk.Y)
        ttk.Button(buttons, text="添加…", command=self._add_watch).pack(fill=tk.X)
        ttk.Button(buttons, text="移除", command=self._remove_watch).pack(fill=tk.X, pady=4)

        ttk.Label(frame, text="判定阈值：").grid(row=2, column=0, sticky=tk.W, pady=4)
        threshold_frame = ttk.Frame(frame)
        threshold_frame.grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=4)
        self.auto_var = tk.StringVar(value=str(self.cfg.auto_threshold))
        self.suggest_var = tk.StringVar(value=str(self.cfg.suggest_threshold))
        self.margin_var = tk.StringVar(value=str(self.cfg.margin))
        for offset, (label, var, width) in enumerate(
            (("自动阈值", self.auto_var, 8), ("建议阈值", self.suggest_var, 8), ("间隔", self.margin_var, 8))
        ):
            ttk.Label(threshold_frame, text=label).grid(row=0, column=offset * 2, padx=(0 if offset == 0 else 14, 4))
            ttk.Entry(threshold_frame, textvariable=var, width=width).grid(row=0, column=offset * 2 + 1)
        ttk.Label(
            threshold_frame,
            text="（自动=高分才移动；建议=进入待确认；间隔=与第二名的分差）",
            foreground="gray",
        ).grid(row=1, column=0, columnspan=6, sticky=tk.W, pady=(4, 0))

        self.content_var = tk.BooleanVar(value=self.cfg.include_content)
        ttk.Checkbutton(
            frame, text="读取正文（docx/xlsx/pptx/txt/pdf，扫描件需 OCR，未安装 pypdf 时自动降级）",
            variable=self.content_var,
        ).grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=8)

        ttk.Button(frame, text="保存并应用", command=self._save_settings).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=10
        )

        info = (
            f"数据目录：{self.cfg.data_dir}\n"
            f"数据库：{self.cfg.db_path}\n"
            "配置保存在数据目录的 config.json；删除数据库不会删除资料库里的文件。"
        )
        ttk.Label(frame, text=info, foreground="gray", justify=tk.LEFT).grid(
            row=5, column=0, columnspan=3, sticky=tk.W, pady=(10, 0)
        )

    # ------------------------------------------------------------------ 日志
    def _build_log(self) -> None:
        toolbar = ttk.Frame(self.tab_log)
        toolbar.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(toolbar, text="清空", command=self._clear_log).pack(side=tk.LEFT)
        ttk.Button(
            toolbar, text="打开日志文件", command=lambda: self._open_path(str(self.cfg.log_dir / "app.log"))
        ).pack(side=tk.LEFT, padx=6)
        self.log_text = scrolledtext.ScrolledText(self.tab_log, height=20, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

    # ------------------------------------------------------------------ 事件循环
    def _tick(self) -> None:
        try:
            for _ in range(400):
                event = self.service.events.get_nowait()
                try:
                    self._handle(event)
                except Exception as exc:  # 单条事件失败不能中断刷新循环
                    self._append_log(f"[UI] 事件处理失败：{type(exc).__name__}: {exc}", error=True)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        if self._dirty:
            self._refresh_all()
        if not self.winfo_exists():
            return
        self.after(300, self._tick)

    def _handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "log":
            message = event.get("msg", "")
            stamp = datetime.now().strftime("%H:%M:%S")
            self._append_log(f"[{stamp}] {message}", error=event.get("level") == "error")
        elif kind == "watching":
            self._update_watch_button()
            self._dirty.add("header")
        elif kind == "classified":
            self.status_var.set(
                f"已归档 {event.get('name')} → {event.get('target') or '资料库根'}（{event.get('score')}）"
            )
            self._dirty.update({"pending", "records", "tasks", "header"})
        elif kind == "pending":
            self.status_var.set(f"待确认：{event.get('name')}")
            self._dirty.update({"pending", "header"})
        elif kind == "pending_resolved":
            self._dirty.update({"pending", "records", "tasks", "header"})
        elif kind in ("task", "task_changed"):
            self._dirty.update({"tasks", "header"})
        elif kind == "model":
            self._show_model(event.get("info", {}))
            self._dirty.add("header")
        elif kind == "scan_start":
            self.progress.start(12)
            self.progress_label.config(text="扫描中…")
            self._training_append("\n=== 扫描开始 ===")
        elif kind == "scan_progress":
            self.progress_label.config(
                text=f"已扫描 {event.get('scanned')} 个：{event.get('current', '')}"
            )
        elif kind == "scan_done":
            self.progress.stop()
            self.progress_label.config(text="")
            if event.get("ok"):
                self._training_append(
                    f"扫描完成：{event.get('scanned')} 个文件，新增 {event.get('added')}，"
                    f"跳过 {event.get('skipped')}，用时 {event.get('seconds')}s"
                )
                self._dirty.add("header")
            else:
                self._training_append(f"扫描失败：{event.get('error')}")
        elif kind == "eval_done":
            result = event.get("result") or {}
            self._last_eval = result
            self._show_eval(result)
        elif kind == "import_done":
            if event.get("ok"):
                self._training_append(f"v6 数据导入完成：{event.get('count')} 条")
            else:
                self._training_append(f"v6 数据导入失败：{event.get('error')}")
        elif kind == "undo":
            self.status_var.set(event.get("msg", "已撤销"))
            self._dirty.update({"records", "pending", "tasks", "header"})
        elif kind == "error":
            self.status_var.set(f"错误：{event.get('msg')}")
        elif kind == "duplicate":
            self.status_var.set(f"重复文件已隔离：{event.get('name')}")
            self._dirty.update({"records", "header"})
        elif kind == "stats":
            self._dirty.add("header")

    def _refresh_all(self) -> None:
        if "header" in self._dirty:
            self._refresh_header()
        if "pending" in self._dirty:
            self._refresh_pending()
        if "tasks" in self._dirty:
            self._refresh_tasks()
        if "records" in self._dirty:
            self._refresh_records()
        self._dirty.clear()

    def _refresh_header(self) -> None:
        counts = self.service.counts()
        self.counts_var.set(
            f"待确认 {counts['pending']} ｜ 任务 {counts['tasks_todo']} ｜ "
            f"逾期 {counts['tasks_overdue']} ｜ 样本 {counts['labels']}"
        )
        self.library_var.set(self.cfg.library_root or "（未设置）")
        self._update_watch_button()

    def _update_watch_button(self) -> None:
        if self.service.is_watching:
            self.watch_btn.config(text="■ 停止监听")
            self.status_var.set("监听中")
        else:
            self.watch_btn.config(text="▶ 启动监听")

    def _refresh_pending(self) -> None:
        rows = self.service.pending_rows()
        self._pending = {int(r["id"]): r for r in rows}
        self.pending_tree.delete(*self.pending_tree.get_children())
        for row in rows:
            created = (row.get("created_at") or "")[11:19]
            values = (
                created,
                row.get("filename", ""),
                "观察" if row.get("status") == "observed" else "确认",
                row.get("suggestion") or "（无建议）",
                f"{float(row.get('score') or 0):.2f}",
                "仅观察" if row.get("status") == "observed" else "待确认",
            )
            self.pending_tree.insert("", "end", iid=str(row["id"]), values=values)

    def _refresh_tasks(self) -> None:
        scope = self.task_scope.get() if hasattr(self, "task_scope") else "open"
        rows = self.service.task_rows(scope)
        self._tasks = {int(r["id"]): r for r in rows}
        self.task_tree.delete(*self.task_tree.get_children())
        for row in rows:
            values = (
                row.get("due") or "—",
                PRIORITY_MARK.get(int(row.get("priority") or 1), "低"),
                row.get("dtype") or "",
                row.get("title") or "",
                STATUS_LABEL.get(row.get("status", ""), row.get("status", "")),
            )
            self.task_tree.insert("", "end", iid=str(row["id"]), values=values)
            status = row.get("status", "")
            if row.get("due") and status in ("todo", "doing") and row["due"] < date.today().isoformat():
                self.task_tree.item(str(row["id"]), tags=("overdue",))
        self.task_tree.tag_configure("overdue", foreground="#b00020")

    def _refresh_records(self) -> None:
        rows = self.service.journal_rows(200)
        self._records = {int(r["id"]): r for r in rows}
        self.record_tree.delete(*self.record_tree.get_children())
        for row in rows:
            values = (
                (row.get("created_at") or "").replace("T", " ")[:19],
                row.get("op", ""),
                row.get("src", ""),
                row.get("dst", ""),
                row.get("status", ""),
            )
            self.record_tree.insert("", "end", iid=str(row["id"]), values=values)

    def _show_model(self, info: dict) -> None:
        if not info:
            return
        text = (
            f"\n=== 模型 ===\n"
            f"后端：{info.get('backend', '')}  样本：{info.get('docs', 0)}  "
            f"目录：{info.get('targets', 0)}  词元：{info.get('vocab', 0)}  "
            f"用时：{info.get('seconds', 0)}s\n"
        )
        self._training_append(text, raw=True)

    def _show_eval(self, result: dict) -> None:
        if not result:
            return
        if not result.get("ok"):
            self._training_append(f"\n=== 评测失败 ===\n{result.get('error')}", raw=True)
            return
        recommended = result.get("recommended") or {}
        lines = [
            "\n=== 评测结果 ===",
            f"训练/测试：{result.get('train')} / {result.get('test')}    目录数：{result.get('classes')}",
            f"Top-1：{result.get('top1', 0):.1%}    Top-3：{result.get('top3', 0):.1%}",
            f"推荐自动阈值：{recommended.get('threshold')}    "
            f"覆盖：{recommended.get('coverage')}    精度：{recommended.get('precision')}",
        ]
        confusion = result.get("confusion") or []
        if confusion:
            lines.append("易混淆（真实 → 误判）：")
            for item in confusion[:8]:
                lines.append(f"  {item['truth']} → {item['pred']}：{item['count']}")
        self._training_append("\n".join(lines), raw=True)

    # ------------------------------------------------------------------ 操作
    def _on_mode_change(self, _event=None) -> None:
        index = self.mode_box.current()
        if index < 0:
            return
        self.cfg.mode = MODES[index]
        self.service.cfg.mode = self.cfg.mode
        self.cfg.save()
        self.service.log(f"模式切换为：{MODE_LABELS[self.cfg.mode]}")

    def _toggle_watch(self) -> None:
        if self.service.is_watching:
            self.service.stop_watching()
            self._update_watch_button()
            return
        ok, errors = self.service.start_watching()
        if not ok:
            messagebox.showerror("无法启动", "\n".join(errors), parent=self)
        self._update_watch_button()

    def _selected_pending(self) -> dict | None:
        selection = self.pending_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一条待确认记录", parent=self)
            return None
        return self._pending.get(int(selection[0]))

    def _adopt_suggestion(self) -> None:
        row = self._selected_pending()
        if not row:
            return
        target = row.get("suggestion")
        if not target:
            messagebox.showinfo("提示", "这条记录没有建议目录，请使用「改到其他目录」。", parent=self)
            return
        self.service.resolve_pending(int(row["id"]), target)
        self.status_var.set(f"正在归档 {row['filename']} …")

    def _change_pending(self) -> None:
        row = self._selected_pending()
        if not row:
            return
        if not self.cfg.library_root or not os.path.isdir(self.cfg.library_root):
            messagebox.showerror("错误", "资料库目录不存在，请先在设置中配置。", parent=self)
            return
        picker = FolderPicker(self, self.cfg.library_root, row.get("suggestion") or "")
        self.wait_window(picker)
        if picker.result is not None:
            self.service.resolve_pending(int(row["id"]), picker.result)
            self.status_var.set(f"正在归档 {row['filename']} …")

    def _dismiss_pending(self) -> None:
        row = self._selected_pending()
        if row:
            self.service.dismiss_pending(int(row["id"]))

    def _open_pending(self) -> None:
        row = self._selected_pending()
        if row:
            self._open_path(row.get("path", ""))

    def _selected_task(self) -> dict | None:
        selection = self.task_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一条任务", parent=self)
            return None
        return self._tasks.get(int(selection[0]))

    def _set_task_status(self, status: str) -> None:
        row = self._selected_task()
        if row:
            self.service.set_task_status(int(row["id"]), status)

    def _delete_task(self) -> None:
        row = self._selected_task()
        if row and messagebox.askyesno("确认", f"删除任务？\n{row['title']}", parent=self):
            self.service.delete_task(int(row["id"]))

    def _open_task_source(self) -> None:
        row = self._selected_task()
        if not row:
            return
        source = row.get("doc_path")
        if source and os.path.exists(source):
            self._open_path(source)
        else:
            messagebox.showwarning("提示", "来源文件不存在或已被移动。", parent=self)

    def _export_ics(self) -> None:
        path = filedialog.asksaveasfilename(
            title="导出任务日历",
            defaultextension=".ics",
            filetypes=[("iCalendar", "*.ics"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            count = self.service.export_ics(path)
            messagebox.showinfo("完成", f"已导出 {count} 个任务到：\n{path}", parent=self)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _undo(self) -> None:
        if not messagebox.askyesno("确认", "撤销最近一次文件移动？", parent=self):
            return
        self.service.undo_last()

    def _open_record_target(self) -> None:
        selection = self.record_tree.selection()
        if not selection:
            return
        row = self._records.get(int(selection[0]))
        if row:
            self._open_path(row.get("dst", ""))

    # ------------------------------------------------------------------ 训练操作
    def _scan(self) -> None:
        if not self.cfg.library_root or not os.path.isdir(self.cfg.library_root):
            messagebox.showerror("错误", "资料库目录不存在，请先在设置中配置。", parent=self)
            return
        self.service.scan_library()

    def _rebuild(self) -> None:
        self.service.submit(self.service._rebuild_model, True)
        self._training_append("\n正在重建模型…", raw=True)

    def _eval(self) -> None:
        self.service.run_eval()
        self._training_append("\n正在评测…", raw=True)

    def _apply_eval_thresholds(self) -> None:
        recommended = (self._last_eval or {}).get("recommended") or {}
        threshold = recommended.get("threshold")
        if not threshold:
            messagebox.showinfo(
                "提示",
                "本次评测没有给出可用阈值：留出样本不足 5 条，或无法在目标精度下覆盖样本。\n"
                "建议资料库每类至少积累 5~10 份文件后再试。",
                parent=self,
            )
            return
        if float(threshold) >= float(self.cfg.auto_threshold):
            messagebox.showinfo(
                "提示",
                f"评测建议阈值 {threshold} 不低于当前自动阈值 {self.cfg.auto_threshold}，无需调整。",
                parent=self,
            )
            return
        self.cfg.auto_threshold = float(threshold)
        self.auto_var.set(str(threshold))
        self.cfg.save()
        self.service.cfg.auto_threshold = float(threshold)
        messagebox.showinfo("完成", f"自动阈值已调整为 {threshold}", parent=self)

    def _import_v6(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 v6.2 的 learning_data.db（JSON 格式）",
            filetypes=[("JSON", "*.db *.json"), ("所有文件", "*.*")],
        )
        if path:
            self.service.import_v6(path)

    # ------------------------------------------------------------------ 设置操作
    def _browse_library(self) -> None:
        path = filedialog.askdirectory(title="选择资料库根目录")
        if path:
            self.library_entry.delete(0, tk.END)
            self.library_entry.insert(0, path)

    def _add_watch(self) -> None:
        path = filedialog.askdirectory(title="选择监听目录（例如下载目录）")
        if path and path not in self.watch_list.get(0, tk.END):
            self.watch_list.insert(tk.END, path)

    def _remove_watch(self) -> None:
        selection = self.watch_list.curselection()
        if selection:
            self.watch_list.delete(selection[0])

    def _save_settings(self) -> None:
        library = self.library_entry.get().strip()
        watch_dirs = list(self.watch_list.get(0, tk.END))
        try:
            auto = float(self.auto_var.get())
            suggest = float(self.suggest_var.get())
            margin = float(self.margin_var.get())
        except ValueError:
            messagebox.showerror("错误", "阈值必须是数字。", parent=self)
            return
        if not (0 < suggest <= auto < 1):
            messagebox.showerror("错误", "阈值应满足 0 < 建议阈值 ≤ 自动阈值 < 1。", parent=self)
            return
        if not (0 <= margin < 1):
            messagebox.showerror("错误", "间隔应在 0~1 之间。", parent=self)
            return

        self.cfg.library_root = library
        self.cfg.watch_dirs = watch_dirs
        self.cfg.auto_threshold = auto
        self.cfg.suggest_threshold = suggest
        self.cfg.margin = margin
        self.cfg.include_content = bool(self.content_var.get())
        self.cfg.normalize()

        errors = self.cfg.validate()
        if errors:
            if not messagebox.askyesno(
                "配置有问题", "\n".join(errors) + "\n\n仍然保存吗？", parent=self
            ):
                return
        ok, start_errors = self.service.apply_config(self.cfg)
        if not ok:
            messagebox.showerror("应用失败", "\n".join(start_errors), parent=self)
        self.library_var.set(self.cfg.library_root or "（未设置）")
        self._dirty.update({"header", "pending", "tasks"})
        messagebox.showinfo("完成", "配置已保存。", parent=self)

    # ------------------------------------------------------------------ 提示
    def _remind(self) -> None:
        try:
            tasks = self.service.task_rows("open")
            messages = reminder_texts(tasks)
        except Exception:
            messages = []
        changed = set(messages) - self._last_reminders
        if changed:
            self._append_log("任务提醒：")
            for message in messages[:10]:
                self._append_log("  - " + message)
            self._last_reminders = set(messages)
        if messages:
            self.status_var.set(f"有 {len(messages)} 条任务提醒，详见任务中心/日志")
        # 每 30 分钟复查一次（应用长时间开着也能收到新提醒）
        try:
            self.after(30 * 60 * 1000, self._remind)
        except tk.TclError:
            pass

    def _about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"SmartFileOrganizer v{__version__}\n\n"
            "本地运行的智能归档 + 任务助手：不联网、不上传文件。\n"
            "分类基于 TF-IDF 语义近邻，正文抽取支持 docx/xlsx/pptx/txt，"
            "安装 pypdf 后可读 PDF。\n"
            "所有移动均可撤销；重复文件只隔离、不删除。",
            parent=self,
        )

    # ------------------------------------------------------------------ 工具
    def _append_log(self, message: str, error: bool = False) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 1200:
            self.log_text.delete("1.0", "200.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _training_append(self, message: str, raw: bool = False) -> None:
        self.training_text.configure(state=tk.NORMAL)
        self.training_text.insert(tk.END, message + ("\n" if not message.endswith("\n") else ""))
        self.training_text.see(tk.END)
        self.training_text.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    @staticmethod
    def _open_path(path: str) -> None:
        if not path:
            return
        if not os.path.exists(path):
            messagebox.showwarning("提示", f"路径不存在：\n{path}")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("无法打开", str(exc))

    def _on_close(self) -> None:
        try:
            self.service.close()
        finally:
            self.destroy()


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass


def _acquire_lock(data_dir: str):
    path = Path(data_dir) / "app.lock"
    handle = None
    try:
        handle = open(path, "a+")
        import msvcrt

        handle.seek(0)
        handle.write("x")
        handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        return None


def main(data_dir: str | None = None) -> int:
    _enable_dpi_awareness()
    cfg = Config.load(data_dir)
    lock = _acquire_lock(cfg.data_dir)
    if lock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("已在运行", "SmartFileOrganizer 已经启动了一个窗口。")
        root.destroy()
        return 1
    app = App(cfg)
    try:
        app.mainloop()
    finally:
        try:
            lock.close()
        except Exception:
            pass
    return 0
