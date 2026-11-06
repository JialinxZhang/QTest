from __future__ import annotations

import copy
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import platform
import time
import uuid

from . import __version__
from .benchmarks import get_benchmark
from .contracts import QTestError, canonical, digest, require, validate_device, validate_measurement, validate_suite, write_json


def now():
    return datetime.now(timezone.utc).isoformat()


def case_seed(suite, case_id, repetition):
    return int(digest([suite["seed"], case_id, repetition])[:8], 16)


def request(workload, shots, seed, index):
    return dict(workload, shots=shots, seed=int(digest([seed, index])[:8], 16))


def plan(suite, device):
    validate_suite(suite)
    validate_device(device)
    cases = []
    for case in suite["cases"]:
        benchmark = get_benchmark(case["benchmark"])
        supported = benchmark.capability in device["capabilities"]
        jobs = benchmark.jobs(case["parameters"]) * case["repetitions"] if supported else 0
        cases.append({"id": case["id"], "supported": supported, "required_capability": benchmark.capability,
                      "jobs": jobs, "requested_shots": jobs * case["shots"]})
    jobs, shots = sum(c["jobs"] for c in cases), sum(c["requested_shots"] for c in cases)
    return {"suite_sha256": digest(suite), "device": device, "cases": cases, "jobs": jobs,
            "requested_shots": shots, "fits_budget": jobs <= suite["limits"]["max_jobs"] and shots <= suite["limits"]["max_shots"]}


class ExecutionStopped(QTestError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Session:
    def __init__(self, adapter, suite, root, summary, row, started, should_cancel=None):
        self.adapter, self.suite, self.root, self.summary, self.row, self.started = adapter, suite, root, summary, row, started
        self.case = next(c for c in suite["cases"] if c["id"] == row["case_id"])
        self.seed = case_seed(suite, self.case["id"], row["repetition"])
        self.should_cancel = should_cancel or (lambda: False)

    def save(self):
        write_json(self.root / "run.json", self.summary)

    def execute(self, work, role):
        limits, totals = self.suite["limits"], self.summary["resources"]
        if self.should_cancel():
            raise ExecutionStopped("cancelled", "Cancellation requested before submission")
        if time.monotonic() - self.started >= limits["wall_seconds"]:
            raise ExecutionStopped("timeout", "Run wall-time budget reached before submission")
        shots = self.case["shots"]
        if totals["reserved_jobs"] + 1 > limits["max_jobs"] or totals["reserved_shots"] + shots > limits["max_shots"]:
            raise ExecutionStopped("budget_exhausted", "Requested job/shot budget reached before submission")
        work = request(work, shots, self.seed, len(self.row["records"]))
        totals["reserved_jobs"] += 1
        totals["reserved_shots"] += shots
        path = f"observations/{totals['reserved_jobs']:06d}.json"
        self.row["records"].append(path)
        record = {"request_id": str(uuid.uuid4()), "role": role, "workload": work,
                  "workload_sha256": digest(work), "created_at": now(), "state": "prepared"}
        write_json(self.root / path, record)
        self.save()  # Durable intent precedes the external submit call.
        begin = time.monotonic()
        job_id = None
        try:
            record["state"] = "submitting"
            write_json(self.root / path, record)
            job_id = self.adapter.submit(copy.deepcopy(work), record["request_id"])
            require(isinstance(job_id, str) and 0 < len(job_id) <= 500, "Adapter returned an invalid job ID")
            record.update(job_id=job_id, state="submitted", submitted_at=now())
            write_json(self.root / path, record)
            while True:
                if self.should_cancel():
                    raise ExecutionStopped("cancelled", "Cancellation requested; provider execution state must be confirmed")
                if time.monotonic() - self.started >= limits["wall_seconds"]:
                    raise ExecutionStopped("timeout", "Wall-time budget reached; provider cancellation requested")
                state = self.adapter.status(job_id)
                require(state in ("queued", "running", "completed", "failed", "cancelled"), "Invalid provider job state")
                if record.get("provider_state") != state:
                    record.update(provider_state=state, observed_at=now())
                    write_json(self.root / path, record)
                if state == "completed":
                    break
                if state in ("failed", "cancelled"):
                    raise ExecutionStopped("failed", f"Provider job ended with state {state}")
                pause_until = time.monotonic() + min(limits["poll_seconds"], max(0, limits["wall_seconds"] - (time.monotonic() - self.started)))
                while time.monotonic() < pause_until and not self.should_cancel():
                    time.sleep(min(0.2, max(0, pause_until - time.monotonic())))
            result = self.adapter.result(job_id)
            # Preserve serializable evidence even when the measurement contract is rejected.
            canonical(result)
            record["result"] = result
            record["result_sha256"] = digest(result)
            validate_measurement(work, result)
            if time.monotonic() - self.started > limits["wall_seconds"]:
                raise ExecutionStopped("timeout", "Result retrieval exceeded run wall-time budget")
            record.update(state="completed", finished_at=now(), client_wall_seconds=time.monotonic() - begin)
            write_json(self.root / path, record)
            return result
        except Exception as exc:
            status = exc.status if isinstance(exc, ExecutionStopped) else "failed"
            # Do not export arbitrary SDK exception text, which may include credentials.
            record.update(state=status, error_type=type(exc).__name__, finished_at=now(),
                          client_wall_seconds=time.monotonic() - begin)
            if job_id is None:
                record["submission_outcome"] = "unknown; confirm request_id execution status with provider before any retry"
            else:
                try:
                    self.adapter.cancel(job_id)
                    record["cancellation"] = "requested; execution stop must be confirmed with provider"
                except Exception:
                    record["cancellation"] = "request_failed; provider execution state unknown"
            write_json(self.root / path, record)
            raise ExecutionStopped(status, f"Job {path}: {type(exc).__name__}; inspect local adapter logs") from None


def run_suite(suite, adapter, output, operator, mode="organizer", *, should_cancel=None):
    require(mode in ("organizer", "vendor"), "Invalid execution mode")
    require(isinstance(operator, str) and 0 < len(operator) <= 200, "Operator label is required")
    device = adapter.describe()
    execution_plan = plan(suite, device)
    require(execution_plan["fits_budget"], "Plan exceeds the configured job/shot budget; revise the suite before running")
    root = Path(output)
    require(not root.exists(), f"Output already exists; choose a new run directory: {root}")
    root.mkdir(parents=True)
    write_json(root / "suite.json", suite)
    summary = {"schema_version": "1.0", "tool_version": __version__, "run_id": str(uuid.uuid4()),
               "suite_sha256": digest(suite), "protocol_version": suite["protocol_version"],
               "environment": {"python": platform.python_version(), "platform": platform.platform(),
                               "numpy": version("numpy"), "jsonschema": version("jsonschema")},
               "operator": operator, "mode": mode, "device": device, "started_at": now(), "status": "running",
               "resources": {"reserved_jobs": 0, "reserved_shots": 0}, "cases": []}
    started = time.monotonic()
    abort = False
    write_json(root / "run.json", summary)
    for case in suite["cases"]:
        benchmark = get_benchmark(case["benchmark"])
        for repetition in range(case["repetitions"]):
            row = {"case_id": case["id"], "benchmark": case["benchmark"], "repetition": repetition,
                   "status": "running", "records": []}
            summary["cases"].append(row)
            if benchmark.capability not in device["capabilities"]:
                row.update(status="unsupported", reason=f"Missing capability {benchmark.capability}")
            elif abort:
                row.update(status="skipped_after_abort", reason="Earlier job execution state needs confirmation")
            else:
                session = Session(adapter, suite, root, summary, row, started, should_cancel)
                try:
                    row["metrics"] = benchmark.run(case["parameters"], session, session.seed)
                    row["status"] = "completed"
                except ExecutionStopped as exc:
                    row.update(status=exc.status, reason=str(exc))
                    abort = True
                except Exception as exc:
                    row.update(status="failed", reason=f"Controller failed ({type(exc).__name__})")
                    abort = True
            write_json(root / "run.json", summary)
    summary.update(status="finished", finished_at=now(), client_wall_seconds=time.monotonic() - started)
    write_json(root / "run.json", summary)
    return summary


class ReplaySession:
    def __init__(self, records, case, seed):
        self.records, self.case, self.seed, self.index = records, case, seed, 0

    def execute(self, work, role):
        require(self.index < len(self.records), "Missing measurement evidence")
        record = self.records[self.index]
        expected = request(work, self.case["shots"], self.seed, self.index)
        require(record.get("role") == role, "Measurement role/order differs from protocol")
        require(record.get("workload") == expected, "Recorded workload differs from independently replayed protocol")
        require(record.get("state") == "completed", "Completed case contains unfinished job")
        self.index += 1
        return validate_measurement(expected, record["result"])


def equivalent(a, b):
    if type(a) in (int, float) and type(b) in (int, float):
        return abs(a - b) <= 1e-10 * max(1, abs(a), abs(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equivalent(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equivalent(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def verify_files(files, expected_suite=None):
    """Independent replay of closed pilot protocols; no device access or bundle code execution."""
    from .contracts import loads
    require("suite.json" in files and "run.json" in files, "Missing suite.json or run.json")
    suite = validate_suite(loads(files["suite.json"]))
    summary = loads(files["run.json"])
    # These releases share the same frozen v1 experiment controllers.
    require(summary.get("schema_version") == "1.0" and summary.get("tool_version") in ("0.1.0", "0.2.0", "0.2.1", "0.3.0", "0.4.0"), "Unsupported evidence/tool version")
    require(summary.get("status") == "finished", "Run incomplete; retain evidence and confirm unfinished job execution states")
    require(summary.get("suite_sha256") == digest(suite), "Suite hash mismatch")
    require(summary.get("protocol_version") == suite["protocol_version"], "Protocol version mismatch")
    if expected_suite is not None:
        require(digest(validate_suite(expected_suite)) == digest(suite), "Result does not match the organizer's expected suite")
    validate_device(summary["device"])
    expected_rows = [(c, i) for c in suite["cases"] for i in range(c["repetitions"])]
    require(len(summary["cases"]) == len(expected_rows), "Missing or extra case repetitions")
    referenced, request_ids, job_ids = set(), set(), set()
    reserved_shots = 0
    for row, (case, repetition) in zip(summary["cases"], expected_rows):
        require((row["case_id"], row["benchmark"], row["repetition"]) == (case["id"], case["benchmark"], repetition), "Case identity/order mismatch")
        records = []
        for path in row["records"]:
            require(path in files and path not in referenced and path.startswith("observations/") and path.endswith(".json"), "Missing, duplicate, or invalid evidence path")
            referenced.add(path)
            record = loads(files[path])
            if record.get("state") == "completed":
                require(isinstance(record.get("job_id"), str) and record["job_id"] and record["job_id"] not in job_ids,
                        "Missing or duplicate provider job_id in completed evidence")
                job_ids.add(record["job_id"])
            require(record["request_id"] not in request_ids, "Duplicate request_id")
            request_ids.add(record["request_id"])
            require(record["workload_sha256"] == digest(record["workload"]), "Workload hash mismatch")
            require(record["workload"].get("shots") == case["shots"], "Shot budget mismatch")
            reserved_shots += record["workload"]["shots"]
            if "result" in record:
                require(record["result_sha256"] == digest(record["result"]), "Result hash mismatch")
            records.append(record)
        if row["status"] == "completed":
            controller = get_benchmark(case["benchmark"])
            require(controller.capability in summary["device"]["capabilities"], "Device lacks claimed completed capability")
            replay = ReplaySession(records, case, case_seed(suite, case["id"], repetition))
            computed = controller.run(case["parameters"], replay, replay.seed)
            require(replay.index == len(records), "Extra measurement evidence beyond protocol")
            require(equivalent(computed, row.get("metrics")), "Submitted metrics differ from independently recomputed metrics")
            row["metrics"] = computed
        else:
            require(row["status"] in ("unsupported", "skipped_after_abort", "failed", "timeout", "budget_exhausted", "cancelled"), "Invalid case status")
            require("metrics" not in row, "Unfinished cases may not carry scores")
            if row["status"] == "unsupported":
                require(get_benchmark(case["benchmark"]).capability not in summary["device"]["capabilities"] and not records, "False unsupported status")
            if row["status"] == "skipped_after_abort":
                require(not records, "Skipped case may not contain jobs")
    require(referenced == {p for p in files if p.startswith("observations/")}, "Unaccounted observation files")
    require(summary["resources"] == {"reserved_jobs": len(referenced), "reserved_shots": reserved_shots}, "Reserved resource totals mismatch")
    require(len(referenced) <= suite["limits"]["max_jobs"] and reserved_shots <= suite["limits"]["max_shots"], "Recorded run exceeded budget")
    summary["verification"] = {"completed_cases_replayed": True, "bound_to_expected_suite": expected_suite is not None,
                               "device_identity": "self_declared; not independently authenticated",
                               "failed_cases": "retained, not scored; confirm provider job execution state"}
    return summary
