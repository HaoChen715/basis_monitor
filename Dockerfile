# syntax=docker/dockerfile:1
# 基差/跨期价差 + 证券行情价格监控系统 —— 单镜像部署
# 构建:  docker build -t basis-monitor:latest .
# 运行:  docker compose up -d   （或见 README / DEPLOY.md）
FROM python:3.11-slim

LABEL org.opencontainers.image.title="basis-monitor" \
      org.opencontainers.image.description="基差/跨期价差 + 证券行情价格监控（企业微信告警）"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    HOST=0.0.0.0 \
    PORT=8080

# tzdata 保证交易时段判断使用正确的时区；curl 用于容器健康检查
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata curl \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo "$TZ" > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖层单独缓存
COPY pyproject.toml ./
RUN pip install --no-cache-dir "flask>=3.0" "requests>=2.31" "beautifulsoup4>=4.12"

# 应用代码
COPY app.py ./
COPY monitor ./monitor
COPY templates ./templates

# 首次启动时由 entrypoint 从示例生成 config/config.ini、config/rules.json
COPY config/config.ini.example ./config.ini.example
COPY config/rules.json ./rules.json.example
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /app/config /app/logs

EXPOSE 8080
# 配置、规则、用户、历史与日志落盘持久化
VOLUME ["/app/config", "/app/logs"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "app.py"]
