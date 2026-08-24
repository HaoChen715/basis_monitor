"""后台监控主线程。

按配置的数据源定时拉取行情 -> 逐条计算规则 -> 触发条件满足时发送企业微信告警。
数据源：
  - tinysoft: 慢（10~60s/次），但含矫正基差/隐含利率/理论基差等衍生指标
  - sina    : 新浪免费实时行情（亚秒级），仅含 期货价格/现货价格/当前基差
  - hybrid  : sina 为主（实时），后台线程定时合并 tinysoft 的衍生指标
支持交易时段判断、午间休眠、收盘休眠、心跳看门狗与告警冷却去重。
"""
import json
import logging
import os
import threading
import time
from datetime import datetime

from .alerts import WeChatAlarm
from .config import DATA_DIR
from .fetcher import TinySoftFetcher
from .sina_fetcher import SinaFetcher
from .storage import RulesStore, RuntimeState

logger = logging.getLogger("basis_monitor")

MARKET_CACHE_PATH = os.path.join(DATA_DIR, "market_cache.json")


def merge_market_data(base, overlay):
    """把 overlay(nested) 的指标合并进 base(nested)。

    已存在的指标保留 base 的新值（如 sina 的实时价），只补充缺失的衍生指标。
    返回新 dict，不修改入参。
    """
    out = {v: {c: dict(row) for c, row in t.items()} for v, t in base.items()}
    for variety, table in overlay.items():
        for contract, row in table.items():
            if variety in out and contract in out[variety]:
                for k, v in row.items():
                    out[variety][contract].setdefault(k, v)
            else:
                out.setdefault(variety, {})[contract] = dict(row)
    return out


def _hhmmss(dt=None):
    return int((dt or datetime.now()).strftime("%H%M%S"))


class MonitorEngine:
    def __init__(self, config, rules_store: RulesStore, state: RuntimeState,
                 fetcher: TinySoftFetcher, alarm: WeChatAlarm, sina_fetcher: SinaFetcher = None):
        self.config = config
        self.rules = rules_store
        self.state = state
        self.fetcher = fetcher
        self.alarm = alarm
        self.sina_fetcher = sina_fetcher or SinaFetcher()
        self._stop_flag = threading.Event()
        self._thread = None
        self._derived_thread = None
        self._prev_trigger = {}   # rule_id -> direction 或 None
        self._last_was_open = False
        self._tinysoft_cache = {}  # 最近一次 tinysoft 衍生指标缓存（hybrid 用）

    def start(self):
        if not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run, name="monitor-engine", daemon=True)
            self._thread.start()
        if self._source() == "hybrid" and not (self._derived_thread and self._derived_thread.is_alive()):
            self._derived_thread = threading.Thread(
                target=self._derived_loop, name="monitor-derived", daemon=True
            )
            self._derived_thread.start()

    def stop(self):
        self._stop_flag.set()

    def _source(self):
        return str(self.config.get("datasource", "sina")).strip().lower()

    # ------------------------------------------------------------- 交易时段
    def _is_trading_time(self, now: datetime):
        if now.weekday() >= 5:  # 周末
            return False
        t = _hhmmss(now)
        open_t = int(self.config.get("marketopen", "091500"))
        close_t = int(self.config.get("marketclose", "150000"))
        lunch_start = int(self.config.get("lunchstart", "113000"))
        lunch_end = int(self.config.get("lunchend", "130000"))
        if t < open_t or t > close_t:
            return False
        if lunch_start <= t < lunch_end:
            return False
        return True

    # ------------------------------------------------------------- 主循环
    def _run(self):
        self.state.engine_running = True
        self.state.engine_start_time = time.strftime("%Y-%m-%d %H:%M:%S")
        self.state.set_heartbeat()
        logger.info("监控引擎启动")

        # 非交易时段（如重启后休市）先加载最近一次行情缓存，供实时行情页展示
        if not self.state.snapshot().get("market_data"):
            self._load_market_cache()

        heartbeat_interval = self.config.get_int("heartbeatinterval", 3600)
        last_heartbeat = time.time()
        startup_sent = False
        backoff = 0  # 抓取失败时的指数退避（秒），避免持续冲击数据源

        while not self._stop_flag.is_set():
            now = datetime.now()
            try:
                if not self._is_trading_time(now):
                    self.state.set_market_state(False)
                    if self._was_open:
                        logger.info("进入非交易时段，监控休眠")
                    self._was_open = False
                    time.sleep(60)
                    continue

                self._was_open = True
                self.state.set_market_state(True)
                if not startup_sent:
                    startup_sent = True
                    self.alarm.send_startup()
                    logger.info("开盘，开始监控")

                data = self._fetch_data()
                self.state.set_market_data(data)
                self.state.set_market_cached(False)
                self._persist_market_cache(data)
                self._evaluate_rules(data)
                backoff = 0

                if self.config.get_bool("enableheartbeat", False):
                    if heartbeat_interval and heartbeat_interval > 0 and \
                            time.time() - last_heartbeat >= heartbeat_interval:
                        self.alarm.send_heartbeat()
                        self.state.set_heartbeat()
                        last_heartbeat = time.time()
            except Exception as exc:
                self.state.set_error(str(exc))
                logger.error("监控主循环异常: %s", exc, exc_info=True)
                backoff = 15 if not backoff else min(backoff * 2, 120)
            finally:
                if self._stop_flag.is_set():
                    break
                if backoff:
                    wait = backoff
                elif self._source() in ("sina", "hybrid"):
                    wait = max(self.config.get_int("sinarefreshinterval", 1), 1)
                else:
                    wait = max(self.config.get_int("pollinterval", 10), 1)
                time.sleep(wait)

        self.state.engine_running = False
        logger.info("监控引擎已停止")

    # ------------------------------------------------------------- 数据源
    def _fetch_data(self):
        """按配置的数据源抓取行情，返回 {variety: {contract: {metric: value}}}。"""
        source = self._source()
        if source == "sina":
            return self.sina_fetcher.fetch_all()
        if source == "hybrid":
            data = self.sina_fetcher.fetch_all()
            if self._tinysoft_cache:
                data = merge_market_data(data, self._tinysoft_cache)
            return data
        # tinysoft
        return self.fetcher.fetch_all()

    def _derived_loop(self):
        """hybrid 模式后台线程：定时从 tinysoft 抓取衍生指标并合并。"""
        interval = max(self.config.get_int("derivedinterval", 300), 60)
        last = 0.0
        while not self._stop_flag.is_set():
            if self._is_trading_time(datetime.now()):
                try:
                    derived = self.fetcher.fetch_all()
                    self._tinysoft_cache = derived
                    latest = self.state.snapshot().get("market_data") or {}
                    merged = merge_market_data(latest, derived)
                    self.state.set_market_data(merged)
                    self._evaluate_rules(merged)
                    last = time.time()
                    logger.info("tinysoft 衍生指标已合并（矫正基差/隐含利率/理论基差等）")
                except Exception as exc:
                    logger.error("tinysoft 衍生指标刷新失败: %s", exc, exc_info=True)
            if self._stop_flag.is_set():
                break
            time.sleep(interval)

    # ------------------------------------------------------------- 行情缓存（非交易时段展示最近数据）
    def _persist_market_cache(self, market_data):
        """每次成功抓取后把最新行情落盘，供休市/重启后展示。"""
        try:
            os.makedirs(os.path.dirname(MARKET_CACHE_PATH), exist_ok=True)
            tmp = MARKET_CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "data": market_data},
                    f, ensure_ascii=False,
                )
            os.replace(tmp, MARKET_CACHE_PATH)
        except Exception as exc:
            logger.warning("行情缓存保存失败: %s", exc)

    def _load_market_cache(self):
        """启动时若在非交易时段，加载最近一次行情缓存供展示。"""
        try:
            with open(MARKET_CACHE_PATH, encoding="utf-8") as f:
                obj = json.load(f)
            data = obj.get("data")
            if not data:
                return
            self.state.set_market_data(data, fetch_time=obj.get("time"))
            self.state.set_market_cached(True)
            logger.info("已加载最近一次行情缓存（时间 %s），非交易时段展示", obj.get("time"))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("行情缓存加载失败: %s", exc)

    @property
    def _was_open(self):
        return self._last_was_open

    @_was_open.setter
    def _was_open(self, value):
        self._last_was_open = value

    # ------------------------------------------------------------- 规则计算
    def _evaluate_rules(self, market_data):
        rules = self.rules.list()
        for rule in rules:
            if not rule.get("enabled"):
                self.state.remove_rule_value(rule["id"])
                continue
            try:
                self._evaluate_one(rule, market_data)
            except Exception as exc:
                logger.error("规则[%s]计算异常: %s", rule.get("name"), exc)
                self.state.set_rule_value(rule["id"], {
                    "name": rule.get("name"),
                    "value": None,
                    "trigger": "error",
                    "detail": str(exc),
                    "last_alert": None,
                })

    def _evaluate_one(self, rule, market_data):
        metric = rule["metric"]
        contract_a = rule["contract_a"]
        contract_b = rule.get("contract_b") or ""

        val_a = self.fetcher.get_contract_value(market_data, contract_a, metric)
        val_b = self.fetcher.get_contract_value(market_data, contract_b, metric) if contract_b else None

        if val_a is None or (contract_b and val_b is None):
            detail = f"未获取到 {contract_a}" if val_a is None else f"未获取到 {contract_b}"
            logger.warning("规则[%s] 数据缺失: %s", rule.get("name"), detail)
            self.state.set_rule_value(rule["id"], {
                "name": rule.get("name"),
                "value": None,
                "trigger": "missing",
                "detail": detail,
                "last_alert": None,
            })
            self._prev_trigger[rule["id"]] = None
            return

        value = round(val_a - val_b, 4) if (contract_b and val_b is not None) else round(val_a, 4)
        min_th = rule.get("min")
        max_th = rule.get("max")

        direction = None
        detail = ""
        if max_th is not None and value > max_th:
            direction = "above"
            detail = f"当前值 {value} 高于上限 {max_th}"
        elif min_th is not None and value < min_th:
            direction = "below"
            detail = f"当前值 {value} 低于下限 {min_th}"

        last_alert = self._prev_trigger.get(rule["id"])

        if direction:
            result = self.alarm.alarm(rule, direction, value, detail)
            self.state.add_alert({
                "rule_id": rule["id"],
                "rule_name": rule.get("name"),
                "type": direction,
                "value": value,
                "detail": detail,
                "sent": bool(result),
            })
            logger.warning("规则[%s] 触发%s告警, %s", rule.get("name"), direction, detail)
            self.state.set_rule_value(rule["id"], {
                "name": rule.get("name"),
                "value": value,
                "trigger": direction,
                "detail": detail,
                "last_alert": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            self._prev_trigger[rule["id"]] = direction
        else:
            if last_alert and rule.get("notify_recovery", True):
                self.alarm.send(
                    webhook_key=rule.get("webhook_key"),
                    content=(
                        f"<font color=\"info\">【基差/跨期价差监控-恢复】</font>\n"
                        f">监控项:{rule.get('name')}\n"
                        f">当前值:{value}，已回到正常区间\n"
                        f">播报时间:{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    ),
                    mentioned_list=rule.get("mentioned_list") or ["@all"],
                )
                self.state.add_alert({
                    "rule_id": rule["id"],
                    "rule_name": rule.get("name"),
                    "type": "recovery",
                    "value": value,
                    "detail": "已回到正常区间",
                    "sent": True,
                })
                logger.info("规则[%s] 已恢复, 当前值 %s", rule.get("name"), value)
            self.state.set_rule_value(rule["id"], {
                "name": rule.get("name"),
                "value": value,
                "trigger": "ok",
                "detail": "",
                "last_alert": last_alert,
            })
            self._prev_trigger[rule["id"]] = None

    # ------------------------------------------------------------- 手动检测
    def check_now(self, rule_id):
        """立即用最新行情重新计算单条规则，返回结果（不主动告警）。"""
        data = self.state.snapshot().get("market_data") or {}
        rule = self.rules.get(rule_id)
        if not rule:
            return {"error": "规则不存在"}
        # 在内存副本上模拟一次计算
        from .storage import RuntimeState
        return self._simulate(rule, data)

    def _simulate(self, rule, market_data):
        metric = rule["metric"]
        contract_a = rule["contract_a"]
        contract_b = rule.get("contract_b") or ""
        val_a = self.fetcher.get_contract_value(market_data, contract_a, metric)
        val_b = self.fetcher.get_contract_value(market_data, contract_b, metric) if contract_b else None
        if val_a is None or (contract_b and val_b is None):
            return {"name": rule.get("name"), "value": None, "trigger": "missing",
                    "detail": "数据缺失（合约未在行情中出现或盘前无数据）"}
        value = round(val_a - val_b, 4) if (contract_b and val_b is not None) else round(val_a, 4)
        trigger = "ok"
        detail = ""
        if rule.get("max") is not None and value > rule["max"]:
            trigger = "above"
            detail = f"高于上限 {rule['max']}"
        elif rule.get("min") is not None and value < rule["min"]:
            trigger = "below"
            detail = f"低于下限 {rule['min']}"
        return {"name": rule.get("name"), "value": value, "trigger": trigger, "detail": detail}