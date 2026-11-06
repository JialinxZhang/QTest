from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from qtest_bench.adapter import LocalAdapter
from qtest_bench.bundles import read_run
from qtest_bench.contracts import QTestError, loads, read_json, write_json
from qtest_bench.engine import verify_files
from qtest_bench.web.app import create_app
from qtest_bench.web.auth import add_user, update_user
from qtest_bench.web.store import Conflict, Store
from qtest_bench.web.worker import claim, execute_task, exclusive_worker, recover, run_worker

ORIGIN = "http://127.0.0.1:8765"
PASSWORD = "test-only-password-1234"
HEADERS = {"Origin": ORIGIN, "X-QTest-Request": "1"}


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.store.initialize()
        self.admin = add_user(self.store, "admin", PASSWORD, "admin")
        self.operator = add_user(self.store, "tester", PASSWORD, "operator")
        self.viewer = add_user(self.store, "reader", PASSWORD, "viewer")
        self.app = create_app(self.root)
        self.client = TestClient(self.app, base_url=ORIGIN, headers=HEADERS)
        self.signin("admin")

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def signin(self, username):
        self.client.cookies.clear()
        response = self.client.post("/api/login", json={"username": username, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def body(self, all_cases=False):
        rows = self.store.case_rows()
        selected = rows if all_cases else [next(c for c in rows if c["data"]["benchmark"] == "kernel.boson_fock.v1")]
        return {"name": "API smoke", "device_id": "local", "cases": [{"id": c["id"], "version": c["version"]} for c in selected],
                "seed": 2345, "limits": {"max_jobs": 100, "max_shots": 100000, "wall_seconds": 30, "poll_seconds": 0.01}, "mode": "organizer"}

    def submit(self, body=None, key="test-idempotency-key-0001"):
        response = self.client.post("/api/tasks", json=body or self.body(), headers={"Idempotency-Key": key})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_static_and_authentication_boundary(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/assets/app.js").status_code, 200)
        self.assertEqual(self.client.get("/api/tasks").status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={"username": "admin", "password": "wrong"}).status_code, 403)
        response = self.signin("admin")
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.client.post("/api/logout", json={})
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_csrf_origin_host_and_request_size(self):
        self.assertEqual(self.client.post("/api/tasks", json=self.body(), headers={"Origin": "https://other.test"}).status_code, 403)
        self.assertEqual(self.client.post("/api/tasks", json=self.body(), headers={"X-QTest-Request": ""}).status_code, 403)
        self.assertEqual(self.client.get("/api/me", headers={"Host": "evil.test"}).status_code, 400)
        self.assertEqual(self.client.post("/api/cases", content='{"padding":"'+'a'*524288+'"}', headers={"Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.client.post("/api/cases", content='{"name":"x","name":"y"}', headers={"Content-Type": "application/json"}).status_code, 400)

    def test_lan_requires_https_and_secure_cookie(self):
        with self.assertRaises(QTestError):
            create_app(self.root, "http://lab.test")
        app = create_app(self.root, "https://lab.test")
        with TestClient(app, base_url="https://lab.test", headers={"Origin": "https://lab.test", "X-QTest-Request": "1"}) as client:
            response = client.post("/api/login", json={"username": "admin", "password": PASSWORD})
            self.assertEqual(response.status_code, 200)
            self.assertIn("Secure", response.headers["set-cookie"])

    def test_catalog_exposes_metric_semantics_and_primary(self):
        catalog = self.client.get("/api/catalog").json()
        self.assertIn("metrics", catalog)
        self.assertIn("directions", catalog)
        material = next(p for p in catalog["protocols"] if p["id"] == "application.material_dynamics.v1")
        self.assertEqual(material["primary"], "mean_structure_factor_mae")
        self.assertEqual(catalog["metrics"]["mean_structure_factor_mae"][2], "lower")
        self.assertEqual(catalog["metrics"]["absolute_energy_error"][0], "最终能量绝对误差")
        self.assertEqual(catalog["directions"]["lower"], "越低越好")

    def test_viewer_and_operator_permissions(self):
        self.signin("reader")
        self.assertEqual(self.client.get("/api/cases").status_code, 200)
        self.assertEqual(self.client.post("/api/cases", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/tasks", json=self.body()).status_code, 403)
        self.assertEqual(self.client.get("/api/users").status_code, 403)
        self.signin("tester")
        self.assertEqual(self.client.post("/api/users", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/devices/local/resolve", json={"note": "done"}).status_code, 403)
        self.submit()

    def test_case_validation_edit_conflict_archive_and_frozen_inputs(self):
        original = self.store.case_rows()[0]
        new = copy.deepcopy(original)
        new["name"] = "new case"
        new["data"]["id"] = "new-case"
        response = self.client.post("/api/cases", json=new)
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        input_body = self.body()
        input_body["cases"] = [{"id": row["id"], "version": row["version"]}]
        task = self.submit(input_body)
        row["data"]["shots"] = 123
        updated = self.client.put("/api/cases/"+row["id"], json=row)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["version"], 2)
        self.assertEqual(self.client.put("/api/cases/"+row["id"], json=row).status_code, 409)
        frozen = self.client.get("/api/tasks/"+task["id"]).json()["input"]["suite"]
        self.assertEqual(frozen["cases"][0]["shots"], original["data"]["shots"])
        self.assertEqual(self.client.post("/api/plan", json=input_body).status_code, 409)
        row = updated.json()
        row["archived"] = True
        self.client.put("/api/cases/"+row["id"], json=row)
        input_body["cases"][0]["version"] = 3
        self.assertEqual(self.client.post("/api/plan", json=input_body).status_code, 409)
        row["data"]["shots"] = 0
        self.assertEqual(self.client.post("/api/cases", json=row).status_code, 400)

    def test_plan_rejects_invalid_parameters_and_duplicate_case_ids(self):
        body = self.body()
        self.assertEqual(self.client.post("/api/plan", json=body).status_code, 200)
        body["limits"]["max_shots"] = 1
        self.assertEqual(self.client.post("/api/plan", json=body).status_code, 400)
        body = self.body()
        body["cases"] *= 2
        self.assertEqual(self.client.post("/api/plan", json=body).status_code, 400)

    def test_malformed_ids_return_contract_errors(self):
        for value in (None, [], {}, 42):
            body = self.body()
            body["device_id"] = value
            self.assertEqual(self.client.post("/api/plan", json=body).status_code, 400)

    def test_atomic_json_retries_only_transient_windows_file_lock(self):
        original = Path.replace
        attempts = []
        def temporary_lock(path, target):
            attempts.append(target)
            if len(attempts) < 3:
                raise PermissionError("file is open in the monitor")
            return original(path, target)
        target = self.root / "atomic.json"
        with patch("qtest_bench.contracts.os.name", "nt"), patch.object(Path, "replace", temporary_lock):
            write_json(target, {"complete": True})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(read_json(target), {"complete": True})
        self.assertFalse(target.with_suffix(".json.tmp").exists())

    def test_live_polling_does_not_interrupt_worker_file_updates(self):
        task = self.submit(self.body(all_cases=True))
        claimed = claim(self.store)
        thread = threading.Thread(target=execute_task, args=(self.store, claimed))
        thread.start()
        polls = 0
        try:
            while thread.is_alive():
                response = self.client.get(f"/api/tasks/{task['id']}")
                self.assertEqual(response.status_code, 200, response.text)
                polls += 1
                time.sleep(0.002)
        finally:
            thread.join(35)
        self.assertGreater(polls, 0)
        self.assertEqual(self.store.one("tasks", task["id"])["status"], "succeeded")
        self.assertFalse(list(self.store.task_dir(task["id"]).rglob("*.tmp")))

    def test_idempotency_and_conflicting_submission(self):
        a = self.submit()
        b = self.submit()
        self.assertEqual(a["id"], b["id"])
        body = self.body()
        body["name"] = "different"
        response = self.client.post("/api/tasks", json=body, headers={"Idempotency-Key": "test-idempotency-key-0001"})
        self.assertEqual(response.status_code, 409)

    def test_simultaneous_duplicate_submissions_only_enqueue_once(self):
        body = self.body()
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.store.enqueue(body, self.admin["id"], "concurrent-idempotency-1")["id"], range(4)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.client.get("/api/tasks").json()), 1)

    def test_owner_cancellation_and_queued_no_device_call(self):
        task = self.submit()
        self.signin("tester")
        self.assertEqual(self.client.post(f"/api/tasks/{task['id']}/cancel", json={}).status_code, 403)
        self.signin("admin")
        self.assertEqual(self.client.post(f"/api/tasks/{task['id']}/cancel", json={}).status_code, 200)
        with patch("qtest_bench.web.worker.load_adapter", side_effect=AssertionError("Must not submit")):
            run_worker(self.root, once=True)
        self.assertEqual(self.store.one("tasks", task["id"])["status"], "cancelled")

    def test_worker_all_protocols_results_download_and_compare(self):
        ids = []
        for key in ("first-e2e-idempotency-1", "second-e2e-idempotency-2"):
            task = self.submit(self.body(all_cases=True), key)
            ids.append(task["id"])
            self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/result").status_code, 409)
            run_worker(self.root, once=True)
            detail = self.client.get(f"/api/tasks/{task['id']}").json()
            self.assertEqual(detail["status"], "succeeded", detail["message"])
            self.assertEqual(detail["progress"]["completed"], 10)
            self.assertEqual(len(detail["artifacts"]), 3)
            result = self.client.get(f"/api/tasks/{task['id']}/result").json()
            self.assertTrue(result["summary"]["verification"]["bound_to_expected_suite"])
            self.assertEqual(result["resources"]["jobs"], 48)
            for name in detail["artifacts"]:
                response = self.client.get(f"/api/tasks/{task['id']}/artifacts/{name}")
                self.assertEqual(response.status_code, 200)
                self.assertIn("attachment", response.headers["content-disposition"])
            self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/artifacts/qtest.sqlite3").status_code, 404)
        comparison = self.client.post("/api/comparisons", json={"name": "comparison", "task_ids": ids}, headers={"Idempotency-Key": "compare-e2e-idempotency"}).json()
        run_worker(self.root, once=True)
        result = self.client.get(f"/api/tasks/{comparison['id']}/result").json()
        self.assertEqual(result["format"], "qtest-comparison-2")
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(len(result["groups"]), 5)

    def test_running_cancel_keeps_evidence_and_blocks_device(self):
        seen = threading.Event()
        class Waiting(LocalAdapter):
            def status(self, job_id):
                seen.set()
                return "queued"
        task = self.submit()
        claimed = claim(self.store)
        with patch("qtest_bench.web.worker.load_adapter", return_value=Waiting()):
            thread = threading.Thread(target=execute_task, args=(self.store, claimed))
            thread.start()
            try:
                self.assertTrue(seen.wait(10))
                live = self.client.get(f"/api/tasks/{task['id']}").json()
                self.assertIn(live["progress"]["latest_job"]["state"], ("submitted", "cancelled"))
                self.client.post(f"/api/tasks/{task['id']}/cancel", json={})
            finally:
                thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.store.one("tasks", task["id"])["status"], "cancelled")
        self.assertTrue(self.store.one("devices", "local")["blocked"])
        summary = verify_files(read_run(self.store.task_dir(task["id"]) / "run"))
        self.assertEqual(summary["cases"][0]["status"], "cancelled")
        self.assertEqual(self.client.post("/api/plan", json=self.body()).status_code, 400)
        self.assertEqual(self.client.post("/api/devices/local/resolve", json={"note": "Checked: provider job is cancelled"}).status_code, 200)
        self.assertEqual(self.store.one("devices", "local")["blocked"], "")

    def test_recovery_does_not_resubmit_uncertain_work(self):
        first = self.submit()
        second = self.submit(key="second-queued-task-123")
        claimed = claim(self.store)
        self.assertEqual(claimed["id"], first["id"])
        with exclusive_worker(self.root):
            recover(self.store)
            self.assertIsNone(claim(self.store))
        self.assertEqual(self.store.one("tasks", first["id"])["status"], "interrupted")
        self.assertEqual(self.store.one("tasks", second["id"])["status"], "queued")

    def test_exclusive_worker_lock(self):
        with exclusive_worker(self.root):
            with self.assertRaises(QTestError):
                with exclusive_worker(self.root):
                    pass

    def test_device_config_path_and_credentials_not_exposed(self):
        with self.store.db() as conn:
            conn.execute("UPDATE devices SET config_path=? WHERE id='local'", ("C:/secrets/credential.json",))
        task = self.submit()
        for path in ("/api/overview", "/api/devices", f"/api/tasks/{task['id']}"):
            response = self.client.get(path)
            self.assertNotIn("config_path", response.text)
            self.assertNotIn("C:/secrets", response.text)

    def test_user_disable_revokes_session_and_last_admin_is_protected(self):
        viewer = TestClient(self.app, base_url=ORIGIN, headers=HEADERS)
        try:
            viewer.post("/api/login", json={"username": "reader", "password": PASSWORD})
            response = self.client.put("/api/users/"+self.viewer["id"], json={"role": "viewer", "enabled": False})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(viewer.get("/api/me").status_code, 401)
            self.assertEqual(self.client.put("/api/users/"+self.admin["id"], json={"role": "viewer", "enabled": True}).status_code, 400)
        finally:
            viewer.close()

    def test_login_rate_limit_is_persistent(self):
        self.client.cookies.clear()
        for _ in range(20):
            response = self.client.post("/api/login", json={"username": "absent", "password": "wrong"})
            self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/login", json={"username": "admin", "password": PASSWORD})
        self.assertEqual(response.status_code, 403)
        self.assertIn("5 分钟", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
