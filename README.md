# 基差 / 跨期价差监控系统 (Basis Monitor)

基于 tinysoft / 新浪实时行情，监控股指期货（IH/IF/IC/IM）的**基差**与**跨期价差**，
达到阈值时通过**企业微信**发送告警。支持多用户登录与数据隔离，可打包为单文件二进制部署到内网。

## 功能特性

- **行情监控**：基差（期货 − 现货）、跨期价差（合约A − 合约B）、矫正基差、隐含利率等 10 项指标
- **证券行情价格监控**：按代码监控股票 / ETF / 可转债（如可转债ETF `511380`）的最新价、涨跌幅、成交额等指标，与基差监控共用同一套规则与告警
- **数据源**：新浪财经免费实时行情（秒级）为主，hybrid 模式下后台合并 tinysoft 衍生指标；证券行情价格走新浪证券接口
- **多用户隔离**：登录认证；管理员全权，普通用户只见归属自己的规则与相关行情，用户间互相隔离
- **价格走势图**：自动记录每个监控项的历史数值，按监控项查看曲线，并叠加 MIN / MAX 告警阈值线（点击监控项弹出）
- **企业微信告警**：高于 MAX / 低于 MIN 触发，支持告警冷却去重、恢复通知、心跳看门狗；支持内网网关
- **配置前端**：规则增删改查、实时行情、价格走势、告警日志、用户管理、系统设置，无需改代码
- **非交易时段**：展示最近一次行情缓存，标注抓取时间
- **一键部署**：单文件二进制（PyInstaller 内嵌 Python 3.11），支持 systemd 开机自启或进程模式

## 快速开始（源码运行）

```bash
git clone <repo-url>
cd basis-monitor
cp config/config.ini.example config/config.ini   # 填写 webhook key、管理员密码等
pdm install
pdm run dev                                       # 访问 http://127.0.0.1:8080/
```

> 端口说明：非 root 默认回退 8080；root 用 80（config.ini `serverport`）。
> 80 为特权端口，非 root 自动回退 `serverportfallback`。

## Docker 部署（推荐）

```bash
docker compose up -d --build        # 构建并启动，访问 http://<host>:8080/
```

- 配置/规则/用户/历史/日志持久化到宿主机 `./data/`（挂载到容器 `/app/config`、`/app/logs`）
- 首次启动自动生成 `data/config/config.ini`，可用前端「系统设置」填写企业微信 key、数据源等
- 改端口：编辑 `docker-compose.yml` 的 `ports` 与 `PORT`；设置正确 `TZ`（默认 `Asia/Shanghai`）以保证交易时段判断
- 端口/数据源等详见 [DEPLOY.md](DEPLOY.md) 的「Docker 部署」章节

## 二进制打包与部署

```bash
bash scripts/make-package.sh      # 生成 dist/basis-monitor-package.tar.gz
bash scripts/smoke.sh             # 冒烟测试打包产物
```

内网部署（systemd 或进程模式）：
```bash
sudo bash basis-monitor-package/deploy.sh          # 自动检测 systemd；或 --systemd / --process
sudo bash basis-monitor-package/start.sh
```

## 文档

- **部署/迁移/运维/排障**：见 [DEPLOY.md](DEPLOY.md)
- 脚本说明：见 DEPLOY.md「九、脚本说明」

## 技术栈

Python 3.11 · Flask · requests · BeautifulSoup4 · PyInstaller · PDM