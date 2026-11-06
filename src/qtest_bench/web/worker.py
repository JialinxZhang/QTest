"""One durable, sequential worker per local data directory. No automatic retries."""
from __future__ import annotations

from contextlib import contextmanager
import os
import signal
import threading
import time

from ..adapter import load_adapter
from ..bundles import read_run, write_bundle
from ..compare import write_comparison
from ..contracts import QTestError, digest, loads, read_json, require, write_json
from ..engine import now, run_suite, verify_files
from ..metrics import METRICS
from ..report import usage
from ..report_io import write_report
from .store import Store, event


@contextmanager
def exclusive_worker(root):
    """OS lock survives API restarts and is released by the OS on worker death."""
    handle = (root / "worker.lock").open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise QTestError("已有 Worker 正在使用此数据目录") from None
        yield
    finally:
        handle.close()


def recover(store):
    # Called only after acquiring the exclusive process lock: no live worker owns these.
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT id,device_id FROM tasks WHERE status IN ('running','cancel_requested')").fetchall()
        for row in rows:
            message = "执行器中断，未自动重试；请核实设备任务及保留证据"
            conn.execute("UPDATE tasks SET status='interrupted',finished_at=?,message=? WHERE id=?", (now(), message, row["id"]))
            if row["device_id"]:
                conn.execute("UPDATE devices SET blocked=? WHERE id=?", ("任务 " + row["id"] + " 的外部执行状态待核实", row["device_id"]))
            event(conn, row["id"], message)


def claim(store):
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT t.* FROM tasks t LEFT JOIN devices d ON d.id=t.device_id
            WHERE t.status='queued' AND (t.kind='comparison' OR (d.enabled=1 AND d.blocked=''))
            ORDER BY t.created_at,t.id LIMIT 1""").fetchone()
        if row is None:
            return None
        conn.execute("UPDATE tasks SET status='running',started_at=? WHERE id=?", (now(), row["id"]))
        event(conn, row["id"], "执行器已领取任务")
        return dict(row)


def execute_task(store, task, stop=None):
    stop = stop or threading.Event()
    task_id, payload = task["id"], loads(task["payload"])
    root = store.task_dir(task_id)
    root.mkdir(parents=True, exist_ok=True)

    def cancelled():
        return stop.is_set() or store.one("tasks", task_id)["status"] == "cancel_requested"

    blocked, state, message = False, "failed", "任务未完成"
    try:
        if cancelled():
            state, message = "cancelled", "任务在执行前取消"
        elif task["kind"] == "comparison":
            sources = [store.task_dir(i) / "report.report.json" for i in payload["task_ids"]]
            write_comparison(sources, root / "comparison.md", strict=True)
            state = "succeeded"
            message = "综合对比已完成；取消申请到达时计算已结束" if cancelled() else "综合对比已完成，按可比条件分组"
        else:
            device = payload["device"]
            config = read_json(device["config_path"]) if device["config_path"] else {}
            adapter = load_adapter(device["adapter"], config)
            require(digest(adapter.describe()) == digest(device["descriptor"]), "设备描述已改变，请重新预检创建任务")
            operator = store.one("users", task["created_by"])["username"]
            summary = run_suite(payload["suite"], adapter, root / "run", operator, payload["mode"], should_cancel=cancelled)
            evidence = read_run(root / "run")
            records = [loads(v) for k, v in evidence.items() if k.startswith("observations/")]
            # A failed/uncertain provider call blocks further work even if report generation fails.
            blocked = any(r["state"] != "completed" for r in records)
            with store.db() as conn:
                event(conn, task_id, "实验执行结束，正在独立验收证据与生成报告")
            verified = verify_files(evidence, payload["suite"])
            write_report(evidence, root / "report.md", payload["suite"])
            write_bundle(root / "result.zip", "result", evidence)
            write_json(root / "result.json", {"summary": verified, "resources": usage(records), "metric_definitions": METRICS})
            statuses = [c["status"] for c in summary["cases"]]
            if "cancelled" in statuses:
                state, message = "cancelled", "已停止继续提交；已提交的外部作业状态需核实"
            elif all(s == "completed" for s in statuses):
                state, message = "succeeded", "全部重复实验完成，证据独立验收通过"
            elif "completed" in statuses:
                state, message = "partial", "部分实验完成，请查看失败或不支持的用例"
            else:
                state, message = "failed", "未获得完整实验结果，已保留证据"
    except Exception as exc:
        # SDK exception text can contain credentials; expose only a type and generic context.
        message = f"执行或验收失败（{type(exc).__name__}），请检查服务器配置和证据"
        state = "failed"
        if (root / "run").exists():
            blocked = True
    with store.db() as conn:
        conn.execute("UPDATE tasks SET status=?,finished_at=?,message=? WHERE id=?", (state, now(), message, task_id))
        if blocked and task["device_id"]:
            conn.execute("UPDATE devices SET blocked=? WHERE id=?", (f"任务 {task_id} 有外部执行状态待核实", task["device_id"]))
        event(conn, task_id, message)


def run_worker(root, once=False, stop=None):
    store = Store(root)
    require(store.path.exists(), "请先执行 qtest-web init")
    stop = stop or threading.Event()
    with exclusive_worker(store.root):
        recover(store)
        heartbeat_stop = threading.Event()

        def heartbeat():
            while not heartbeat_stop.is_set():
                try:
                    with store.db() as conn:
                        conn.execute("INSERT INTO worker VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,pid=excluded.pid", (time.time(), os.getpid()))
                except Exception:
                    stop.set()
                heartbeat_stop.wait(2)

        beat = threading.Thread(target=heartbeat, daemon=True)
        beat.start()
        try:
            while not stop.is_set():
                task = claim(store)
                if task:
                    execute_task(store, task, stop)
                if once:
                    break
                if not task:
                    stop.wait(0.5)
        finally:
            heartbeat_stop.set()
            beat.join(timeout=5)
            with store.db() as conn:
                conn.execute("DELETE FROM worker WHERE id=1")


def main_worker(root, once=False):
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run_worker(root, once=once, stop=stop)
