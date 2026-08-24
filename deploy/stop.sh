#!/usr/bin/env bash
# 停止 basis-monitor
# 自动识别运行模式: systemd 服务 -> systemctl stop；否则进程模式（pkill）
# 用法: sudo bash stop.sh
set -euo pipefail

APP_NAME=basis-monitor
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$APP_DIR/basis-monitor"

# systemd 模式检测
if [ -f "/etc/systemd/system/$APP_NAME.service" ] && command -v systemctl >/dev/null 2>&1; then
  echo ">> systemd 模式: systemctl stop $APP_NAME"
  sudo systemctl stop "$APP_NAME"
  exit 0
fi

# 进程模式
PIDS=$(pgrep -f "$BIN" 2>/dev/null || true)
if [ -n "$PIDS" ]; then
  echo ">> 停止进程: $PIDS"
  kill $PIDS 2>/dev/null || true
  sleep 2
  REMAIN=$(pgrep -f "$BIN" 2>/dev/null || true)
  if [ -n "$REMAIN" ]; then
    echo ">> 强杀残留进程: $REMAIN"
    kill -9 $REMAIN 2>/dev/null || true
  fi
  echo ">> 已停止"
else
  echo ">> basis-monitor 未在运行"
fi