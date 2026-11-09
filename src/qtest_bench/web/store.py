"""Short SQLite transactions; task inputs are immutable snapshots, never live joins."""
from __future__ import annotations

from contextlib import contextmanager
from importlib.resources import files
import re
import sqlite3
import uuid
from pathlib import Path

from ..adapter import LocalAdapter
from ..contracts import canonical, digest, loads, require, validate_suite
from ..engine import now, plan

ACTIVE = ("queued", "running", "cancel_requested")
ROLES = ("viewer", "operator", "admin")


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


def encode(value):
    return canonical(value).decode("utf-8")


def uid():
    return uuid.uuid4().hex


def label(value, maximum=200):
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum, "名称或说明不能为空，且不能超出长度限制")
    return value.strip()


def fixture():
    return loads(files(__package__).joinpath("defaults/smoke-suite.json").read_bytes())


def case_valid(case):
    suite = fixture()
    suite["cases"] = [case]
    validate_suite(suite)
    return case


def event(conn, task_id, message):
    conn.execute("INSERT INTO events(task_id,at,message) VALUES(?,?,?)", (task_id, now(), message))


def audit(conn, actor, action, target, detail=""):
    conn.execute("INSERT INTO audit(at,actor,action,target,detail) VALUES(?,?,?,?,?)", (now(), actor, action, target, detail))


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / "qtest.sqlite3"

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "tasks").mkdir(exist_ok=True)
        with self.db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users(
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                    role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts(
                    key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, started REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cases(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, data TEXT NOT NULL,
                    version INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS devices(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, adapter TEXT NOT NULL,
                    config_path TEXT, descriptor TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    blocked TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                    created_by TEXT NOT NULL REFERENCES users(id), device_id TEXT REFERENCES devices(id),
                    status TEXT NOT NULL, payload TEXT NOT NULL, input_hash TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT, message TEXT NOT NULL DEFAULT '',
                    UNIQUE(created_by,idempotency_key));
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    at TEXT NOT NULL, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS worker(
                    id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL, pid INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status,created_at);
                CREATE INDEX IF NOT EXISTS events_task ON events(task_id,id);
            """)
            conn.execute("INSERT OR IGNORE INTO devices(id,name,adapter,descriptor) VALUES(?,?,?,?)",
                         ("local", "本地理想模拟器", "local", encode(LocalAdapter().describe())))
            titles = ["镜像电路保真度与深度", "Ising 哈密顿演化", "HOM 玻色采样", "五节点 MIS 优化", "双比特模型 VQE", "一维 Ising 链材料动力学"]
            for title, case in zip(titles, fixture()["cases"]):
                conn.execute("INSERT OR IGNORE INTO cases VALUES(?,?,?,?,1,0,?,?)",
                             ("builtin-" + case["id"], title, "内置集成测试示例，可复制后编辑；不代表真实设备成绩。", encode(case), "system", now()))

    def one(self, table, key):
        assert table in ("cases", "tasks", "devices", "users")
        require(isinstance(key, str) and 0 < len(key) <= 200, "无效记录 ID")
        with self.db() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (key,)).fetchone()
        if row is None:
            raise Missing("记录不存在")
        return dict(row)

    def task_dir(self, task_id):
        require(bool(re.fullmatch(r"[0-9a-f]{32}", task_id)), "无效任务 ID")
        return self.root / "tasks" / task_id

    def case_rows(self):
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM cases ORDER BY archived,updated_at DESC LIMIT 2000").fetchall()
        return [self.case_public(dict(r)) for r in rows]

    @staticmethod
    def case_public(row):
        row["data"] = loads(row["data"])
        row["archived"] = bool(row["archived"])
        return row

    def save_case(self, body, actor, case_id=None):
        operation = "case.update" if case_id else "case.create"
        name = label(body.get("name"))
        description = body.get("description", "")
        require(isinstance(description, str) and len(description) <= 4000, "描述最多 4000 字符")
        data = case_valid(body.get("data"))
        archived = body.get("archived", False)
        require(type(archived) is bool, "归档状态必须为布尔值")
        with self.db() as conn:
            if case_id:
                require(type(body.get("version")) is int, "请提供用例版本号")
                cursor = conn.execute("UPDATE cases SET name=?,description=?,data=?,version=version+1,archived=?,updated_at=? WHERE id=? AND version=?",
                                      (name, description, encode(data), archived, now(), case_id, body["version"]))
                if cursor.rowcount != 1:
                    raise Conflict("用例已被其他人更新，请重新打开后编辑")
            else:
                require(conn.execute("SELECT count(*) FROM cases").fetchone()[0] < 2000, "用例数量达到首版上限 2000")
                case_id = uid()
                conn.execute("INSERT INTO cases VALUES(?,?,?,?,1,?,?,?)", (case_id, name, description, encode(data), archived, actor, now()))
            audit(conn, actor, operation, case_id)
        return self.case_public(self.one("cases", case_id))

    def device_rows(self):
        with self.db() as conn:
            rows = conn.execute("SELECT id,name,adapter,descriptor,enabled,blocked FROM devices ORDER BY name").fetchall()
        return [dict(r, descriptor=loads(r["descriptor"]), enabled=bool(r["enabled"])) for r in rows]

    def prepare(self, body):
        device = self.one("devices", body.get("device_id"))
        require(device["enabled"] and not device["blocked"], "设备已停用或执行状态待核实")
        refs = body.get("cases")
        require(isinstance(refs, list) and 1 <= len(refs) <= 64, "每次任务选择 1–64 个用例")
        cases, snapshots = [], []
        for ref in refs:
            require(isinstance(ref, dict), "无效用例引用")
            require(type(ref.get("version")) is int, "用例版本必须为整数")
            row = self.one("cases", ref.get("id"))
            if row["version"] != ref.get("version") or row["archived"]:
                raise Conflict("所选用例版本发生变化或已归档，请刷新后重新预检")
            cases.append(loads(row["data"]))
            snapshots.append({"id": row["id"], "version": row["version"], "name": row["name"]})
        suite = {"schema_version": "1.0", "suite_id": "web-suite-v1", "protocol_version": "pilot-closed-0.1",
                 "seed": body.get("seed"), "limits": body.get("limits"), "cases": cases}
        execution_plan = plan(suite, loads(device["descriptor"]))
        require(execution_plan["fits_budget"], "预计作业数或 shots 超出设置上限")
        require(execution_plan["jobs"] > 0, "此设备不支持任何所选用例")
        require(execution_plan["jobs"] <= 2000, "Web 单任务最多 2000 次作业，请拆分测试")
        return {"suite": suite, "case_versions": snapshots, "plan": execution_plan,
                "device": {"id": device["id"], "adapter": device["adapter"], "config_path": device["config_path"], "descriptor": loads(device["descriptor"])}}

    def enqueue(self, body, actor, key, kind="test"):
        require(isinstance(key, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{16,100}", key)), "需要有效的 Idempotency-Key")
        name = label(body.get("name"))
        fingerprint = digest({"kind": kind, "body": body})
        with self.db() as conn:
            old = conn.execute("SELECT id,input_hash FROM tasks WHERE created_by=? AND idempotency_key=?", (actor, key)).fetchone()
        if old:
            if old["input_hash"] != fingerprint:
                raise Conflict("同一提交标识不能用于不同任务")
            return self.task_public(old["id"])
        if kind == "test":
            payload = self.prepare(body)
            device_id = body["device_id"]
        else:
            ids = body.get("task_ids")
            require(isinstance(ids, list) and 2 <= len(ids) <= 20 and all(isinstance(i, str) for i in ids) and len(set(ids)) == len(ids), "请选择 2–20 个不同的测试任务")
            for task_id in ids:
                task = self.one("tasks", task_id)
                require(task["kind"] == "test" and task["status"] not in ACTIVE and (self.task_dir(task_id) / "report.report.json").is_file(), "所选任务尚无已验证报告")
            payload, device_id = {"task_ids": ids}, None
        task_id = uid()
        try:
            with self.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                require(conn.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running','cancel_requested')").fetchone()[0] < 200, "队列已满，请等待任务结束")
                conn.execute("INSERT INTO tasks(id,kind,name,created_by,device_id,status,payload,input_hash,idempotency_key,created_at) VALUES(?,?,?,?,?,'queued',?,?,?,?)",
                             (task_id, kind, name, actor, device_id, encode(payload), fingerprint, key, now()))
                audit(conn, actor, "task.create", task_id, kind)
                event(conn, task_id, "任务已进入队列，输入快照已冻结")
        except sqlite3.IntegrityError:
            # A concurrent request with the same key won the UNIQUE constraint.
            with self.db() as conn:
                old = conn.execute("SELECT id,input_hash FROM tasks WHERE created_by=? AND idempotency_key=?", (actor, key)).fetchone()
            if not old or old["input_hash"] != fingerprint:
                raise Conflict("重复提交标识与任务输入冲突") from None
            task_id = old["id"]
        return self.task_public(task_id)

    def task_public(self, task_id):
        row = self.one("tasks", task_id)
        row.pop("idempotency_key")
        row.pop("input_hash")
        payload = loads(row.pop("payload"))
        if "device" in payload:
            payload["device"].pop("config_path", None)
        row["input"] = payload
        root = self.task_dir(task_id)
        names = ("report.md", "report.report.json", "result.zip") if row["kind"] == "test" else ("comparison.md", "comparison.comparison.json", "comparison.comparison.csv")
        row["artifacts"] = [name for name in names if (root / name).is_file() and row["status"] not in ACTIVE]
        return row

    def cancel(self, task_id, actor, admin=False):
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise Missing("任务不存在")
            if not admin and task["created_by"] != actor:
                raise PermissionError("只能取消本人任务")
            if task["status"] not in ACTIVE:
                return
            state = "cancelled" if task["status"] == "queued" else "cancel_requested"
            conn.execute("UPDATE tasks SET status=?,finished_at=? WHERE id=?", (state, now() if state == "cancelled" else None, task_id))
            audit(conn, actor, "task.cancel", task_id)
            event(conn, task_id, "排队任务已取消" if state == "cancelled" else "已申请停止继续执行；外部作业停止状态需核实")
