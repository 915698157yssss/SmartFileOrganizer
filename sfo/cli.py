"""命令行入口：便于批处理、测试和排障。

    python main.py --data-dir D:\\sfo-data scan
    python main.py scan
    python main.py classify "D:\\Downloads\\xx.pdf"
    python main.py eval
    python main.py tasks --scope week
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Config, MODE_LABELS, MODES
from .service import Service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sfo", description=f"SmartFileOrganizer {__version__} 命令行"
    )
    parser.add_argument("--data-dir", help="数据目录（默认 %%LOCALAPPDATA%%\\SmartFileOrganizer）")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化配置")
    init.add_argument("--library", required=True, help="资料库根目录")
    init.add_argument("--watch", action="append", default=[], help="监听目录，可重复")
    init.add_argument("--mode", choices=MODES, default="suggest")

    sub.add_parser("status", help="查看状态")

    scan = sub.add_parser("scan", help="扫描资料库并训练")
    scan.add_argument("--max", type=int, default=0, help="最多扫描多少个文件（0=配置值）")

    ev = sub.add_parser("eval", help="留出集评测")
    ev.add_argument("--top", type=int, default=5)

    cl = sub.add_parser("classify", help="处理单个文件")
    cl.add_argument("file")
    cl.add_argument("--mode", choices=MODES, default="")
    cl.add_argument("--dry-run", action="store_true", help="只判定不移动")

    sub.add_parser("serve", help="前台运行监听服务（Ctrl+C 退出）")

    tasks = sub.add_parser("tasks", help="查看任务")
    tasks.add_argument("--scope", default="open",
                       choices=["all", "open", "candidate", "todo", "doing", "today", "week",
                                "overdue", "done", "snoozed", "dismissed"])
    tasks.add_argument("--ics", help="导出到 .ics 文件")

    sub.add_parser("undo", help="撤销最近一次移动")
    imp = sub.add_parser("import-v6", help="导入 v6.2 的 learning_data JSON")
    imp.add_argument("file")
    return parser


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.data_dir)

    if args.command == "init":
        cfg.library_root = str(Path(args.library).expanduser())
        cfg.watch_dirs = [str(Path(p).expanduser()) for p in args.watch]
        cfg.mode = args.mode
        cfg.normalize()
        cfg.save()
        print(f"配置已保存：{cfg.config_path}")
        errors = cfg.validate()
        if errors:
            print("注意：")
            for error in errors:
                print("  -", error)
        return 0 if not errors else 2

    service = Service(cfg)
    logging.getLogger("sfo").addHandler(logging.StreamHandler(sys.stdout))
    try:
        if args.command == "status":
            _print_json({
                "version": __version__,
                "data_dir": cfg.data_dir,
                "library_root": cfg.library_root,
                "watch_dirs": cfg.watch_dirs,
                "mode": cfg.mode,
                "mode_label": MODE_LABELS.get(cfg.mode, ""),
                "counts": service.counts(),
                "model": service.model_info
                or {"note": "模型在首次归档或「重建模型」时加载", "backend": cfg.model_backend},
            })
            return 0

        if args.command == "scan":
            if args.max:
                cfg.max_scan_files = args.max
            result = service.scan_library(sync=True)
            _print_json(result)
            return 0 if result.get("ok") else 1

        if args.command == "eval":
            result = service.run_eval(sync=True)
            if not result.get("ok"):
                _print_json(result)
                return 1
            _print_json({
                key: result[key]
                for key in ("top1", "top3", "train", "test", "classes", "recommended")
            })
            if result.get("confusion"):
                print("\n混淆对：")
                for row in result["confusion"][: args.top]:
                    print(f"  {row['truth']} → {row['pred']}：{row['count']}")
            return 0

        if args.command == "classify":
            mode = args.mode or cfg.mode
            if args.dry_run:
                mode = "observe"
            result = service.classify_now(args.file, mode)
            _print_json(result)
            if result.get("action") == "error":
                return 1
            return 0

        if args.command == "serve":
            ok, errors = service.start_watching()
            if not ok:
                for error in errors:
                    print("错误：", error, file=sys.stderr)
                return 2
            print("监听中，Ctrl+C 退出……")
            try:
                while True:
                    import time

                    time.sleep(0.5)
            except KeyboardInterrupt:
                service.stop_watching()
            return 0

        if args.command == "tasks":
            if args.ics:
                count = service.export_ics(args.ics)
                print(f"已导出 {count} 个任务到 {args.ics}")
                return 0
            rows = service.task_rows(args.scope)
            if not rows:
                print("没有任务")
                return 0
            for row in rows:
                due = row.get("due") or "—"
                print(f"#{row['id']:<4} {due:<12} P{row['priority']} {row['status']:<9} "
                      f"{row['dtype'] or '':<8} {row['title']}")
            return 0

        if args.command == "undo":
            ok, message = service.undo_now()
            print(message)
            return 0 if ok else 1

        if args.command == "import-v6":
            result = service.import_v6(args.file, sync=True)
            _print_json(result)
            return 0 if result.get("ok") else 1

        return 1
    finally:
        service.close()
