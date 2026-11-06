"""Authenticated API and bundled offline UI. The API never calls a vendor SDK."""
from __future__ import annotations

from collections import Counter
from importlib.resources import files
import os
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..benchmarks import REGISTRY
from ..contracts import QTestError, loads, read_json, require
from ..metrics import DIRECTION, METRICS, PRIMARY
from . import auth
from .store import ACTIVE, Conflict, Missing, Store, audit, fixture, label

BODY_LIMIT = 512 * 1024


def read_live_json(path):
    # The worker atomically replaces these files while the monitor reads them;
    # Windows denies the read for the instant of replacement. Retry the local
    # read only, never anything that touches a device.
    for attempt in range(41):
        try:
            return read_json(path)
        except PermissionError:
            if os.name != "nt" or attempt == 40:
                raise
            time.sleep(0.025)


def create_app(data_dir, origin="http://127.0.0.1:8765"):
    store = Store(data_dir)
    require(store.path.exists(), "请先执行 qtest-web init")
    parsed = urlsplit(origin)
    require(parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in ("", "/"), "origin 必须是完整 HTTP(S) 站点地址")
    require(parsed.scheme == "https" or parsed.hostname in ("127.0.0.1", "localhost", "::1"), "内网多人部署请配置 HTTPS origin；HTTP 仅用于本机预览")
    origin = origin.rstrip("/")
    secure = parsed.scheme == "https"
    app = FastAPI(title="QTest Lab", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname])

    @app.middleware("http")
    async def guard(request, call_next):
        if request.url.path.startswith("/api/"):
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if request.headers.get("origin") != origin or request.headers.get("x-qtest-request") != "1":
                    return JSONResponse({"detail": "写操作必须来自管理台同源页面"}, status_code=403)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"detail": "需要 application/json"}, status_code=415)
            if request.url.path != "/api/login":
                user = auth.current_user(store, request.cookies.get("qtest_session"))
                if not user:
                    return JSONResponse({"detail": "请登录"}, status_code=401)
                request.state.user = user
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(QTestError)
    async def contract_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(Missing)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    async def body(request):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > BODY_LIMIT:
                raise HTTPException(413, "请求超过 512 KiB")
            chunks.append(chunk)
        value = loads(b"".join(chunks))
        require(isinstance(value, dict), "请求必须是 JSON 对象")
        return value

    def access(request, role="viewer"):
        user = request.state.user
        if role == "admin" and user["role"] != "admin" or role == "operator" and user["role"] == "viewer":
            raise PermissionError("当前账号没有此操作权限")
        return user

    @app.post("/api/login")
    async def login(request: Request):
        from starlette.concurrency import run_in_threadpool
        data = await body(request)
        token = await run_in_threadpool(auth.login, store, data.get("username"), data.get("password"), request.client.host if request.client else "unknown")
        response = JSONResponse({"user": auth.current_user(store, token)})
        response.set_cookie("qtest_session", token, httponly=True, secure=secure, samesite="strict", max_age=auth.SESSION_SECONDS, path="/")
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        with store.db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (auth.token_hash(request.cookies["qtest_session"]),))
        response = JSONResponse({"ok": True})
        response.delete_cookie("qtest_session", path="/")
        return response

    @app.get("/api/me")
    def me(request: Request):
        return {"user": access(request), "version": __version__, "deployment": "实验室共享工作区"}

    @app.get("/api/overview")
    def overview():
        with store.db() as conn:
            counts = {r["status"]: r["n"] for r in conn.execute("SELECT status,count(*) n FROM tasks GROUP BY status")}
            beat = conn.execute("SELECT heartbeat,pid FROM worker WHERE id=1").fetchone()
            cases = conn.execute("SELECT count(*) FROM cases WHERE archived=0").fetchone()[0]
        age = max(0, time.time() - beat["heartbeat"]) if beat else None
        return {"status_counts": counts, "cases": cases, "devices": store.device_rows(), "worker": {"online": age is not None and age < 10, "heartbeat_age_seconds": age}}

    @app.get("/api/catalog")
    def catalog():
        return {"protocols": [{"id": key, "title": b.title, "layer": b.layer, "capability": b.capability,
                                "primary": PRIMARY.get(key),
                                "template": next(c for c in fixture()["cases"] if c["benchmark"] == key)} for key, b in REGISTRY.items()],
                "default_limits": fixture()["limits"], "default_seed": fixture()["seed"],
                "metrics": METRICS, "directions": DIRECTION}

    @app.get("/api/cases")
    def cases():
        return store.case_rows()

    @app.post("/api/cases")
    async def new_case(request: Request):
        user = access(request, "operator")
        return store.save_case(await body(request), user["id"])

    @app.put("/api/cases/{case_id}")
    async def edit_case(case_id: str, request: Request):
        user = access(request, "operator")
        return store.save_case(await body(request), user["id"], case_id)

    @app.get("/api/devices")
    def devices():
        return store.device_rows()

    @app.post("/api/devices/{device_id}/resolve")
    async def resolve_device(device_id: str, request: Request):
        user = access(request, "admin")
        note = label((await body(request)).get("note"), 1000)
        store.one("devices", device_id)
        with store.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            require(not conn.execute("SELECT 1 FROM tasks WHERE device_id=? AND status IN ('running','cancel_requested')", (device_id,)).fetchone(), "设备仍有执行中的任务，不能解除阻塞")
            conn.execute("UPDATE devices SET blocked='' WHERE id=?", (device_id,))
            audit(conn, user["id"], "device.resolve", device_id, note)
        return {"ok": True}

    @app.post("/api/plan")
    async def preview_plan(request: Request):
        access(request, "operator")
        return store.prepare(await body(request))["plan"]

    @app.get("/api/tasks")
    def tasks():
        with store.db() as conn:
            rows = conn.execute("""SELECT t.id,t.kind,t.name,t.created_by,u.username,t.device_id,t.status,t.created_at,
                t.started_at,t.finished_at,t.message FROM tasks t JOIN users u ON t.created_by=u.id
                ORDER BY t.created_at DESC,t.id DESC LIMIT 200""").fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/tasks")
    async def new_task(request: Request):
        user = access(request, "operator")
        return store.enqueue(await body(request), user["id"], request.headers.get("idempotency-key"))

    @app.post("/api/comparisons")
    async def compare(request: Request):
        user = access(request, "operator")
        return store.enqueue(await body(request), user["id"], request.headers.get("idempotency-key"), "comparison")

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str):
        task = store.task_public(task_id)
        with store.db() as conn:
            task["events"] = [dict(r) for r in conn.execute("SELECT at,message FROM events WHERE task_id=? ORDER BY id DESC LIMIT 100", (task_id,))]
        root = store.task_dir(task_id)
        runfile = root / "run" / "run.json"
        if runfile.is_file():
            summary = read_live_json(runfile)
            rows = summary["cases"]
            # Live summary is clearly separate from independently verified final results.
            task["progress"] = {"total": sum(c["repetitions"] for c in task["input"]["suite"]["cases"]),
                                "ended": sum(c["status"] != "running" for c in rows),
                                "completed": sum(c["status"] == "completed" for c in rows),
                                "resources": summary["resources"], "cases": rows}
            record_paths = [p for row in rows for p in row["records"]]
            if record_paths:
                record = read_live_json(root / "run" / record_paths[-1])
                task["progress"]["latest_job"] = {k: record[k] for k in ("request_id", "job_id", "state", "provider_state", "observed_at", "created_at", "cancellation", "submission_outcome") if k in record}
        return task

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str, request: Request):
        user = access(request, "operator")
        store.cancel(task_id, user["id"], user["role"] == "admin")
        return {"ok": True}

    @app.get("/api/tasks/{task_id}/result")
    def result(task_id: str):
        task = store.one("tasks", task_id)
        if task["status"] in ACTIVE:
            raise Conflict("任务尚未结束")
        name = "result.json" if task["kind"] == "test" else "comparison.comparison.json"
        path = store.task_dir(task_id) / name
        if not path.is_file():
            raise Missing("暂无通过验收的结果，请查看任务事件")
        return read_json(path)

    @app.get("/api/tasks/{task_id}/artifacts/{name}")
    def download(task_id: str, name: str):
        task = store.task_public(task_id)
        if name not in task["artifacts"]:
            raise Missing("文件尚未生成或不允许下载")
        return FileResponse(store.task_dir(task_id) / name, filename=task_id[:8] + "-" + name, media_type="application/octet-stream")

    @app.get("/api/users")
    def users(request: Request):
        access(request, "admin")
        with store.db() as conn:
            return [dict(r) for r in conn.execute("SELECT id,username,role,enabled,created_at FROM users ORDER BY created_at")]

    @app.post("/api/users")
    async def new_user(request: Request):
        from starlette.concurrency import run_in_threadpool
        user = access(request, "admin")
        data = await body(request)
        return await run_in_threadpool(auth.add_user, store, data.get("username"), data.get("password"), data.get("role"), user["id"])

    @app.put("/api/users/{user_id}")
    async def edit_user(user_id: str, request: Request):
        from starlette.concurrency import run_in_threadpool
        actor = access(request, "admin")
        await run_in_threadpool(auth.update_user, store, user_id, await body(request), actor["id"])
        return {"ok": True}

    @app.get("/api/audit")
    def audit_log(request: Request):
        access(request, "admin")
        with store.db() as conn:
            return [dict(r) for r in conn.execute("SELECT a.*,COALESCE(u.username,a.actor) username FROM audit a LEFT JOIN users u ON u.id=a.actor ORDER BY a.id DESC LIMIT 200")]

    static = files(__package__).joinpath("static")
    app.mount("/assets", StaticFiles(directory=str(static)), name="assets")

    @app.get("/")
    def index():
        return FileResponse(str(static.joinpath("index.html")))

    return app
