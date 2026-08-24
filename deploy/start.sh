#!/usr/bin/env bash
# 启动 basis-monitor
# 自动识别运行模式: 已安装 systemd 服务 -> systemctl；否则进程模式（程序所在目录）
# 用法: sudo bash start.sh [端口]
set -euo pipefail

APP_NAME=basis-monitor
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$APP_DIR/basis-monitor"
DATA_DIR="$APP_DIR"

# systemd 模式检测
if [ -f "/etc/systemd/system/$APP_NAME.service" ] && command -v systemctl >/dev/null 2>&1; then
  echo ">> systemd 模式: systemctl start $APP_NAME"
  sudo systemctl start "$APP_NAME"
  sleep 2
  systemctl --no-pager --lines=8 status "$APP_NAME" 2>/dev/null | grep -E "Active:|●" || true
  echo "   日志: journalctl -u $APP_NAME -f"
  exit 0
fi

# 进程模式
if [ ! -x "$BIN" ]; then
  echo "!! 未找到 $BIN" >&2
  exit 1
fi
PORT="${1:-${PORT:-}}"
mkdir -p "$DATA_DIR/logs"
if pgrep -f "$BIN" >/dev/null 2>&1; then
  echo "!! basis-monitor 已在运行 (PID $(pgrep -f "$BIN" | tr '\n' ' '))"
  exit 0
fi

CMD_ENV=()
if [ -n "$PORT" ]; then
  CMD_ENV+=("PORT=$PORT")
fi
nohup env "${CMD_ENV[@]}" "$BIN" >> "$DATA_DIR/logs/console.log" 2>&1 &
echo ">> 已后台启动 basis-monitor (PID $!)  程序目录: $APP_DIR"
if [ -n "$PORT" ]; then echo "   端口: $PORT"; else echo "   端口: config.ini 的 serverport"; fi
echo "   日志: tail -f $DATA_DIR/logs/console.log"
echo "   停止: bash $APP_DIR/stop.sh"