#!/usr/bin/env bash
# 部署脚本
# 支持两种运行模式（自动检测，也可显式指定）：
#   --systemd : 安装为 systemd 服务（开机自启、崩溃自动重启），需要 sudo + systemd
#   --process : 程序所在目录进程模式（无 systemd 环境，如 k8s 虚拟机）
# 默认: 检测到 systemd 用 systemd 模式，否则进程模式。
# 所有路径均在程序所在目录，不写系统目录。
# 用法: sudo bash deploy.sh [--systemd|--process]
set -euo pipefail

APP_NAME=basis-monitor
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

MODE=auto
for arg in "$@"; do
  case "$arg" in
    --systemd) MODE=systemd ;;
    --process) MODE=process ;;
  esac
done
if [ "$MODE" = "auto" ]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl --version >/dev/null 2>&1; then
    MODE=systemd
  else
    MODE=process
  fi
fi
echo ">> 运行模式: $MODE  (程序目录: $APP_DIR)"

# 1. 初始化 config/ 与 logs/
mkdir -p "$APP_DIR/config" "$APP_DIR/logs"
if [ ! -f "$APP_DIR/config/config.ini" ]; then
  echo "!! 缺少 $APP_DIR/config/config.ini，请确认包完整" >&2
  exit 1
fi
chmod +x "$APP_DIR/basis-monitor" "$APP_DIR/start.sh" "$APP_DIR/stop.sh" "$APP_DIR/restart.sh" 2>/dev/null || true

# 2. 读取端口并放行防火墙
PORT=$(sed -n 's/^serverport *= *\(.*\)/\1/p' "$APP_DIR/config/config.ini" | tr -d '[:space:]')
PORT="${PORT:-80}"
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  echo ">> 防火墙放行端口 ${PORT}/tcp ..."
  firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null 2>&1 || true
  firewall-cmd --reload >/dev/null 2>&1 || true
  echo "   已放行（若改用其他端口启动，请另行放行: firewall-cmd --permanent --add-port=<端口>/tcp && firewall-cmd --reload）"
fi

if [ "$MODE" = "systemd" ]; then
  # ---------------------------------------------------------- systemd 模式
  echo ">> 安装 systemd 服务 $APP_NAME ..."
  # 停止旧实例（进程模式残留或已存在服务）
  systemctl stop "$APP_NAME" 2>/dev/null || true
  pkill -f "$APP_DIR/basis-monitor" 2>/dev/null || true

  cat > "/etc/systemd/system/$APP_NAME.service" <<EOF
[Unit]
Description=Basis / Calendar Spread Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=BASIS_MONITOR_DATA=$APP_DIR
Environment=PORT=$PORT
ExecStart=$APP_DIR/basis-monitor
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$APP_NAME"
  systemctl start "$APP_NAME"

  echo
  echo "======================================================"
  echo "  已安装为 systemd 服务（开机自启）"
  echo "  服务名:   $APP_NAME"
  echo "  状态:     systemctl status $APP_NAME"
  echo "  日志:     journalctl -u $APP_NAME -f"
  echo "  重启:     sudo bash $APP_DIR/restart.sh"
  echo "  停止:     sudo bash $APP_DIR/stop.sh"
  echo "  启动:     sudo bash $APP_DIR/start.sh"
  echo "  前端:     http://<本机IP>:${PORT}/"
  echo "  配置文件: $APP_DIR/config/config.ini"
  echo "======================================================"
else
  # ---------------------------------------------------------- 进程模式
  echo
  echo "======================================================"
  echo "  进程模式（无 systemd，程序所在目录）"
  echo "  启动:   sudo bash $APP_DIR/start.sh [端口]"
  echo "  停止:   sudo bash $APP_DIR/stop.sh"
  echo "  重启:   sudo bash $APP_DIR/restart.sh"
  echo "  前端:   http://<本机IP>:<serverport>/"
  echo "  配置文件: $APP_DIR/config/config.ini"
  echo "======================================================"
fi

echo
echo ">> 前置检查："
echo "   - 外网白名单: hq.sinajs.cn / web.tinysoft.com.cn / qyapi.weixin.qq.com (443)"
echo "   - 企业微信告警走内网网关时，编辑 config.ini 的 wechatapibase"
echo "   - 如需代理出网，systemd 模式在 service 加 Environment=HTTPS_PROXY=...，进程模式启动前 export HTTPS_PROXY=..."