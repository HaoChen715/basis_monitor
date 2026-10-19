"""通用证券行情抓取器（股票 / ETF / 可转债等，新浪财经免费实时行情）。

用于「行情价格」类监控，按证券代码监控任意沪深证券的最新价，例如：
  - 可转债 ETF: 511380 → sh511380
  - 股票      : 600000 → sh600000 / 000001 → sz000001
  - 可转债    : 113050 → sh113050 / 123456 → sz123456
  - ETF / LOF : 510300 → sh510300 / 159915 → sz159915

新浪完整行情接口:
    https://hq.sinajs.cn/list=sh511380 (需 Referer 头, GBK 编码)
字段:
    0 名称  1 今开  2 昨收  3 最新价  4 最高  5 最低  6 竞买价  7 竞卖价
    8 成交量(股/张)  9 成交额(元)  ...  30 日期  31 时间  32 状态

与 basis 监控共用同一套规则/告警框架，因此本模块只负责「抓行情 + 取指标值」。
"""
import logging
import re

import requests

logger = logging.getLogger("basis_monitor")

QUOTE_URL = "https://hq.sinajs.cn/list="
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
}

# 行情价格类规则可选指标
PRICE_METRICS = [
    "最新价",
    "涨跌幅(%)",
    "涨跌额",
    "今开",
    "昨收",
    "最高",
    "最低",
    "成交额(万)",
]


def _infer_exchange(num: str) -> str:
    """仅凭 6 位代码推断交易所前缀（无显式前缀时的兜底）。"""
    if num[:2] == "11":      # 沪市可转债 110/113/118
        return "sh"
    if num[:2] == "12":      # 深市可转债 123/127/128
        return "sz"
    first = num[0]
    if first in "5689":      # 沪市股票/科创板/基金/ B 股
        return "sh"
    if first in "0123":      # 深市股票/基金/ B 股
        return "sz"
    return "sh"


def normalize_symbol(code) -> str:
    """把用户输入的证券代码归一化为新浪符号（sh/sz + 6 位数字）。

    支持：511380 / sh511380 / SH511380 / 511380.SH / 511380.XSHG 等写法。
    无法识别时返回空串。
    """
    if code is None:
        return ""
    c = str(code).strip().lower().replace(" ", "")
    if not c:
        return ""

    # 带交易所后缀：600000.sh / 600000.xshg
    if "." in c:
        num, _, suffix = c.partition(".")
        suffix = suffix.lower()
        if suffix in ("sh", "ss", "xshg") and re.fullmatch(r"\d{6}", num):
            return "sh" + num
        if suffix in ("sz", "xshe") and re.fullmatch(r"\d{6}", num):
            return "sz" + num
        c = num

    m = re.fullmatch(r"(sh|sz)(\d{6})", c)
    if m:
        return c
    if re.fullmatch(r"\d{6}", c):
        return _infer_exchange(c) + c
    return ""


class QuoteFetcher:
    def __init__(self, timeout=10):
        self.timeout = timeout

    # ---------------------------------------------------------------- 抓取
    def fetch(self, codes):
        """抓取一批证券代码，返回 {normalized_symbol: quote_dict}。

        quote_dict 含 code / name / price / prev_close / open / high / low /
        volume / amount / change / change_pct / date / time。
        """
        symbols = []
        for code in codes:
            sym = normalize_symbol(code)
            if sym and sym not in symbols:
                symbols.append(sym)
        if not symbols:
            return {}

        raw = self._get_batch(symbols)
        out = {}
        for sym in symbols:
            quote = self._parse(sym, raw.get(sym, ""))
            if quote:
                out[sym] = quote
        return out

    def _get_batch(self, symbols):
        url = QUOTE_URL + ",".join(symbols)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=self.timeout)
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="replace")
        except Exception as exc:
            logger.error("证券行情请求失败: %s", exc)
            return {}
        data = {}
        for m in re.finditer(r'var hq_str_([\w]+)="([^"]*)";', text):
            data[m.group(1)] = m.group(2)
        return data

    @staticmethod
    def _parse(symbol, raw):
        if not raw:
            return None
        fields = raw.split(",")
        if len(fields) < 4:
            return None

        def num(idx):
            try:
                return float(fields[idx])
            except (IndexError, ValueError):
                return None

        price = num(3)
        prev = num(2)
        name = fields[0].strip() or symbol.upper()
        if price is None:
            return None
        change = round(price - prev, 4) if prev not in (None, 0) else None
        change_pct = round((price - prev) / prev * 100, 4) if prev else None

        return {
            "code": symbol,
            "name": name,
            "price": price,
            "prev_close": prev,
            "open": num(1),
            "high": num(4),
            "low": num(5),
            "volume": num(8),
            "amount": num(9),
            "change": change,
            "change_pct": change_pct,
            "date": fields[30] if len(fields) > 30 else "",
            "time": fields[31] if len(fields) > 31 else "",
        }

    @staticmethod
    def metric_value(quote, metric_label):
        """从 quote_dict 中取某个指标的值，取不到返回 None。"""
        if not quote:
            return None
        mapping = {
            "最新价": "price",
            "涨跌幅(%)": "change_pct",
            "涨跌额": "change",
            "今开": "open",
            "昨收": "prev_close",
            "最高": "high",
            "最低": "low",
        }
        if metric_label == "成交额(万)":
            amount = quote.get("amount")
            return round(amount / 10000, 4) if amount is not None else None
        key = mapping.get(metric_label)
        if key is None:
            return None
        val = quote.get(key)
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
