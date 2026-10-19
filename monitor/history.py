"""监控值历史采样（用于价格波动曲线图）。

规则每次计算出有效值后，按最小采样间隔记录一个数据点，持久化到
<数据目录>/history.json，供前端绘制「价格走势」曲线（含 MIN/MAX 阈值线）。

结构:
    {"version": 1, "rules": {"<rule_id>": [[epoch, value], ...], ...}}

为控制文件大小，每条规则最多保留 max_points 个采样点（默认 2000，先进先出），
且间隔小于 min_interval 秒的重复采样只就地更新最后一个点，保证曲线末端实时。
"""
import json
import logging
import os
import threading
import time

from .config import program_dir

logger = logging.getLogger("basis_monitor")

HISTORY_PATH = os.path.join(
    os.environ.get("BASIS_MONITOR_DATA") or program_dir(), "history.json"
)


class HistoryStore:
    def __init__(self, path=HISTORY_PATH, min_interval=30, max_points=2000,
                 flush_interval=5):
        self.path = path
        self.min_interval = max(int(min_interval or 0), 0)
        self.max_points = max(int(max_points or 0), 10)
        self.flush_interval = max(int(flush_interval or 0), 1)
        self._lock = threading.RLock()
        self._data = {}
        self._dirty = False
        self._last_flush = 0.0
        self.load()

    # ---------------------------------------------------------------- 持久化
    def load(self):
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                    raw = obj.get("rules", {})
                    self._data = {
                        rid: [[float(p[0]), float(p[1])] for p in pts if len(p) >= 2]
                        for rid, pts in raw.items()
                    }
                except Exception as exc:
                    logger.error("history.json 解析失败: %s", exc)
                    self._data = {}
            else:
                self._data = {}
            self._dirty = False

    def flush(self, force=False):
        with self._lock:
            if not self._dirty:
                return
            now = time.time()
            if not force and now - self._last_flush < self.flush_interval:
                return
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                payload = {"version": 1, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "rules": self._data}
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                os.replace(tmp, self.path)
                self._dirty = False
                self._last_flush = now
            except Exception as exc:
                logger.warning("历史记录保存失败: %s", exc)

    # ---------------------------------------------------------------- 记录/查询
    def configure(self, min_interval=None, max_points=None):
        with self._lock:
            if min_interval is not None:
                self.min_interval = max(int(min_interval), 0)
            if max_points is not None:
                self.max_points = max(int(max_points), 10)

    def record(self, rule_id, value, ts=None):
        if rule_id is None or value is None:
            return
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        now = float(ts if ts is not None else time.time())
        with self._lock:
            points = self._data.setdefault(rule_id, [])
            if points and now - points[-1][0] < self.min_interval:
                points[-1][1] = value          # 就地更新末端点，曲线保持实时
            else:
                points.append([round(now, 3), value])
                if len(points) > self.max_points:
                    del points[:len(points) - self.max_points]
            self._dirty = True

    def get(self, rule_id, limit=None):
        with self._lock:
            pts = [{"t": p[0], "v": p[1]} for p in self._data.get(rule_id, [])]
        if limit and limit > 0:
            pts = pts[-limit:]
        return pts

    def remove(self, rule_id):
        with self._lock:
            if rule_id in self._data:
                self._data.pop(rule_id, None)
                self._dirty = True
