"""TinySoft 数据抓取器。

流程：
1. GET https://web.tinysoft.com.cn/website/index.tsl?PageID=27433
   解析页面中的 g_randomid 以及 npmodelconf（np / modename 会话令牌，
   modename 经 base64 解码后形如 "jicha_refresh_im_28B9FE44"，可用于识别品种）。
2. POST loadContentDataAjax.tsl 一次性提交全部区块的 np / modename，
   返回各品种 (IH/IF/IC/IM) 的行情矩阵（现货价格、期货价格、当前基差等）。

会话令牌会过期，因此每隔 token_refresh_interval 秒重新拉取一次页面。
"""
import base64
import json
import logging
import re
import threading
import time

import requests

INDEX_URL = "https://web.tinysoft.com.cn/website/index.tsl?PageID=27433"
AJAX_URL = "https://web.tinysoft.com.cn/website/loadContentDataAjax.tsl?ref=js"

PAGE_ID = "27433"
WEBSITE_ID = "20906"
CREATOR = "cjzq"
QUERY_PARAMS = {
    "指定利率(%)": 2.25,
    "是否用历史隐含利率进行估计": "1",
    "使用过去多少天的隐含利率": 1,
}

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://web.tinysoft.com.cn",
    "Referer": "https://web.tinysoft.com.cn/website/index.tsl?PageID=27433",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

logger = logging.getLogger("basis_monitor")


def _b64decode_text(text: str) -> str:
    """base64 解码并自动补齐 padding。"""
    text = text.strip()
    text += "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.b64decode(text).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_balanced_object(text: str, start: int) -> str:
    """从 text[start] == '{' 开始做括号配平，返回完整 JSON 对象字符串。"""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    raise ValueError("无法配平 JSON 对象括号")


class TinySoftFetcher:
    def __init__(self, token_refresh_interval=600, timeout=60):
        self.token_refresh_interval = max(token_refresh_interval, 30)
        self.timeout = timeout
        self._lock = threading.RLock()
        self._g_randomid = None
        self._sections = {}          # variety -> {np, modename}
        self._token_ts = 0.0
        self.last_token_error = None

    # ------------------------------------------------------------------ 会话令牌
    def _ensure_tokens(self):
        now = time.time()
        if self._g_randomid and (now - self._token_ts) < self.token_refresh_interval:
            return
        with self._lock:
            now = time.time()
            if self._g_randomid and (now - self._token_ts) < self.token_refresh_interval:
                return
            self._refresh_tokens()

    def _refresh_tokens(self):
        try:
            resp = requests.get(INDEX_URL, headers=HEADERS, timeout=self.timeout)
            resp.raise_for_status()
            # 页面为 GBK 编码且响应头未声明 charset，需手动解码
            html = resp.content.decode("gbk", errors="replace")

            m = re.search(r'g_randomid\s*=\s*"([^"]+)"', html)
            if not m:
                raise ValueError("页面中未找到 g_randomid")
            g_randomid = m.group(1)

            idx = html.find("npmodelconf")
            if idx < 0:
                raise ValueError("页面中未找到 npmodelconf")
            brace = html.find("{", idx)
            conf_raw = _extract_balanced_object(html, brace)
            conf = json.loads(conf_raw)

            sections = {}
            for np_key, modename in conf.items():
                decoded = _b64decode_text(modename)
                parts = decoded.split("_")
                variety = parts[-2].upper() if len(parts) >= 2 else ""
                if not variety or not np_key:
                    continue
                sections[variety] = {"np": np_key, "modename": modename}

            if not sections:
                raise ValueError("npmodelconf 解析为空")

            self._g_randomid = g_randomid
            self._sections = sections
            self._token_ts = time.time()
            self.last_token_error = None
            logger.info(
                "tinysoft 会话令牌刷新成功 varieties=%s",
                ",".join(sorted(sections.keys())),
            )
        except Exception as exc:
            self.last_token_error = str(exc)
            logger.error("tinysoft 会话令牌刷新失败: %s", exc)
            raise

    # ------------------------------------------------------------------ 数据抓取
    def fetch_all(self):
        """返回 {variety: {contract: {metric_label: value}}}"""
        self._ensure_tokens()
        with self._lock:
            g_randomid = self._g_randomid
            sections = dict(self._sections)
        if not sections:
            raise RuntimeError("无有效会话令牌")

        np_list = [sec["np"] for sec in sections.values()]
        modenames = [sec["modename"] for sec in sections.values()]

        form_data = {
            "params": json.dumps(QUERY_PARAMS, ensure_ascii=False),
            "PageID": PAGE_ID,
            "websiteID": WEBSITE_ID,
            "ContentID": "Content",
            "UserID": "",
            "menup": "0",
            "_cb": "",
            "_cbdata": "",
            "_cbExec": "1",
            "_cbDispType": "1",
            "__pageState": "0",
            "__globalUrlParam": json.dumps({"PageID": PAGE_ID, "pageid": PAGE_ID}),
            "g_randomid": json.dumps(g_randomid),
            "np": json.dumps(np_list),
            "modename": json.dumps(modenames),
            "creator": json.dumps(CREATOR),
        }

        resp = requests.post(AJAX_URL, data=form_data, headers=HEADERS, timeout=self.timeout)
        resp.raise_for_status()
        try:
            payload = json.loads(resp.content.decode("gbk", errors="replace"))
        except json.JSONDecodeError:
            raise ValueError("tinysoft 返回内容并非 JSON: " + resp.text[:300])

        return self._parse_content(payload)

    def _parse_content(self, payload):
        with self._lock:
            variety_by_np = {sec["np"]: var for var, sec in self._sections.items()}

        result = {}
        for section in payload.get("content", []):
            np_key = section.get("np", "")
            html = section.get("html", "")
            if not html:
                continue
            m = re.search(r'\.tsGrid\("grid-init",', html)
            if not m:
                continue
            brace = html.find("{", m.start())
            try:
                grid = json.loads(_extract_balanced_object(html, brace))
            except Exception as exc:
                logger.error("grid-init 解析失败 np=%s: %s", np_key, exc)
                continue

            fprue = grid.get("FPrueData", [])
            if not fprue:
                continue

            contracts = [k for k in fprue[0].keys() if k not in ("rowIndex", "", "分组")]
            table = {}
            for row in fprue:
                label = row.get("", "")
                if not label:
                    continue
                for contract in contracts:
                    table.setdefault(contract, {})[label] = row.get(contract)

            variety = variety_by_np.get(np_key)
            if not variety:
                # 兜底：根据合约前缀推断品种
                prefixes = sorted({c[:2] for c in contracts if c[:2] in ("IH", "IF", "IC", "IM")})
                variety = prefixes[0] if prefixes else "UNKNOWN"
            result[variety] = table

        if not result:
            raise ValueError("未能解析出任何行情数据")
        return result

    def get_contract_value(self, market_data, contract, metric_label):
        """从行情矩阵中取某个合约某个指标的值。找不到返回 None。"""
        for table in market_data.values():
            row = table.get(contract)
            if row is not None:
                val = row.get(metric_label)
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return None
        return None