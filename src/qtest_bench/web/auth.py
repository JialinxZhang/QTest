"""Server-side revocable sessions and explicit shared-laboratory roles."""
from __future__ import annotations

import hashlib
import re
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

from ..contracts import require
from ..engine import now
from .store import ROLES, Conflict, Missing, audit, uid

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))
SESSION_SECONDS = 8 * 3600


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password):
    require(isinstance(password, str) and 12 <= len(password) <= 200, "密码长度应为 12–200 字符")
    return HASHER.hash(password)


def add_user(store, username, password, role="operator", actor="server-admin"):
    require(isinstance(username, str) and bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username)), "用户名应为 3–64 位字母、数字、点、横线或下划线")
    require(role in ROLES, "无效角色")
    hashed = password_hash(password)
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise Conflict("用户名已存在")
        user_id = uid()
        conn.execute("INSERT INTO users VALUES(?,?,?,?,1,?)", (user_id, username, hashed, role, now()))
        audit(conn, actor, "user.create", user_id, role)
    return {"id": user_id, "username": username, "role": role, "enabled": True}


def update_user(store, user_id, body, actor):
    role, enabled = body.get("role"), body.get("enabled")
    require(role in ROLES and type(enabled) is bool, "角色或启用状态无效")
    new_hash = password_hash(body["password"]) if body.get("password") else None
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise Missing("账号不存在")
        if row["role"] == "admin" and row["enabled"] and (not enabled or role != "admin"):
            require(conn.execute("SELECT count(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0] > 1, "不能停用或降级最后一位管理员")
        conn.execute("UPDATE users SET role=?,enabled=?,password_hash=? WHERE id=?", (role, enabled, new_hash or row["password_hash"], user_id))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        audit(conn, actor, "user.update", user_id, f"role={role}; enabled={enabled}")


def login(store, username, password, address):
    require(isinstance(username, str) and len(username) <= 64 and isinstance(password, str) and len(password) <= 200, "用户名或密码无效")
    keys = ["ip:" + token_hash(address), "user:" + token_hash(username)]
    stamp = time.time()
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM login_attempts WHERE started < ?", (stamp - 300,))
        for key in keys:
            row = conn.execute("SELECT attempts FROM login_attempts WHERE key=?", (key,)).fetchone()
            if row and row["attempts"] >= 20:
                raise PermissionError("尝试次数过多，请在 5 分钟后重试")
        for key in keys:
            conn.execute("INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1", (key, stamp))
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    try:
        valid = HASHER.verify(row["password_hash"] if row else DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid or not row or not row["enabled"]:
        raise PermissionError("用户名或密码无效")
    token = secrets.token_urlsafe(32)
    with store.db() as conn:
        # Recheck after the expensive hash to respect concurrent user suspension.
        current = conn.execute("SELECT enabled,password_hash FROM users WHERE id=?", (row["id"],)).fetchone()
        if not current["enabled"] or current["password_hash"] != row["password_hash"]:
            raise PermissionError("账号状态已改变，请重新登录")
        conn.execute("DELETE FROM sessions WHERE expires < ?", (stamp,))
        conn.execute("INSERT INTO sessions VALUES(?,?,?)", (token_hash(token), row["id"], stamp + SESSION_SECONDS))
        conn.execute("DELETE FROM login_attempts WHERE key=?", (keys[1],))
        audit(conn, row["id"], "session.login", row["id"])
    return token


def current_user(store, token):
    if not token or len(token) > 200:
        return None
    with store.db() as conn:
        row = conn.execute("SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.enabled=1", (token_hash(token), time.time())).fetchone()
    return dict(row) if row else None
