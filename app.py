"""基差 / 跨期价差监控系统入口。

启动方式：
    .venv/bin/python app.py
启动后浏览器访问 http://<host>:<port>/ 打开配置前端（需登录）。

多用户权限：
  - admin 管理员：可查看/配置全部规则、全部行情、系统设置、用户管理
  - user  普通用户：只能看到归属自己的规则（owner=自己）及共享规则（owner="*"），
    互相隔离；行情/状态/告警日志仅展示自己可见规则涉及的合约
"""
import logging
import os
import secrets
import sys
import threading
import time
from functools import wraps

from flask import Flask, jsonify, render_template, request, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from monitor.alerts import WeChatAlarm
from monitor.auth import OWNER_SHARED, ROLE_ADMIN, UserStore
from monitor.config import GlobalConfig, program_dir
from monitor.engine import MonitorEngine
from monitor.fetcher import TinySoftFetcher
from monitor.sina_fetcher import SinaFetcher
from monitor.storage import METRIC_LABELS, RulesStore, RuntimeState, VARIETIES

# ---------------------------------------------------------------- 日志
# 所有运行路径（logs/、config/）均在程序所在目录；可用 BASIS_MONITOR_DATA 覆盖
DATA_DIR = os.environ.get("BASIS_MONITOR_DATA") or program_dir()
LOG_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOGGER_FORMAT = "%(asctime)s %(name)s %(levelname)s  %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S %a"
logging.basicConfig(
    level=logging.INFO,
    format=LOGGER_FORMAT,
    datefmt=DATE_FORMAT,
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, time.strftime("%Y%m%d") + ".log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("basis_monitor")

# ---------------------------------------------------------------- 全局单例
_config = GlobalConfig()
# 会话签名密钥：自动生成并持久化
if not _config.get("secretkey"):
    _config.update({"secretkey": secrets.token_hex(32)})
_users = UserStore(
    os.path.join(DATA_DIR, "config", "users.json"),
    bootstrap_user=_config.get("adminuser", "admin"),
    bootstrap_pass=_config.get("adminpass", ""),
)
_rules = RulesStore()
_state = RuntimeState()
_fetcher = TinySoftFetcher(token_refresh_interval=_config.get_int("tokenrefreshinterval", 600))
_sina_fetcher = SinaFetcher()
_alarm = WeChatAlarm(
    global_key_provider=lambda: _config.get_webhook_key(),
    base_url_provider=lambda: _config.get("wechatapibase") or "",
)
_engine = MonitorEngine(_config, _rules, _state, _fetcher, _alarm, sina_fetcher=_sina_fetcher)

# 注入线程锁，保证配置/告警跨线程安全
_state_lock = threading.RLock()
_config.set_lock(_state_lock)
_alarm._lock = _state_lock


def create_app():
    app = Flask(__name__)
    app.secret_key = _config.get("secretkey") or "insecure-dev-key"
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    # ------------------------------------------------------------ 认证辅助
    def current_user():
        return session.get("user")

    def current_role():
        return session.get("role", ROLE_ADMIN)

    def is_admin():
        return current_role() == ROLE_ADMIN

    def login_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user():
                return jsonify({"ok": False, "error": "未登录"}), 401
            return f(*args, **kwargs)
        return wrapper

    def admin_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user():
                return jsonify({"ok": False, "error": "未登录"}), 401
            if not is_admin():
                return jsonify({"ok": False, "error": "无权限，需要管理员"}), 403
            return f(*args, **kwargs)
        return wrapper

    def visible_rules():
        """当前登录用户可见的规则：管理员全部；普通用户 = 自己的 + 共享。"""
        if is_admin():
            return _rules.list()
        me = current_user()
        return [r for r in _rules.list()
                if r.get("owner") in (me, OWNER_SHARED, None)]

    def can_edit_rule(rule):
        return is_admin() or rule.get("owner") == current_user()

    def visible_contracts(rules):
        contracts = set()
        for r in rules:
            if r.get("contract_a"):
                contracts.add(r["contract_a"])
            if r.get("contract_b"):
                contracts.add(r["contract_b"])
        return contracts

    # ------------------------------------------------------------ 前端
    @app.route("/")
    def index():
        return render_template("index.html")

    # ------------------------------------------------------------ 登录
    @app.route("/api/login", methods=["POST"])
    def api_login():
        data = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        user = _users.authenticate(username, password)
        if not user:
            logger.warning("登录失败: 用户 %s", username)
            return jsonify({"ok": False, "error": "用户名或密码错误"}), 401
        session.clear()
        session["user"] = user["username"]
        session["role"] = user["role"]
        logger.info("用户登录: %s (%s)", user["username"], user["role"])
        return jsonify({"ok": True, "user": user})

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.clear()
        return jsonify({"ok": True})

    @app.route("/api/me")
    def api_me():
        if not current_user():
            return jsonify({"ok": False, "error": "未登录"}), 401
        return jsonify({"ok": True, "user": {"username": current_user(), "role": current_role()}})

    # ------------------------------------------------------------ 状态
    @app.route("/api/status")
    @login_required
    def api_status():
        snap = _state.snapshot()
        rules = visible_rules()
        rule_ids = {r["id"] for r in rules}
        snap["rule_values"] = {k: v for k, v in snap.get("rule_values", {}).items() if k in rule_ids}
        snap["alert_history"] = [a for a in snap.get("alert_history", []) if a.get("rule_id") in rule_ids]
        return jsonify(snap)

    # ------------------------------------------------------------ 规则 CRUD
    @app.route("/api/rules", methods=["GET"])
    @login_required
    def api_rules_list():
        return jsonify({
            "rules": visible_rules(),
            "metrics": METRIC_LABELS,
            "varieties": VARIETIES,
            "role": current_role(),
            "user": current_user(),
        })

    @app.route("/api/rules", methods=["POST"])
    @login_required
    def api_rules_create():
        data = request.get_json(silent=True) or {}
        if is_admin():
            data.setdefault("owner", OWNER_SHARED)
        else:
            data["owner"] = current_user()  # 普通用户只能创建自己的规则
        try:
            rule = _rules.create(data)
            return jsonify({"ok": True, "rule": rule}), 201
        except Exception as exc:
            logger.error("创建规则失败: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/rules/<rule_id>", methods=["PUT"])
    @login_required
    def api_rules_update(rule_id):
        data = request.get_json(silent=True) or {}
        exist = _rules.get(rule_id)
        if not exist:
            return jsonify({"ok": False, "error": "规则不存在"}), 404
        if not can_edit_rule(exist):
            return jsonify({"ok": False, "error": "无权修改该规则"}), 403
        if not is_admin():
            data["owner"] = current_user()  # 普通用户改规则时归属不变
        rule = _rules.update(rule_id, data)
        return jsonify({"ok": True, "rule": rule})

    @app.route("/api/rules/<rule_id>", methods=["DELETE"])
    @login_required
    def api_rules_delete(rule_id):
        exist = _rules.get(rule_id)
        if not exist:
            return jsonify({"ok": False, "error": "规则不存在"}), 404
        if not can_edit_rule(exist):
            return jsonify({"ok": False, "error": "无权删除该规则"}), 403
        _rules.delete(rule_id)
        _state.remove_rule_value(rule_id)
        return jsonify({"ok": True})

    # ------------------------------------------------------------ 手动检测
    @app.route("/api/check/<rule_id>", methods=["POST"])
    @login_required
    def api_check(rule_id):
        exist = _rules.get(rule_id)
        if not exist:
            return jsonify({"error": "规则不存在"}), 404
        if not can_edit_rule(exist):
            return jsonify({"ok": False, "error": "无权操作该规则"}), 403
        return jsonify(_engine.check_now(rule_id))

    # ------------------------------------------------------------ 基础元信息（所有登录用户）
    @app.route("/api/meta")
    @login_required
    def api_meta():
        return jsonify({
            "user": current_user(),
            "role": current_role(),
            "datasource": _config.get("datasource", "hybrid"),
            "sinarefreshinterval": _config.get("sinarefreshinterval", "1"),
            "derivedinterval": _config.get("derivedinterval", "300"),
            "pollinterval": _config.get("pollinterval", "10"),
        })

    # ------------------------------------------------------------ 全局配置（仅管理员）
    @app.route("/api/config", methods=["GET"])
    @admin_required
    def api_config_get():
        return jsonify(_config.as_dict())

    @app.route("/api/config", methods=["POST"])
    @admin_required
    def api_config_update():
        data = request.get_json(silent=True) or {}
        # 前端提交脱敏值时表示"不修改"，不覆盖真实 key
        if "****" in str(data.get("wechathookkey", "")):
            data.pop("wechathookkey", None)
        with _state_lock:
            changed = _config.update(data)
            _fetcher.token_refresh_interval = max(_config.get_int("tokenrefreshinterval", 600), 30)
        # 切换到 hybrid 时确保衍生指标后台线程在运行
        if _config.get("datasource", "sina").strip().lower() == "hybrid":
            _engine.start()
        return jsonify({"ok": True, "changed": changed, "config": _config.as_dict()})

    # ------------------------------------------------------------ 测试告警（仅管理员）
    @app.route("/api/test-alert", methods=["POST"])
    @admin_required
    def api_test_alert():
        data = request.get_json(silent=True) or {}
        key = data.get("webhook_key") or _config.get_webhook_key()
        if not key:
            return jsonify({"ok": False, "error": "尚未配置企业微信 webhook key"}), 400
        result = _alarm.send_test(webhook_key=key, mentioned_list=data.get("mentioned_list"))
        ok = result.get("errcode", -1) == 0
        return jsonify({"ok": ok, "result": result})

    # ------------------------------------------------------------ 行情数据（按用户隔离）
    @app.route("/api/market-data")
    @login_required
    def api_market_data():
        md = _state.snapshot().get("market_data") or {}
        flat = {}
        for variety, table in md.items():
            for contract, row in table.items():
                flat[contract] = row
        if not is_admin():
            allowed = visible_contracts(visible_rules())
            flat = {c: row for c, row in flat.items() if c in allowed}
        return jsonify(flat)

    # ------------------------------------------------------------ 用户管理（仅管理员）
    @app.route("/api/users", methods=["GET"])
    @admin_required
    def api_users_list():
        return jsonify({"users": _users.list(), "me": current_user()})

    @app.route("/api/users", methods=["POST"])
    @admin_required
    def api_users_create():
        data = request.get_json(silent=True) or {}
        try:
            user = _users.create(
                username=data.get("username", ""),
                password=data.get("password", ""),
                role=data.get("role", "user"),
            )
            return jsonify({"ok": True, "user": user}), 201
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/users/<username>/password", methods=["POST"])
    @admin_required
    def api_users_set_password(username):
        data = request.get_json(silent=True) or {}
        try:
            _users.set_password(username, data.get("password", ""))
            return jsonify({"ok": True})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/api/users/<username>", methods=["DELETE"])
    @admin_required
    def api_users_delete(username):
        if username == current_user():
            return jsonify({"ok": False, "error": "不能删除当前登录的管理员"}), 400
        try:
            _users.delete(username)
            return jsonify({"ok": True})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    # ------------------------------------------------------------ 引擎控制（仅管理员）
    @app.route("/api/engine", methods=["POST"])
    @admin_required
    def api_engine():
        data = request.get_json(silent=True) or {}
        action = data.get("action", "start")
        if action == "start":
            _engine.start()
            return jsonify({"ok": True, "running": True})
        if action == "stop":
            _engine.stop()
            return jsonify({"ok": True, "running": False})
        return jsonify({"ok": False, "error": "未知操作"}), 400

    return app


app = create_app()


def _resolve_listen_port(host, port):
    """特权端口(如 80)在非 root 下无法绑定，自动回退到非特权端口 serverportfallback。

    仅对 <1024 的端口做探测；root 有权限则仍用配置端口，其他绑定错误交给 Flask 正常报错。
    """
    if port >= 1024:
        return port
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except PermissionError:
        fallback = int(os.environ.get("PORT_FALLBACK") or _config.get("serverportfallback") or "5000")
        logger.warning(
            "端口 %s 为特权端口且当前非 root 运行，自动改用回退端口 %s"
            "（可设环境变量 PORT_FALLBACK 或 config.ini 的 serverportfallback 调整）",
            port, fallback,
        )
        return fallback
    finally:
        probe.close()
    return port


if __name__ == "__main__":
    # 端口/监听地址：优先环境变量 HOST/PORT（k8s 部署常用），否则读取 config.ini 的 serverhost/serverport
    host = os.environ.get("HOST") or _config.get("serverhost") or "0.0.0.0"
    port = int(os.environ.get("PORT") or _config.get("serverport") or "80")
    port = _resolve_listen_port(host, port)
    _engine.start()
    logger.info("监控服务已启动: http://%s:%s", host, port)
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)