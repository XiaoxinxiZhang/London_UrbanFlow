#!/usr/bin/env bash
# Run one due-data collection pass; suitable for a Linux cron job.
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

exec "$PY" "$ROOT/tools/collect_live.py" run-once "$@"
