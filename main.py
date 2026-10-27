"""SmartFileOrganizer v7 启动入口。

    python main.py                  # 图形界面
    python main.py scan             # 命令行（见 sfo/cli.py）
    python main.py --data-dir D:\\sfo-data status
"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from sfo.cli import main as cli_main

        return cli_main()
    from sfo.ui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
