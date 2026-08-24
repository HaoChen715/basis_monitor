#!/usr/bin/env bash
# 打包脚本：使用独立构建环境生成 Linux x86_64 单文件二进制。
# 产物: dist/basis-monitor
# 注意: 二进制依赖 glibc(2.34, Rocky9 系列一致)，请在 Rocky 9 或兼容环境构建。
set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_VENV="${BUILD_VENV:-/tmp/basis-monitor-build-venv}"

# 1. 准备构建环境（首次自动创建并安装依赖）
if [ ! -x "$BUILD_VENV/bin/python" ]; then
  echo ">> 创建构建环境: $BUILD_VENV"
  python3.11 -m venv "$BUILD_VENV"
fi
if ! "$BUILD_VENV/bin/python" -c "import flask,requests,bs4,PyInstaller" 2>/dev/null; then
  echo ">> 安装构建/运行依赖 ..."
  "$BUILD_VENV/bin/pip" install -q --upgrade pip
  "$BUILD_VENV/bin/pip" install -q "pyinstaller==6.22.2" \
    "flask>=3.0" "requests>=2.31" "beautifulsoup4>=4.12"
fi

# 2. 打包
echo ">> 开始打包 ..."
rm -rf dist build .spec_cache
"$BUILD_VENV/bin/pyinstaller" \
  --noconfirm --clean --onefile \
  --name basis-monitor \
  --add-data "$(pwd)/templates:templates" \
  --distpath dist \
  --workpath build \
  --specpath .spec_cache \
  app.py

BIN=dist/basis-monitor
if [ ! -x "$BIN" ]; then
  echo "!! 打包失败: 未生成 $BIN" >&2
  exit 1
fi
echo ">> 打包成功: $(ls -lh "$BIN" | awk '{print $5}')  ->  $BIN"