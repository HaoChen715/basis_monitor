"""企业微信机器人告警发送。

单条规则可配置独立 webhook key（webhook_key 非空则优先使用），
否则回退到全局 wechathookkey。告警按冷却时间去重，避免刷屏。

内网部署：可配置企业微信接口地址（wechatapibase），程序最终发送地址为
    {wechatapibase}?key={用户外网 webhook key}
即把内网网关/代理地址与用户自己的外网 webhook key 拼接。默认使用官方地址
https://qyapi.weixin.qq.com/cgi-bin/webhook/send 。
"""
import json
import logging
import time

import requests

DEFAULT_WEBHOOK_BASE = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"

logger = logging.getLogger("basis_monitor")


class WeChatAlarm:
    def __init__(self, global_key_provider=None, base_url_provider=None):
        # global_key_provider: callable 返回全局 webhook key
        # base_url_provider:   callable 返回企业微信接口地址（内网代理时配置）
        self.global_key_provider = global_key_provider
        self.base_url_provider = base_url_provider
        self._last_alert = {}   # key: (rule_id, direction) -> timestamp
        self._lock = None       # 由 app 注入

    def _resolve_key(self, webhook_key=None):
        key = (webhook_key or "").strip()
        if not key and self.global_key_provider:
            key = (self.global_key_provider() or "").strip()
        return key

    def _resolve_base(self):
        if self.base_url_provider:
            base = (self.base_url_provider() or "").strip().rstrip("/")
            if base:
                return base
        return DEFAULT_WEBHOOK_BASE

    def send(self, webhook_key, content, mentioned_list=None):
        key = self._resolve_key(webhook_key)
        if not key:
            logger.warning("未配置企业微信 webhook key，无法发送告警")
            return {"errcode": -1, "errmsg": "webhook key 未配置"}
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        mentioned = mentioned_list or ["@all"]
        if mentioned:
            payload["markdown"]["mentioned_list"] = mentioned
        url = f"{self._resolve_base()}?key={key}"
        try:
            resp = requests.post(url, json=payload, timeout=10)
            try:
                data = resp.json()
            except ValueError:
                data = None
            if not isinstance(data, dict):
                # 网关/接口返回了非 JSON 对象（字符串、空内容等），统一按失败处理
                logger.error(
                    "企业微信告警响应异常: %r (url=%s)", resp.text[:200], url
                )
                return {
                    "errcode": -1,
                    "errmsg": f"接口返回异常: {resp.text[:200]}",
                }
            if data.get("errcode", 0) != 0:
                logger.error("企业微信告警发送失败: %s (url=%s)", data, url)
            return data
        except Exception as exc:
            logger.error("企业微信告警发送异常: %s", exc)
            return {"errcode": -1, "errmsg": str(exc)}

    def _cooldown_passed(self, rule_id, direction, cooldown):
        """direction: 'above' | 'below' | 'recovery' | 'heartbeat' | 'startup'"""
        if cooldown and cooldown > 0:
            key = (rule_id, direction)
            now = time.time()
            last = self._last_alert.get(key, 0.0)
            if now - last < cooldown:
                return False
            self._last_alert[key] = now
        return True

    def alarm(self, rule, direction, value, message):
        cooldown = float(rule.get("alert_cooldown") or 0)
        if cooldown and cooldown > 0 and not self._cooldown_passed(rule["id"], direction, cooldown):
            logger.info("规则[%s] %s 触发但在冷却期内，跳过告警", rule.get("name"), direction)
            return None

        alarm_time = time.strftime("%Y-%m-%d %H:%M:%S")
        if direction == "above":
            direction_text = "高于上限 (>" + str(rule.get("max")) + ")"
            color = "warning"
        elif direction == "below":
            direction_text = "低于下限 (<" + str(rule.get("min")) + ")"
            color = "warning"
        else:
            direction_text = direction
            color = "info"

        content = (
            f"<font color=\"warning\">【基差/跨期价差监控】</font>\n"
            f">监控项:<font color=\"comment\">{rule.get('name')}</font>\n"
            f">指标:{rule.get('metric')}  {rule.get('contract_a')} - {rule.get('contract_b', '')}\n"
            f">触发:{direction_text}\n"
            f">当前值:<font color=\"{color}\">{value}</font>\n"
            f">播报时间:{alarm_time}\n"
        )
        if message:
            content += f">备注:{message}\n"

        result = self.send(
            webhook_key=rule.get("webhook_key"),
            content=content,
            mentioned_list=rule.get("mentioned_list") or ["@all"],
        )
        return result

    def send_test(self, webhook_key=None, mentioned_list=None):
        content = (
            "<font color=\"info\">【基差/跨期价差监控】</font>\n"
            f">这是一条测试告警，说明企业微信机器人配置正常\n"
            f">播报时间:{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        return self.send(webhook_key, content, mentioned_list)

    def send_startup(self, webhook_key=None):
        return self.send(
            webhook_key,
            "<font color=\"info\">【基差/跨期价差监控】监控程序已启动</font>",
            [],
        )

    def send_shutdown(self, webhook_key=None):
        return self.send(
            webhook_key,
            "<font color=\"warning\">【基差/跨期价差监控】监控程序已停止</font>",
            [],
        )

    def send_heartbeat(self, webhook_key=None):
        return self.send(
            webhook_key,
            f"<font color=\"info\">【看门狗】监控程序运行正常，播报时间:{time.strftime('%Y-%m-%d %H:%M:%S')}</font>",
            [],
        )