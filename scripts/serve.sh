#!/usr/bin/env bash
# Run the dashboard in the foreground on Linux/macOS.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${URBANFLOW_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
  elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
    PY="$ROOT/.venv/Scripts/python.exe"
  elif command -v python3 >/dev/null 2>&1; then
    PY=python3
  elif command -v python >/dev/null 2>&1; then
    PY=python
  else
    echo "未找到 Python。请安装 Python 3.10+，或设置 URBANFLOW_PYTHON。" >&2
    exit 1
  fi
fi

if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "需要 Python 3.10 或更高版本（当前: $("$PY" -V 2>&1)）" >&2
  exit 1
fi

if ! "$PY" -c 'import flask' >/dev/null 2>&1; then
  echo "未安装 Flask。请运行: $PY -m pip install -r requirements.txt" >&2
  exit 1
fi

exec "$PY" "$ROOT/run.py" "$@"
