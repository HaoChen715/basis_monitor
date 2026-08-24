#!/usr/bin/env bash
# 打包产物冒烟测试：把二进制+配置放到同一临时目录，模拟"程序所在目录"运行，
# 不设置 BASIS_MONITOR_DATA，验证程序自动使用自身所在目录的 config/ 与 logs/。
# 用法: scripts/smoke.sh [端口]  默认 8090
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8090}"
BIN=dist/basis-monitor

if [ ! -x "$BIN" ]; then
  echo "!! 未找到 $BIN，请先运行 scripts/build.sh" >&2
  exit 1
fi

# 程序目录 = 临时目录（二进制 + config/ 在一起，logs/ 由程序自建）
APP_DIR=$(mktemp -d)
cp dist/basis-monitor "$APP_DIR/"
mkdir -p "$APP_DIR/config"
cp config/config.ini config/rules.json "$APP_DIR/config/"
# 放宽交易时段，便于任何时候验证行情抓取
sed -i 's/marketclose = 150000/marketclose = 235959/' "$APP_DIR/config/config.ini"
# 指定初始管理员密码，便于登录验证（确保无残留 users.json，保证干净引导）
rm -f "$APP_DIR/config/users.json"
sed -i 's/^adminpass *=.*/adminpass = Admin@123/' "$APP_DIR/config/config.ini"

echo ">> 启动 $APP_DIR/basis-monitor (port=$PORT) ..."
cd "$APP_DIR"
PORT="$PORT" ./basis-monitor >/tmp/basis-monitor-smoke.log 2>&1 &
BINPID=$!

cleanup() { kill "$BINPID" 2>/dev/null || true; pkill -P "$BINPID" 2>/dev/null || true; }
trap cleanup EXIT

OK=0
for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    OK=1; break
  fi
  sleep 1
done
if [ "$OK" != "1" ]; then
  echo "!! 启动失败，日志:" >&2
  grep -vE "werkzeug" /tmp/basis-monitor-smoke.log | tail -15 >&2
  exit 1
fi

echo ">> 验证程序所在目录已自建 logs/ ..."
if [ -d "$APP_DIR/logs" ] && ls "$APP_DIR/logs/"*.log >/dev/null 2>&1; then
  echo "   OK: $APP_DIR/logs/$(ls "$APP_DIR/logs/" | head -1)"
else
  echo "   WARN: 程序所在目录未发现日志，检查 $APP_DIR"
fi

echo ">> 等待行情抓取 (约 8s) ..."
sleep 8
echo ">> 验证登录与权限 ..."
if curl -s -o /dev/null -w "%{http_code}" -m 3 "http://127.0.0.1:$PORT/api/rules" | grep -q 401; then
  echo "   OK: 未登录访问规则接口返回 401"
else
  echo "   FAIL: 未登录应返回 401" >&2; exit 1
fi
LOGIN=$(curl -s -m 3 -c /tmp/basis-monitor-smoke-cookie -X POST "http://127.0.0.1:$PORT/api/login" \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"Admin@123"}')
echo "   admin 登录: $(echo "$LOGIN" | /usr/bin/python3 -c "import sys,json;print('ok role='+json.load(sys.stdin)['user']['role'])")"

echo "== /api/config (admin) =="
curl -s -m 5 -b /tmp/basis-monitor-smoke-cookie "http://127.0.0.1:$PORT/api/config" | /usr/bin/python3 -c "import sys,json;d=json.load(sys.stdin);print('datasource =',d['datasource'],'| wechatapibase =',d['wechatapibase'],'| webhook_set =',d['wechathookkey_set'])"
echo "== /api/status (admin) =="
curl -s -m 5 -b /tmp/basis-monitor-smoke-cookie "http://127.0.0.1:$PORT/api/status" | /usr/bin/python3 -c "import sys,json;d=json.load(sys.stdin);print('engine =',d['engine_running'],'| last_fetch =',d['last_fetch_time'],'| err =',(d['last_error'] or 'none')[:60])"
echo "== /api/market-data (admin) =="
curl -s -m 5 -b /tmp/basis-monitor-smoke-cookie "http://127.0.0.1:$PORT/api/market-data" | /usr/bin/python3 -c "
import sys,json;d=json.load(sys.stdin)
im=d.get('IM2612',{})
print('IM2612 期货价格 =',im.get('期货价格'),'| 当前基差 =',im.get('当前基差'),'| 矫正基差 =',im.get('矫正基差'),'| metrics =',len(im))
print('contracts =',len(d))
"
echo ">> 冒烟测试通过（程序目录: $APP_DIR）"