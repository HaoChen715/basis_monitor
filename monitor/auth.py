"""用户认证与权限（多用户隔离）。

用户存储: <程序目录>/config/users.json
  - 操作人员可直接在该文件新增用户: {"username": "张三", "password": "明文密码", "role": "user"}
    （明文 password 首次加载时自动转为哈希并写回）
  - 管理员也可在前端「用户管理」页创建/删除/重置密码

角色:
  - admin 管理员: 可查看/配置全部规则、全部行情、系统设置、用户管理
  - user  普通用户: 只能看到归属自己的规则（owner == 自己）以及共享规则（owner == "*"）

规则可见性（rules.json 的 owner 字段）:
  - "<用户名>": 仅该用户可见可编辑
  - "*"       : 共享规则，所有用户可见，仅管理员可编辑
"""
import json
import logging
import os
import secrets
import threading
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger("basis_monitor")

ROLE_ADMIN = "admin"
ROLE_USER = "user"
OWNER_SHARED = "*"


def hash_password(password):
    return generate_password_hash(password)


class UserStore:
    def __init__(self, path, bootstrap_user="admin", bootstrap_pass=""):
        self.path = path
        self._lock = threading.RLock()
        self._users = []
        self.load()
        self._ensure_admin(bootstrap_user, bootstrap_pass)

    # ---------------------------------------------------------------- 持久化
    def load(self):
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        self._users = json.load(f).get("users", [])
                except Exception as exc:
                    logger.error("users.json 解析失败: %s", exc)
                    self._users = []
            else:
                self._users = []

            changed = False
            for u in self._users:
                u.setdefault("role", ROLE_USER)
                u.setdefault("created_at", datetime.now().isoformat())
                # 操作人员直接写入的明文 password -> 自动转为哈希
                if u.get("password"):
                    u["password_hash"] = hash_password(u.pop("password"))
                    changed = True
                if not u.get("password_hash"):
                    raise ValueError(f"用户 {u.get('username')} 缺少 password/password_hash")
            if changed:
                self._save_locked()

    def _save_locked(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {"version": 1, "users": self._users}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _ensure_admin(self, bootstrap_user, bootstrap_pass):
        """首次运行且无任何管理员时，按配置自动创建初始管理员。"""
        with self._lock:
            if any(u.get("role") == ROLE_ADMIN for u in self._users):
                return
            username = (bootstrap_user or "admin").strip()
            if bootstrap_pass:
                password = bootstrap_pass
                logger.info("已按 config.ini 创建初始管理员: %s", username)
            else:
                password = secrets.token_urlsafe(12)
                logger.warning(
                    "已自动创建初始管理员 %s，初始密码: %s（请登录后立即修改）",
                    username, password,
                )
            self._users.append({
                "username": username,
                "password_hash": hash_password(password),
                "role": ROLE_ADMIN,
                "created_at": datetime.now().isoformat(),
            })
            self._save_locked()

    # ---------------------------------------------------------------- 查询
    def list(self):
        with self._lock:
            return [
                {"username": u.get("username"), "role": u.get("role"),
                 "created_at": u.get("created_at")}
                for u in self._users
            ]

    def get(self, username):
        with self._lock:
            for u in self._users:
                if u.get("username") == username:
                    return dict(u)
        return None

    def has_admin(self):
        return any(u.get("role") == ROLE_ADMIN for u in self._users)

    def authenticate(self, username, password):
        u = self.get(username)
        if not u or not u.get("password_hash"):
            return None
        if check_password_hash(u["password_hash"], password):
            return {"username": u["username"], "role": u["role"]}
        return None

    # ---------------------------------------------------------------- 管理
    def create(self, username, password, role=ROLE_USER):
        username = (username or "").strip()
        if not username or not password:
            raise ValueError("用户名与密码不能为空")
        if self.get(username):
            raise ValueError(f"用户 {username} 已存在")
        if role not in (ROLE_ADMIN, ROLE_USER):
            raise ValueError("角色无效")
        with self._lock:
            self._users.append({
                "username": username,
                "password_hash": hash_password(password),
                "role": role,
                "created_at": datetime.now().isoformat(),
            })
            self._save_locked()
        return {"username": username, "role": role}

    def set_password(self, username, new_password):
        if not new_password:
            raise ValueError("新密码不能为空")
        with self._lock:
            for u in self._users:
                if u.get("username") == username:
                    u["password_hash"] = hash_password(new_password)
                    self._save_locked()
                    return True
        raise ValueError(f"用户 {username} 不存在")

    def delete(self, username):
        with self._lock:
            remaining_admin = sum(
                1 for u in self._users
                if u.get("role") == ROLE_ADMIN and u.get("username") != username
            )
            if not remaining_admin:
                raise ValueError("不能删除最后一个管理员")
            before = len(self._users)
            self._users = [u for u in self._users if u.get("username") != username]
            if len(self._users) != before:
                self._save_locked()
                return True
        raise ValueError(f"用户 {username} 不存在")