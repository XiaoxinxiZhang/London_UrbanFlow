#!/usr/bin/env bash
# One-time Linux/macOS setup: create .venv and install the web dependencies.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${URBANFLOW_PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PY=python3
  elif command -v python >/dev/null 2>&1; then
    PY=python
  else
    echo "未找到 Python。请安装 Python 3.10+ 后重试。" >&2
    exit 1
  fi
fi

if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "需要 Python 3.10 或更高版本（当前: $("$PY" -V 2>&1)）" >&2
  exit 1
fi

if ! "$PY" -m venv .venv; then
  echo "创建虚拟环境失败。Debian/Ubuntu 可先安装: sudo apt install python3-venv" >&2
  exit 1
fi

VENV_PY="$ROOT/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  VENV_PY="$ROOT/.venv/Scripts/python.exe"
fi
if [ ! -x "$VENV_PY" ]; then
  echo "虚拟环境创建失败：找不到 .venv/bin/python 或 .venv/Scripts/python.exe" >&2
  exit 1
fi

"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r requirements.txt

echo
echo "依赖安装完成。下一步："
echo "  bash scripts/serve.sh --host 0.0.0.0 --port 8765"
echo "或后台运行："
echo "  bash scripts/tmux.sh start"
