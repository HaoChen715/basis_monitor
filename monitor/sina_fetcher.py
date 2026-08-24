"""新浪财经免费实时行情抓取器（备用/加速数据源）。

接口：
- 股指期货实时报价: https://hq.sinajs.cn/list=nf_IM2609 (需 Referer 头, GBK 编码)
  nf_ 前缀 = 国内期货, 字段 3 为最新价
- 标的指数实时点位: https://hq.sinajs.cn/list=s_sh000852 (s_ 前缀简略数据)
  字段 0 为名称, 字段 1 为当前点数

中金所股指期货每个品种挂牌 4 个合约：当月、下月、下季月、隔季月。
合约月份可由当前日期推导，无需额外查询。

相比 tinysoft 源（响应 10~60 秒且会限流），本接口通常亚秒级返回，
但仅提供 现货价格 / 期货价格 / 当前基差 三项原始指标（基差 = 期货 - 现货）。
矫正基差、隐含利率、理论基差等衍生指标仍依赖 tinysoft 源。
"""
import logging
import re
from datetime import datetime

import requests

logger = logging.getLogger("basis_monitor")

QUOTE_URL = "https://hq.sinajs.cn/list="
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
}

# 品种 -> 标的指数代码 (新浪 s_ 前缀简略数据)
VARIETY_INDEX = {
    "IH": "s_sh000016",   # 上证50
    "IF": "s_sh000300",   # 沪深300
    "IC": "s_sh000905",   # 中证500
    "IM": "s_sh000852",   # 中证1000
}

QUARTER_MONTHS = [3, 6, 9, 12]


def contract_months(now=None):
    """返回 [当月, 下月, 下季月, 隔季月] 的 YYMM 列表，匹配中金所挂牌规则。"""
    now = now or datetime.now()
    y, m = now.year, now.month

    def ym(year, month):
        return f"{year % 100:02d}{month:02d}"

    m1y, m1m = (y + 1, 1) if m == 12 else (y, m + 1)

    qm = next((q for q in QUARTER_MONTHS if q > m1m), QUARTER_MONTHS[0])
    qy = y if qm > m1m else y + 1
    if qm == 3:
        q2y, q2m = qy, 6
    elif qm == 6:
        q2y, q2m = qy, 9
    elif qm == 9:
        q2y, q2m = qy, 12
    else:
        q2y, q2m = qy + 1, 3
    return [ym(y, m), ym(m1y, m1m), ym(qy, qm), ym(q2y, q2m)]


def _decode_gbk(resp):
    return resp.content.decode("gbk", errors="replace")


class SinaFetcher:
    def __init__(self, timeout=10):
        self.timeout = timeout

    # ---------------------------------------------------------------- 抓取
    def fetch_all(self):
        """返回 {variety: {contract: {现货价格, 期货价格, 当前基差}}}，结构同 tinysoft。"""
        months = contract_months()
        symbols = []
        contracts = []          # variety -> [contract,...]
        by_symbol = {}
        for variety in VARIETY_INDEX:
            cs = [variety + m for m in months]
            contracts.append((variety, cs))
            for c in cs:
                sym = "nf_" + c
                symbols.append(sym)
                by_symbol[sym] = c

        futures_raw = self._get_batch(symbols)
        index_raw = self._get_batch(list(VARIETY_INDEX.values()))

        index_values = {}
        for variety, sym in VARIETY_INDEX.items():
            parsed = self._parse_index(index_raw.get(sym, ""))
            if parsed is not None:
                index_values[variety] = parsed

        result = {}
        for variety, cs in contracts:
            table = {}
            spot = index_values.get(variety)
            for c in cs:
                price = self._parse_future(futures_raw.get("nf_" + c, ""))
                if price is None:
                    continue
                row = {"期货价格": price}
                if spot is not None:
                    row["现货价格"] = spot
                    row["当前基差"] = round(price - spot, 4)
                table[c] = row
            if table:
                result[variety] = table
        return result

    def _get_batch(self, symbols):
        if not symbols:
            return {}
        url = QUOTE_URL + ",".join(symbols)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=self.timeout)
            resp.raise_for_status()
            text = _decode_gbk(resp)
        except Exception as exc:
            logger.error("新浪行情请求失败: %s", exc)
            return {}
        data = {}
        for m in re.finditer(r'var hq_str_([\w]+)="([^"]*)";', text):
            data[m.group(1)] = m.group(2)
        return data

    @staticmethod
    def _parse_future(raw):
        """解析期货行，返回最新价。空字符串/无数据返回 None。"""
        if not raw:
            return None
        fields = raw.split(",")
        if len(fields) < 4 or not fields[3]:
            return None
        try:
            return float(fields[3])
        except ValueError:
            return None

    @staticmethod
    def _parse_index(raw):
        """解析指数简略行，返回当前点数。"""
        if not raw:
            return None
        fields = raw.split(",")
        if len(fields) < 2:
            return None
        try:
            return float(fields[1])
        except ValueError:
            return None

    def get_contract_value(self, market_data, contract, metric_label):
        """从行情矩阵中取某个合约某个指标的值（兼容 nested 结构）。"""
        for table in market_data.values():
            row = table.get(contract)
            if row is not None:
                val = row.get(metric_label)
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return None
        return None