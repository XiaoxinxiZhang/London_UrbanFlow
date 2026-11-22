#!/usr/bin/env python3
"""Cross-platform launcher for the London UrbanFlow dashboard.

Usage:
    python run.py                  # http://127.0.0.1:8765/
    python run.py --open           # also open the browser
    python run.py --host 0.0.0.0   # expose on a server/LAN interface
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MINIMUM = (3, 10)

if sys.version_info < MINIMUM:
    raise SystemExit(
        f"需要 Python {MINIMUM[0]}.{MINIMUM[1]} 或更高版本，当前为 {sys.version.split()[0]}"
    )

sys.path.insert(0, str(ROOT / "tools"))

try:
    from server import main
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        raise SystemExit(
            "缺少 Flask。请先安装依赖：python -m pip install -r requirements.txt"
        ) from None
    raise

if __name__ == "__main__":
    raise SystemExit(main())
