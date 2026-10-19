#!/bin/sh
# 容器入口：首次启动时用示例文件初始化可持久化配置目录。
set -e

mkdir -p /app/config /app/logs

# config.ini：首次启动从内置示例生成（挂载卷为空时）
if [ ! -f /app/config/config.ini ] && [ -f /app/config.ini.example ]; then
  cp /app/config.ini.example /app/config/config.ini
fi

# rules.json：首次启动带入示例规则
if [ ! -f /app/config/rules.json ] && [ -f /app/rules.json.example ]; then
  cp /app/rules.json.example /app/config/rules.json
fi

exec "$@"
