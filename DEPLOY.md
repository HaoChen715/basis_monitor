# 基差 / 跨期价差监控系统 — 内网迁移部署方案

> 程序已打成 Linux x86_64 单文件二进制（PyInstaller，Python 3.11 内嵌），
> 目标环境为 **Rocky Linux 9.4**（与构建机同 glibc 2.34，可直接运行）。

## 一、交付物清单

`dist/basis-monitor-package.tar.gz`（约 12M），解压后：

```
basis-monitor-package/
├── basis-monitor          # 单文件可执行程序（含 Python + 全部依赖）
├── config/
│   ├── config.ini         # 全局配置（端口 / webhook key / 内网接口 / 数据源等）
│   └── rules.json         # 监控规则（也可在前端页面配置）
├── deploy.sh              # 安装脚本（自动检测 systemd / 进程模式）
├── start.sh / stop.sh / restart.sh   # 启停/重启脚本（systemd 与进程模式通用）
└── DEPLOY.md              # 迁移与运维说明
```

> **两种运行模式**（`deploy.sh` 自动检测，可用 `--systemd` / `--process` 显式指定）：
> - **systemd 模式**：适用于有 sudo + systemd 的机器，开机自启、崩溃自动重启
> - **进程模式**：程序所在目录运行，适用于无 systemd 的 k8s 虚拟机等

> 目标环境为 **k8s 虚拟机（有 root，无 systemd）**：程序启停由
> `start.sh` / `stop.sh` 管理，也可自行用任意进程管理方式（nohup、supervisor、k8s Job 等）。
>
> **路径约定：所有运行路径（config/、logs/、rules.json）均在程序所在目录**，
> 不写系统目录（无 /opt、/var/lib）。程序自动使用自身所在目录。

## 二、前置准备（内网侧一次性配置）

### 1. 外网白名单
程序运行需要访问以下域名（均为 HTTPS/443，DNS 需能解析）：

| 域名 | 用途 |
|---|---|
| `hq.sinajs.cn` | 新浪实时行情（默认数据源，亚秒级） |
| `web.tinysoft.com.cn` | tinysoft 衍生指标（矫正基差/隐含利率等，hybrid 模式后台合并） |
| `qyapi.weixin.qq.com` | 企业微信告警（若走内网网关则改为放行内网网关） |

> 若内网只能经 HTTP 代理出网：启动前 `export HTTPS_PROXY=http://代理地址:端口`，
> 或写入 `start.sh` 的启动命令前（程序基于 requests，自动识别该变量）。

### 2. 防火墙（放行前端访问端口）— 无法访问服务的首要排查项
若内网其他机器无法打开前端，基本都是防火墙未放行端口：
```bash
# <端口> 为 config.ini 的 serverport（默认 80）
sudo firewall-cmd --permanent --add-port=<端口>/tcp && sudo firewall-cmd --reload
```
> `deploy.sh` 已自动读取 `serverport` 并放行（firewalld 激活时）。若改用环境变量 `PORT`
> 启动，请手动放行对应端口。本机 `curl http://127.0.0.1:<端口>` 能通但其他机器不通，
> 即为防火墙问题。

### 3. SELinux（Rocky 默认 Enforcing）
程序在**程序所在目录**内写 config/logs。若程序目录位于受保护路径（如
`/data`、`/home` 等）且被 SELinux 拦截、出现权限/写入报错，放行该目录：
```bash
# 将 <程序目录> 换成实际路径，例如 /data/basis-monitor
semanage fcontext -a -t var_t '<程序目录>(/.*)?'
restorecon -R <程序目录>
```

## 三、部署步骤（内网机器上执行）

所有文件保持在解压后的目录内，程序自动以自身所在目录为数据根目录。

### 方式 A：systemd 服务（推荐，有 sudo + systemd 的机器）

```bash
# 1. 上传并解压（目录可任意命名/位置）
tar -xzf basis-monitor-package.tar.gz

# 2. 安装并启动（自动注册 systemd 服务、开机自启、放行防火墙端口）
sudo bash basis-monitor-package/deploy.sh          # 自动检测 systemd
#   或强制: sudo bash basis-monitor-package/deploy.sh --systemd

# 3. 日常管理
sudo bash basis-monitor-package/restart.sh         # 重启
sudo bash basis-monitor-package/stop.sh            # 停止
sudo bash basis-monitor-package/start.sh           # 启动
systemctl status basis-monitor                     # 查看状态
journalctl -u basis-monitor -f                     # 查看日志

# 4. 访问前端
http://<内网IP>:<serverport>/
```
服务特性：开机自启（`systemctl enable`）、进程崩溃自动重启（`Restart=always`）。

### 方式 B：进程模式（无 systemd 环境，如 k8s 虚拟机）

```bash
# 1. 上传并解压
tar -xzf basis-monitor-package.tar.gz

# 2. 安装（只装文件，不依赖 systemd）
sudo bash basis-monitor-package/deploy.sh --process

# 3. 后台启动 / 停止 / 重启
sudo basis-monitor-package/start.sh [端口]
sudo basis-monitor-package/stop.sh
sudo basis-monitor-package/restart.sh [端口]
tail -f basis-monitor-package/logs/console.log
```

> deploy.sh 不安装到 /opt、/var/lib 等系统目录，所有路径均在程序所在目录；
> config/ 与 logs/ 由程序启动时自动创建。迁移 = 整体移动/复制整个目录即可。

### 端口配置（已从代码中抽出，二选一）

1. **配置文件**：编辑程序目录下 `config/config.ini`
   ```ini
   [Global]
   serverhost = 0.0.0.0
   serverport = 80            # 默认 80（特权端口）
   serverportfallback = 5000  # 非 root 运行时的回退端口
   ```
2. **环境变量覆盖**（优先级更高，k8s 常用）：`PORT=8080 HOST=0.0.0.0`

> **本地测试**：`pdm run dev`（或 `python app.py`）后访问 `http://127.0.0.1:5000/`，
> 管理员账号 `admin`，密码 `admin123`（config.ini `adminpass`）。非 root 会自动回退到 5000，
> root 则用 80（访问 `http://127.0.0.1/`）。

> **端口 80 与权限**：80 为特权端口，仅 root 可绑定。生产（k8s 虚拟机以 root 运行）直接用 80；
> 非 root 运行（如本地 `pdm run dev` 测试）会自动回退到 `serverportfallback`（默认 8080），
> 并在日志给出提示（可用 `PORT_FALLBACK` 环境变量或 `serverportfallback` 调整）。
> 手动放行防火墙时按实际生效端口放行。

> **⚠️ ERR_UNSAFE_PORT（浏览器打不开页面）**：部分端口被浏览器列入"不安全端口"，直接拒绝加载。
> 常见被拦截：`21 22 23 25 110 143 465 587 5060 6000 6665-6669 10080` 等。
> 若页面报 `ERR_UNSAFE_PORT (-312)`，请换用安全端口，如 **8080 / 8090 / 8000**（建议默认 8080）。

## 四、配置说明

### 1. 企业微信告警（内网关键配置）

`config.ini`（部署后位于程序所在目录的 `config/config.ini`）：

```ini
[Global]
# ① 内网企业微信网关/代理接口地址（程序发送 URL = {wechatapibase}?key={webhook key}）
#    用户只需填自己的外网 webhook key，发送地址由程序拼接。
#    无内网网关则保持官方地址即可。
wechatapibase = https://qyapi.weixin.qq.com/cgi-bin/webhook/send

# ② 用户自己的外网 webhook key
wechathookkey = <你的企业微信机器人 key>
```

也可在前端「系统设置」页填写：接口地址留空 = 用官方地址；key 留空 = 不修改。

### 2. 数据源
```ini
datasource = hybrid        # sina(最快) / hybrid(默认,新浪实时+后台合并tinysoft衍生指标) / tinysoft(慢,全指标)
sinarefreshinterval = 1    # 新浪实时行情刷新秒数
derivedinterval = 300      # hybrid 下 tinysoft 衍生指标合并间隔
pollinterval = 10          # tinysoft 数据源的轮询间隔
```

> **非交易时段行情**：程序每次成功抓取后把最新行情缓存到 `<程序目录>/market_cache.json`。
> 休市/重启后，实时行情页会展示**最近一次行情数据**并标注抓取时间（前端有"非交易时段"提示），
> 次日开盘自动恢复实时更新。

### 3. 交易时段与告警
```ini
marketopen = 091500        # 开盘时间 HHMMSS
marketclose = 150000       # 收盘时间（收盘后自动休眠，次日恢复）
lunchstart = 113000        # 午间休盘
lunchend = 130000
alertcooldown = 300        # 默认告警冷却（秒）
notifyrecovery = 1         # 数值恢复时是否发通知
enableheartbeat = 0        # 心跳看门狗
```

### 4. 监控规则
- 规则文件：`<程序目录>/config/rules.json`
- **推荐**：直接在浏览器前端「规则管理」页新增/编辑/删除（实时生效）
- 规则示例（IM2612−IM2609 期货价差，高于 −100 或低于 −260 告警）：
```json
{
  "name": "IM2612-IM2609 跨期价差",
  "metric": "期货价格",
  "contract_a": "IM2612",
  "contract_b": "IM2609",
  "min": -260,
  "max": -100,
  "alert_cooldown": 300,
  "enabled": true,
  "owner": "*"
}
```

### 5. 用户与权限（多用户隔离）

程序有登录界面。角色与隔离规则：

| 角色 | 权限 |
|---|---|
| **admin 管理员** | 查看/配置全部规则与行情、系统设置、用户管理 |
| **user 普通用户** | 只能看到归属自己的规则 + 共享规则（`owner="*"`），行情/状态/告警日志仅展示自己规则涉及的合约，用户间互相隔离 |

用户存储：`<程序目录>/config/users.json`。两种创建方式：
1. **操作人员直接改配置文件**（首次加载自动把明文密码转为哈希写回）：
   ```json
   { "users": [
     {"username": "张三", "password": "明文密码", "role": "user"}
   ] }
   ```
2. **管理员前端创建**：「用户管理」页新增用户 / 重置密码 / 删除。

初始管理员：首次运行且无管理员时，按 `config.ini` 的 `adminuser`/`adminpass` 自动创建。
- 本地/测试环境默认 **admin / admin123**（已写入 config.ini，登录后请修改）；
- 生产部署请修改 `adminpass` 后再首次启动；`adminpass` 留空则自动生成随机密码并打印到启动日志。
- 若之前运行过且已存在 `config/users.json`，其密码优先于 config.ini（忘记密码可在前端「用户管理 → 重置密码」，或删除 users.json 后重启）。

规则归属 `owner` 字段（管理员在规则编辑时可设）：`"*"` = 共享（所有人可见，仅管理员可编辑）；`"用户名"` = 仅该用户可见可编辑。

## 五、运维（无 systemd 环境）

| 操作 | 命令 |
|---|---|
| 后台启动 | `sudo <程序目录>/start.sh [端口]`（systemd 模式为 `systemctl start basis-monitor`） |
| 停止 | `sudo <程序目录>/stop.sh` |
| 重启 | `sudo <程序目录>/restart.sh` |
| 实时日志 | `tail -f <程序目录>/logs/console.log` 或 `journalctl -u basis-monitor -f`（systemd 模式） |
| 程序自身日志 | `tail -f <程序目录>/logs/<YYYYMMDD>.log` |
| 开机自启 | systemd 模式自动启用；进程模式可用 `crontab @reboot` 或 supervisor 自行接入 |

> `<程序目录>` 即解压后的 `basis-monitor-package` 目录；所有路径均在程序所在目录，
> 整个目录可整体移动/复制到任意位置，无需改动任何配置。

> 无 systemd 意味着进程不会自动拉起/随开机自启，请按自身环境接入
> supervisor / crontab @reboot / k8s 探针等（由你自行解决）。

## 六、更新升级

在开发机重新打包并上传：
```bash
bash scripts/make-package.sh          # 重新生成 tar.gz
# 上传后，内网机器执行（只替换二进制，config/rules/logs 保留在程序目录）：
tar -xzf basis-monitor-package.tar.gz
sudo install -m 0755 basis-monitor-package/basis-monitor <程序目录>/basis-monitor
sudo <程序目录>/stop.sh && sudo <程序目录>/start.sh
```
升级只替换二进制，程序目录下的 config/、rules.json、logs/ 均保持不变。

## 七、常见问题

**Q1. 前端页面打不开 / 其他机器无法访问服务**
- 本机 `curl http://127.0.0.1:<端口>/` 通、其他机器不通 → 防火墙未放行端口，
  执行 `sudo firewall-cmd --permanent --add-port=<端口>/tcp && sudo firewall-cmd --reload`；
- 确认程序进程在跑：`pgrep -f basis-monitor`，日志看 `logs/console.log`；
- 确认监听地址为 `0.0.0.0`（config.ini `serverhost`），否则只能本机访问。

**Q2. 前端显示"非交易时段"，无行情数据**
收盘后（>15:00）程序按设计休眠，次日 9:15 自动恢复；盘中 11:30–13:00 午休同理。
属正常现象，非故障。

**Q3. 行情长时间无数据 / 最近抓取不更新**
- 检查外网白名单是否放行 `hq.sinajs.cn`（hybrid/sina 模式核心依赖）；
- 查看 `logs/<日期>.log` 中的 `监控主循环异常` 报错；
- 确认 DNS 可解析上述域名。

**Q4. 企业微信收不到告警**
- 确认 `wechathookkey` 已配置、机器人已添加到目标群；
- 确认 `wechatapibase` 内网网关可达（程序发送 `{wechatapibase}?key={key}`）；
- 前端「系统设置 → 发送测试告警」直接验证链路。

**Q5. 登录界面打不开 / 登录失败**
- 首次启动日志中会打印初始管理员随机密码（若 config.ini `adminpass` 留空）；
- 也可在 `<程序目录>/config/users.json` 直接添加用户（明文密码自动转哈希）；
- 忘记密码：管理员在前端「用户管理 → 重置密码」，或删除 users.json 后重启（按 config 重新生成初始管理员）。

**Q6. 普通用户看不到某些规则/行情**
- 规则归属 `owner` 需等于该用户名，或为 `"*"`（共享）才可见；
- 行情页仅展示用户可见规则涉及的合约，属设计如此（用户隔离）。

**Q7. 规则提示"数据缺失"**
- 合约写法应为品种大写+年月，如 `IM2612`；中金所挂牌只有 当月/下月/下季月/隔季月，
  非挂牌合约（如 IM2601）无数据属正常；
- 指标需当前数据源支持：sina 仅 现货价格/期货价格/当前基差；
  矫正基差/隐含利率等请使用 hybrid 或 tinysoft 数据源。

**Q8. SELinux 拦截写入**
按「前置准备 → SELinux」一节放行数据目录。

## 八、备选部署方式（无需打包）

若内网机器已有 Python 3.11，也可直接拷贝源码 + venv：
```bash
# 开发机：导出依赖与源码
pdm export -f requirements --without-hashes > requirements.txt
# 内网机（源码模式同样默认使用程序所在目录的 config/ 与 logs/）：
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
nohup .venv/bin/python app.py &
```
（源码模式默认 config/logs 也在程序目录；如需指定其他数据目录，可用
`BASIS_MONITOR_DATA=<目录>` 环境变量覆盖。）

## 九、脚本说明

### 部署包内脚本（basis-monitor-package/）

| 脚本 | 作用 |
|---|---|
| `basis-monitor` | 主程序单文件二进制（内嵌 Python 3.11 + 全部依赖 + 前端） |
| `deploy.sh` | 安装脚本：初始化 config/logs、放行防火墙端口；自动检测 systemd/进程模式并安装（`--systemd` 强制 systemd，`--process` 强制进程模式） |
| `start.sh` | 启动程序。自动识别模式：systemd 用 `systemctl start`；进程模式用 `nohup` 后台启动（可加端口参数） |
| `stop.sh` | 停止程序。systemd 用 `systemctl stop`；进程模式按进程 pkill 停止 |
| `restart.sh` | 重启程序。systemd 用 `systemctl restart`；进程模式 stop + start（可加端口参数） |
| `config/config.ini` | 全局配置（webhook key / 端口 / 数据源 / 管理员等） |
| `config/rules.json` | 监控规则（含归属 owner，多用户隔离） |
| `DEPLOY.md` | 本文档 |

### 开发/打包脚本（仓库根目录 scripts/）

| 脚本 | 作用 |
|---|---|
| `scripts/build.sh` | 用独立构建环境（PyInstaller）生成单文件二进制 `dist/basis-monitor` |
| `scripts/make-package.sh` | 组装部署包 `dist/basis-monitor-package/` 与 `dist/basis-monitor-package.tar.gz`（二进制 + 配置 + 启停脚本 + 文档） |
| `scripts/smoke.sh` | 冒烟测试：临时程序目录启动打包二进制，验证启动/登录/权限/行情 |

## 十、源码仓库说明

- 运行时配置（可能含敏感信息）不入库，.gitignore 已排除：`config/config.ini`、`config/users.json`、`logs/`、`market_cache.json`、`dist/` 等
- 仓库提供安全模板 `config/config.ini.example`，克隆后首次运行：
  ```bash
  cp config/config.ini.example config/config.ini   # 按需填写 webhook key、端口、管理员密码
  pdm install                                       # 安装依赖
  pdm run dev                                       # 本地运行（非 root 自动回退 8080）
  ```
- `config/rules.json` 为规则初始示例（含共享规则）；如需自定义请自行修改