#!/usr/bin/env bash
# 重启 basis-monitor
# 自动识别运行模式: systemd 服务 -> systemctl restart；否则进程模式（stop + start）
# 用法: sudo bash restart.sh [端口]
set -euo pipefail

APP_NAME=basis-monitor
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

# systemd 模式检测
if [ -f "/etc/systemd/system/$APP_NAME.service" ] && command -v systemctl >/dev/null 2>&1; then
  echo ">> systemd 模式: systemctl restart $APP_NAME"
  sudo systemctl restart "$APP_NAME"
  sleep 2
  systemctl --no-pager --lines=8 status "$APP_NAME" 2>/dev/null | grep -E "Active:|●" || true
  echo "   日志: journalctl -u $APP_NAME -f"
  exit 0
fi

# 进程模式
echo ">> 进程模式: stop + start"
bash "$APP_DIR/stop.sh"
bash "$APP_DIR/start.sh" "${1:-}"
echo ">> 重启完成"