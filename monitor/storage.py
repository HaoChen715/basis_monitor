"""规则存储 (config/rules.json) 与运行时状态。

规则由前端页面增删改查，程序后台监控线程读取规则计算并告警。
"""
import json
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime

from .config import program_dir

RULES_PATH = os.path.join(
    os.environ.get("BASIS_MONITOR_DATA") or program_dir(), "config", "rules.json"
)

METRIC_LABELS = [
    "现货价格",
    "期货价格",
    "当前基差",
    "基差占比(%)",
    "期内分红",
    "矫正基差",
    "隐含利率(%)",
    "利率(%)",
    "时间价值",
    "理论基差",
]

# 合约前缀 -> 品种 (用于从规则合约定位 tinysoft 数据区块)
VARIETIES = ["IM", "IF", "IC", "IH"]


class RulesStore:
    """规则配置的增删改查，线程安全，持久化到 rules.json。"""

    def __init__(self, path=RULES_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._rules = []
        self.load()

    def load(self):
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._rules = data.get("rules", [])
                except Exception:
                    self._rules = []
            else:
                self._rules = []
            for rule in self._rules:
                rule.setdefault("id", self._new_id())
                rule.setdefault("enabled", True)
                rule.setdefault("owner", "*")   # 历史规则默认共享
            self._save_locked()

    def _save_locked(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {"version": 1, "updated_at": datetime.now().isoformat(), "rules": self._rules}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @staticmethod
    def _new_id():
        return uuid.uuid4().hex[:12]

    def list(self):
        with self._lock:
            return json.loads(json.dumps(self._rules))

    def get(self, rule_id):
        with self._lock:
            for rule in self._rules:
                if rule["id"] == rule_id:
                    return json.loads(json.dumps(rule))
        return None

    def create(self, data: dict):
        rule = self._normalize(data)
        rule["id"] = self._new_id()
        rule["created_at"] = datetime.now().isoformat()
        rule["updated_at"] = rule["created_at"]
        with self._lock:
            self._rules.append(rule)
            self._save_locked()
        return json.loads(json.dumps(rule))

    def update(self, rule_id: str, data: dict):
        with self._lock:
            for rule in self._rules:
                if rule["id"] == rule_id:
                    merged = {**rule, **self._normalize(data)}
                    merged["id"] = rule_id
                    merged["updated_at"] = datetime.now().isoformat()
                    self._rules[self._rules.index(rule)] = merged
                    self._save_locked()
                    return json.loads(json.dumps(merged))
        return None

    def delete(self, rule_id: str):
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r["id"] != rule_id]
            if len(self._rules) != before:
                self._save_locked()
                return True
        return False

    def _normalize(self, data: dict) -> dict:
        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        contract_a = str(data.get("contract_a", "")).strip().upper()
        contract_b = str(data.get("contract_b", "")).strip().upper()
        metric = data.get("metric", "当前基差")
        if metric not in METRIC_LABELS:
            metric = "当前基差"

        rule = {
            "name": str(data.get("name", "")).strip() or f"{contract_a}-{contract_b}",
            "metric": metric,
            "contract_a": contract_a,
            "contract_b": contract_b,
            "min": _num(data.get("min")),
            "max": _num(data.get("max")),
            "alert_cooldown": _num(data.get("alert_cooldown")) or 0,
            "notify_recovery": bool(data.get("notify_recovery", True)),
            "enabled": bool(data.get("enabled", True)),
            "webhook_key": str(data.get("webhook_key", "")).strip(),
            "mentioned_list": data.get("mentioned_list") or ["@all"],
            "owner": str(data.get("owner", "*")).strip() or "*",
        }
        return rule


class RuntimeState:
    """后台监控线程的实时状态，供前端状态页查询。"""

    def __init__(self):
        self._lock = threading.RLock()
        self.engine_running = False
        self.engine_start_time = None
        self.last_fetch_time = None
        self.last_error = None
        self.market_open = False
        self.market_cached = False       # 当前行情是否来自最近一次缓存（非交易时段）
        self.market_data = {}            # contract -> {metric_label -> value}
        self.rule_values = {}            # rule_id -> {value, trigger, ...}
        self.alert_history = deque(maxlen=200)
        self.last_heartbeat = None

    def snapshot(self):
        with self._lock:
            return {
                "engine_running": self.engine_running,
                "engine_start_time": self.engine_start_time,
                "last_fetch_time": self.last_fetch_time,
                "last_error": self.last_error,
                "market_open": self.market_open,
                "market_cached": self.market_cached,
                "market_data": json.loads(json.dumps(self.market_data)),
                "rule_values": json.loads(json.dumps(self.rule_values)),
                "alert_history": list(self.alert_history),
                "last_heartbeat": self.last_heartbeat,
            }

    def set_market_data(self, market_data: dict, fetch_time: str = None):
        with self._lock:
            self.market_data = market_data
            self.last_fetch_time = fetch_time or time.strftime("%Y-%m-%d %H:%M:%S")
            self.last_error = None

    def set_market_cached(self, flag: bool = True):
        with self._lock:
            self.market_cached = bool(flag)

    def set_error(self, message: str):
        with self._lock:
            self.last_error = message

    def set_rule_value(self, rule_id: str, value: dict):
        with self._lock:
            self.rule_values[rule_id] = value

    def remove_rule_value(self, rule_id: str):
        with self._lock:
            self.rule_values.pop(rule_id, None)

    def set_market_state(self, open_flag: bool):
        with self._lock:
            self.market_open = open_flag

    def add_alert(self, entry: dict):
        with self._lock:
            entry = dict(entry)
            entry["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.alert_history.appendleft(entry)

    def set_heartbeat(self):
        with self._lock:
            self.last_heartbeat = time.strftime("%Y-%m-%d %H:%M:%S")