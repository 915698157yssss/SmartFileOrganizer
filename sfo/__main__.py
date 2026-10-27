"""允许 `python -m sfo` 直接启动图形界面。"""

from .ui import main

raise SystemExit(main())
