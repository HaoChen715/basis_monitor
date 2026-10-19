"""全局配置加载与保存 (config/config.ini)。

监控程序所有全局参数都从这里读取，前端设置页面修改后写回。
"""
import configparser
import logging
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def program_dir():
    """程序所在目录：打包二进制 = 二进制文件所在目录；源码模式 = 项目根目录。

    所有运行路径（config/、logs/、rules.json）均基于该目录，随程序目录迁移。
    仍可用环境变量 BASIS_MONITOR_DATA 覆盖（优先级更高）。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return BASE_DIR


DATA_DIR = os.environ.get("BASIS_MONITOR_DATA") or program_dir()
CONFIG_DIR = os.path.join(DATA_DIR, "config")
CONFIG_INI = os.path.join(CONFIG_DIR, "config.ini")

DEFAULTS = {
    "wechathookkey": "",
    "wechatapibase": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send",
    "pollinterval": "10",
    "sinarefreshinterval": "1",
    "tokenrefreshinterval": "600",
    "alertcooldown": "300",
    "notifyrecovery": "1",
    "enableheartbeat": "0",
    "heartbeatinterval": "3600",
    "marketopen": "091500",
    "marketclose": "150000",
    "lunchstart": "113000",
    "lunchend": "130000",
    "datasource": "hybrid",
    "derivedinterval": "300",
    "historyinterval": "30",
    "historymaxpoints": "2000",
    "serverhost": "0.0.0.0",
    "serverport": "80",
    "serverportfallback": "5000",
    "secretkey": "",
    "adminuser": "admin",
    "adminpass": "",
}

logger = logging.getLogger("basis_monitor")


class GlobalConfig:
    """全局配置，支持前端在线修改并持久化到 config.ini。"""

    def __init__(self, path=CONFIG_INI):
        self.path = path
        self._values = dict(DEFAULTS)
        self._lock = None  # 由 app 注入，保证跨线程安全
        self.reload()

    def set_lock(self, lock):
        self._lock = lock

    def reload(self):
        parser = configparser.ConfigParser()
        parser.read(self.path, encoding="utf-8")
        for key in DEFAULTS:
            try:
                self._values[key] = parser.get("Global", key)
            except (configparser.NoSectionError, configparser.NoOptionError):
                self._values[key] = DEFAULTS[key]

    def save(self):
        parser = configparser.ConfigParser()
        parser.add_section("Global")
        for key, value in self._values.items():
            parser.set("Global", key, str(value))
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            parser.write(f)

    # ---- getters ----
    def get(self, key, default=None):
        return self._values.get(key.lower(), default)

    def get_int(self, key, default=None):
        try:
            return int(float(self._values.get(key.lower(), default)))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key, default=False):
        return str(self._values.get(key.lower(), default)).strip().lower() in ("1", "true", "yes", "on")

    def get_webhook_key(self):
        return self._values.get("wechathookkey", "").strip()

    # ---- updater ----
    def update(self, updates: dict):
        """仅更新白名单内的字段。webhook key 传空串表示不修改。"""
        allowed = set(DEFAULTS.keys())
        changed = {}
        for key, value in updates.items():
            key = key.lower()
            if key not in allowed:
                continue
            if key in ("wechathookkey", "wechatapibase") and not str(value).strip():
                continue
            self._values[key] = str(value).strip()
            changed[key] = self._values[key]
        if changed:
            self.save()
        return changed

    def as_dict(self):
        """返回给前端的配置（敏感字段脱敏/隐藏）。"""
        data = {k: v for k, v in self._values.items()}
        # 敏感字段：会话签名密钥、初始管理员密码
        for sensitive in ("secretkey", "adminpass"):
            if data.get(sensitive):
                data[sensitive] = "***"
                data[sensitive + "_set"] = True
            else:
                data[sensitive] = ""
                data[sensitive + "_set"] = False
        key = data.get("wechathookkey", "")
        if key:
            data["wechathookkey"] = key[:4] + "****" + key[-4:]
            data["wechathookkey_set"] = True
        else:
            data["wechathookkey_set"] = False
        return data