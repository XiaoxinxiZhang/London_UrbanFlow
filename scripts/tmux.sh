#!/usr/bin/env bash
# Keep the dashboard running under tmux on a Linux server.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="${URBANFLOW_TMUX_SESSION:-urbanflow}"
HOST="${URBANFLOW_HOST:-0.0.0.0}"
PORT="${URBANFLOW_PORT:-8765}"
LOG="${URBANFLOW_LOG:-$ROOT/data/live/server.log}"
ACTION="${1:-start}"

if ! command -v tmux >/dev/null 2>&1; then
  echo "未找到 tmux。请先安装，例如: sudo apt install tmux" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG")"
touch "$LOG"

start() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux 会话 $SESSION 已在运行。"
  else
    local extra=""
    for name in URBANFLOW_DB URBANFLOW_TRUSTED_HOSTS URBANFLOW_TRUSTED_ORIGINS URBANFLOW_PYTHON; do
      if [ -n "${!name:-}" ]; then
        extra="$extra $name=$(printf '%q' "${!name}")"
      fi
    done
    tmux new-session -d -s "$SESSION" \
      "cd $(printf '%q' "$ROOT") && URBANFLOW_HOST=$(printf '%q' "$HOST") URBANFLOW_PORT=$(printf '%q' "$PORT")$extra bash $(printf '%q' "$ROOT/scripts/serve.sh") 2>&1 | tee -a $(printf '%q' "$LOG")"
    sleep 1
    if ! tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "启动失败，最近日志：" >&2
      tail -n 20 "$LOG" >&2 || true
      exit 1
    fi
    echo "已启动 tmux 会话 $SESSION。"
  fi

  if [ "$HOST" = "0.0.0.0" ] || [ "$HOST" = "::" ]; then
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    ip="${ip:-127.0.0.1}"
    echo "访问: http://$ip:$PORT/"
    echo "提示: 服务没有身份验证，请用防火墙限制来源，或改用 SSH 隧道。"
  else
    echo "访问: http://$HOST:$PORT/"
  fi
  echo "附加: tmux attach -t $SESSION"
  echo "日志: $LOG"
}

stop() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
    echo "已停止 tmux 会话 $SESSION。"
  else
    echo "tmux 会话 $SESSION 未运行。"
  fi
}

case "$ACTION" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    stop || true
    sleep 1
    start
    ;;
  status)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "running: $SESSION"
    else
      echo "stopped: $SESSION"
      exit 1
    fi
    ;;
  attach)
    exec tmux attach -t "$SESSION"
    ;;
  logs)
    exec tail -n 100 -f "$LOG"
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|attach|logs}" >&2
    exit 2
    ;;
esac
