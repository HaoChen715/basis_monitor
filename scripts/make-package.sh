#!/usr/bin/env bash
# 制作部署包：把二进制 + 配置 + 部署脚本组装成可分发的 tar.gz
# 产物: dist/basis-monitor-package/  与  dist/basis-monitor-package.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."

bash scripts/build.sh

PKG=dist/basis-monitor-package
rm -rf "$PKG"
mkdir -p "$PKG/config"
cp dist/basis-monitor "$PKG/"
cp config/config.ini config/rules.json "$PKG/config/"
cp deploy/deploy.sh deploy/start.sh deploy/stop.sh deploy/restart.sh "$PKG/"
cp DEPLOY.md "$PKG/"
chmod +x "$PKG/deploy.sh" "$PKG/start.sh" "$PKG/stop.sh" "$PKG/restart.sh"

tar -czf dist/basis-monitor-package.tar.gz -C dist basis-monitor-package
echo ">> 部署包: dist/basis-monitor-package.tar.gz"
du -sh "$PKG" | awk '{print "   解压后大小:", $1}'
echo "   上传到内网机器后: tar -xzf basis-monitor-package.tar.gz && sudo bash basis-monitor-package/deploy.sh"