from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
import warnings
import zipfile

import numpy as np

from qtest_bench.adapter import LocalAdapter
from qtest_bench.benchmarks import get_benchmark, mirror_circuit, run_mirror
from qtest_bench.bundles import read_bundle, read_run, read_suite, write_bundle
from qtest_bench.contracts import QTestError, canonical, digest, loads, validate_measurement, validate_suite
from qtest_bench.engine import ExecutionStopped, Session, plan, run_suite, verify_files
from qtest_bench.numerics import analog_probabilities, chain_structure_factor, fock_probabilities, gate_probabilities, sample, spin_correlations
from qtest_bench.report import render

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/smoke-suite.json"


def suite():
    s = read_suite(EXAMPLE)
    for c in s["cases"]:
        c["repetitions"] = 1
        c["shots"] = 128
        if "iterations" in c["parameters"]:
            c["parameters"]["iterations"] = 2
    return s


class ReferenceTests(unittest.TestCase):
    def test_gate_endianness_and_bell(self):
        work = {"n_qubits": 2, "gates": [{"name": "x", "qubits": [0]}]}
        self.assertAlmostEqual(gate_probabilities(work)["10"], 1)
        work["gates"] = [{"name": "h", "qubits": [0]}, {"name": "cx", "qubits": [0, 1]}]
        p = gate_probabilities(work)
        self.assertAlmostEqual(p["00"], 0.5)
        self.assertAlmostEqual(p["11"], 0.5)
        self.assertAlmostEqual(p["01"] + p["10"], 0)

    def test_rotations_and_control_direction(self):
        p = gate_probabilities({"n_qubits": 2, "gates": [{"name": "ry", "qubits": [1], "angle": np.pi}, {"name": "cx", "qubits": [1, 0]}]})
        self.assertAlmostEqual(p["11"], 1)
        p = gate_probabilities({"n_qubits": 1, "gates": [{"name": "h", "qubits": [0]}, {"name": "rz", "qubits": [0], "angle": np.pi}, {"name": "h", "qubits": [0]}]})
        self.assertAlmostEqual(p["1"], 1)

    def test_single_qubit_analytic_evolution(self):
        p = analog_probabilities({"n_qubits": 1, "hx": [1], "hz": [0], "edges": [], "initial": "0", "time": 0.37})
        self.assertAlmostEqual(p["1"], np.sin(0.37)**2)

    def test_mirror_circuit_returns_to_initial_state_ideally(self):
        p = {"n_qubits": 4, "depths": [2, 5, 9], "circuit_seed": 20261109, "success_threshold": 0.75}
        for depth in (1, 2, 5, 9, 20):
            probabilities = gate_probabilities(mirror_circuit(p, depth))
            self.assertAlmostEqual(probabilities["0000"], 1)
        self.assertEqual(mirror_circuit(p, 5), mirror_circuit(p, 5))

    def test_mirror_max_stable_depth_requires_contiguous_prefix(self):
        class StubSession:
            def __init__(self):
                self.probabilities, self.index = [0.75, 0.5, 0.9375], 0
            def execute(self, work, role):
                p = self.probabilities[self.index]
                self.index += 1
                good, rest = round(p * 512), 512 - round(p * 512)
                counts = {"0000": good}
                if rest:
                    counts["1111"] = rest
                return {"counts": counts, "attempted_shots": 512, "accepted_shots": 512,
                        "resources": {"qpu_seconds": None, "execution_seconds": None, "timing_scope": "stub"},
                        "provenance": {"model": "stub"}}
        p = {"n_qubits": 4, "depths": [2, 5, 9], "circuit_seed": 1, "success_threshold": 0.7}
        metrics = run_mirror(p, StubSession(), 0)
        self.assertEqual(metrics["max_stable_depth"], 2)  # depth 5 fails; the later 0.9375 must not repair it
        self.assertAlmostEqual(metrics["mean_success_probability"], (0.75 + 0.5 + 0.9375) / 3)

    def test_hong_ou_mandel_bunching(self):
        p = fock_probabilities(suite()["cases"][2]["parameters"])
        self.assertAlmostEqual(p["1,1"], 0)
        self.assertAlmostEqual(p["2,0"], 0.5)
        self.assertAlmostEqual(p["0,2"], 0.5)

    def test_connected_correlations_vanish_on_product_eigenstate(self):
        work = {"n_qubits": 2, "hx": [0, 0], "hz": [0, 0], "edges": [[0, 1, 1]], "initial": "00", "time": 0.9}
        correlations = spin_correlations(analog_probabilities(work), 2)
        self.assertTrue(all(abs(value) < 1e-12 for row in correlations for value in row))
        self.assertTrue(all(abs(value) < 1e-12 for value in chain_structure_factor(correlations, [0.0, np.pi])))

    def test_sampled_correlations_and_structure_factor_match_exact(self):
        work = {"n_qubits": 4, "hx": [0.6, 0.6, 0.6, 0.6], "hz": [0.1, 0.1, 0.1, 0.1],
                "edges": [[0, 1, 0.8], [1, 2, 0.8], [2, 3, 0.8]], "initial": "0000", "time": 1.2}
        exact = analog_probabilities(work)
        counts = sample(exact, 200000, 20261102)
        measured = spin_correlations({k: v / 200000 for k, v in counts.items()}, 4)
        reference = spin_correlations(exact, 4)
        self.assertGreater(max(abs(reference[i][j]) for i in range(4) for j in range(4) if i != j), 0.05)
        self.assertLess(max(abs(measured[i][j] - reference[i][j]) for i in range(4) for j in range(4)), 0.01)
        wavevectors = [2 * np.pi * k / 4 for k in range(4)]
        sampled_s = chain_structure_factor(measured, wavevectors)
        exact_s = chain_structure_factor(reference, wavevectors)
        self.assertLess(max(abs(a - b) for a, b in zip(sampled_s, exact_s)), 0.02)
        self.assertAlmostEqual(sampled_s[1], sampled_s[3], places=10)  # S(q_k) = S(q_{n-k}) for real C_ij

    def test_identity_optics(self):
        p = fock_probabilities({"unitary_real": [[1, 0], [0, 1]], "unitary_imag": [[0, 0], [0, 0]], "input_occupation": [2, 1]})
        self.assertAlmostEqual(p["2,1"], 1)
        self.assertAlmostEqual(sum(p.values()), 1)


class ContractTests(unittest.TestCase):
    def test_integer_fields_reject_decimal_json_representation(self):
        s = suite()
        s["cases"][0]["repetitions"] = 1.0
        with self.assertRaises(QTestError):
            validate_suite(s)

    def test_invalid_json_numbers_and_duplicates(self):
        for data in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}'):
            with self.subTest(data=data), self.assertRaises(QTestError):
                loads(data)

    def test_nonunitary_and_graph_errors(self):
        s = suite()
        s["cases"][2]["parameters"]["unitary_real"][0][0] = 0.1
        with self.assertRaises(QTestError):
            validate_suite(s)
        s = suite()
        s["cases"][3]["parameters"]["edges"].append([0, 1])
        with self.assertRaises(QTestError):
            validate_suite(s)

    def test_measurement_counts_and_resource_semantics(self):
        work = {"kind": "gate.sample.v1", "n_qubits": 2, "shots": 10}
        good = {"counts": {"00": 8}, "attempted_shots": 10, "accepted_shots": 8,
                "resources": {"qpu_seconds": None, "execution_seconds": None, "timing_scope": "unknown"}, "provenance": {}}
        validate_measurement(work, good)
        for field, value in (("counts", {"00": 9}), ("counts", {"2": 8}), ("attempted_shots", 12), ("accepted_shots", True)):
            bad = dict(good, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(QTestError):
                validate_measurement(work, bad)

    def test_plan_counts_all_training_and_final_calls(self):
        p = plan(suite(), LocalAdapter().describe())
        self.assertEqual(p["jobs"], 3 + 3 + 1 + 3 + 6 + 2)
        self.assertEqual(p["requested_shots"], 18 * 128)

    def test_material_dynamics_requires_two_spins(self):
        s = suite()
        s["cases"][4]["parameters"] = {"n_qubits": 1, "hx": [0.5], "hz": [0], "edges": [], "initial": "0", "times": [0.5]}
        with self.assertRaises(QTestError):
            validate_suite(s)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def complete(self, name="run"):
        s = suite()
        result = run_suite(s, LocalAdapter(), self.root / name, "test-lab")
        return s, result, read_run(self.root / name)

    def test_end_to_end_independent_replay_and_report(self):
        s, result, files = self.complete()
        verified = verify_files(files, s)
        self.assertTrue(all(c["status"] == "completed" for c in result["cases"]))
        self.assertEqual(result["cases"][3]["metrics"]["exact_optimum"], 2)
        self.assertIn("本地模拟验证，不是真机成绩", render(verified))
        self.assertEqual(result["resources"]["reserved_jobs"], 18)
        mirror = result["cases"][0]["metrics"]
        self.assertEqual(mirror["max_stable_depth"], 9)
        self.assertEqual(len(result["cases"][0]["records"]), 3)
        self.assertEqual(len(result["cases"][4]["records"]), 6)
        material = result["cases"][5]["metrics"]
        self.assertIn("mean_correlation_mae", material)
        self.assertIn("mean_structure_factor_mae", material)
        self.assertEqual(len(result["cases"][5]["records"]), 2)

    def test_same_seed_reproduce_metrics_without_mode_field(self):
        _, first, _ = self.complete("first")
        _, second, files = self.complete("second")
        self.assertEqual([c["metrics"] for c in first["cases"]], [c["metrics"] for c in second["cases"]])
        summary = verify_files(files, suite())
        self.assertNotIn("mode", summary)
        self.assertEqual(summary["operator"], "test-lab")

    def test_summary_tampering_rejected(self):
        s, result, files = self.complete()
        result["cases"][3]["metrics"]["best_feasible_size"] = 99
        files["run.json"] = canonical(result)
        with self.assertRaisesRegex(QTestError, "recomputed"):
            verify_files(files, s)

    def test_raw_count_tampering_rejected(self):
        s, result, files = self.complete()
        name = result["cases"][1]["records"][0]
        record = loads(files[name])
        record["result"]["counts"]["000"] += 1
        record["result_sha256"] = digest(record["result"])
        files[name] = canonical(record)
        with self.assertRaises(QTestError):
            verify_files(files, s)

    def test_missing_final_evidence_rejected(self):
        s, result, files = self.complete()
        del files[result["cases"][-1]["records"][-1]]
        with self.assertRaises(QTestError):
            verify_files(files, s)

    def test_changed_suite_cannot_bind_to_organizer_copy(self):
        s, result, files = self.complete()
        changed = copy.deepcopy(s)
        changed["seed"] += 1
        files["suite.json"] = canonical(changed)
        result["suite_sha256"] = digest(changed)
        files["run.json"] = canonical(result)
        with self.assertRaisesRegex(QTestError, "expected suite"):
            verify_files(files, s)

    def test_wrong_role_even_with_consistent_hashes_rejected(self):
        s, result, files = self.complete()
        name = result["cases"][3]["records"][-1]
        record = loads(files[name])
        record["role"] = "train:999"
        files[name] = canonical(record)
        with self.assertRaisesRegex(QTestError, "role/order"):
            verify_files(files, s)

    def test_unsupported_is_not_scored(self):
        class Partial(LocalAdapter):
            def describe(self):
                return dict(super().describe(), capabilities=["gate.sample.v1"])
        s = suite()
        result = run_suite(s, Partial(), self.root / "partial", "lab")
        self.assertEqual([c["status"] for c in result["cases"]], ["completed", "unsupported", "unsupported", "completed", "completed", "unsupported"])
        verify_files(read_run(self.root / "partial"), s)

    def test_ambiguous_submission_never_retries_and_is_durable(self):
        class Broken(LocalAdapter):
            calls = 0
            def submit(self, workload, request_id):
                self.calls += 1
                raise RuntimeError("secret-api-key-DO-NOT-EXPORT")
        adapter = Broken()
        result = run_suite(suite(), adapter, self.root / "broken", "lab")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(result["cases"][0]["status"], "failed")
        files = read_run(self.root / "broken")
        self.assertNotIn(b"secret-api-key", b"".join(files.values()))
        record = loads(files[result["cases"][0]["records"][0]])
        self.assertIn("unknown", record["submission_outcome"])
        verify_files(files, suite())

    def test_timeout_preserves_job_id_and_requests_cancel(self):
        class Queued(LocalAdapter):
            cancelled = False
            def submit(self, workload, request_id):
                return "provider-job-1"
            def status(self, job_id):
                return "queued"
            def cancel(self, job_id):
                self.cancelled = True
        s = suite()
        s["limits"]["wall_seconds"] = 0.08
        adapter = Queued()
        result = run_suite(s, adapter, self.root / "timeout", "lab")
        self.assertTrue(adapter.cancelled)
        self.assertEqual(result["cases"][0]["status"], "timeout")
        record = loads(read_run(self.root / "timeout")[result["cases"][0]["records"][0]])
        self.assertEqual(record["job_id"], "provider-job-1")
        self.assertEqual(record["cancellation"], "requested; execution stop must be confirmed with provider")

    def test_failed_cancel_retains_unknown_execution_state(self):
        class CancelFailure(LocalAdapter):
            def submit(self, workload, request_id):
                return "running-job"
            def status(self, job_id):
                return "running"
            def cancel(self, job_id):
                raise RuntimeError("provider unavailable")
        s = suite()
        s["limits"]["wall_seconds"] = 0.08
        result = run_suite(s, CancelFailure(), self.root / "cancel-failed", "lab")
        files = read_run(self.root / "cancel-failed")
        record = loads(files[result["cases"][0]["records"][0]])
        self.assertEqual(record["cancellation"], "request_failed; provider execution state unknown")
        self.assertEqual(result["cases"][0]["status"], "timeout")
        self.assertEqual(result["resources"]["reserved_jobs"], 1)
        self.assertEqual(result["resources"]["reserved_shots"], s["cases"][0]["shots"])
        self.assertEqual(result["cases"][1]["status"], "skipped_after_abort")
        verify_files(files, s)

    def test_zero_accepted_samples_cannot_be_scored(self):
        class Empty(LocalAdapter):
            def result(self, job_id):
                return dict(super().result(job_id), counts={}, accepted_shots=0)
        result = run_suite(suite(), Empty(), self.root / "empty", "lab")
        self.assertEqual(result["cases"][0]["status"], "failed")
        self.assertNotIn("metrics", result["cases"][0])
        verify_files(read_run(self.root / "empty"), suite())

    def test_duplicate_job_identity_rejected(self):
        s, result, files = self.complete()
        first = loads(files[result["cases"][0]["records"][0]])
        second_name = result["cases"][0]["records"][1]
        second = loads(files[second_name])
        second["job_id"] = first["job_id"]
        files[second_name] = canonical(second)
        with self.assertRaisesRegex(QTestError, "duplicate provider job_id"):
            verify_files(files, s)

    def test_budget_preflight_and_runtime_guard(self):
        s = suite()
        s["limits"]["max_jobs"] = 1
        with self.assertRaisesRegex(QTestError, "exceeds"):
            run_suite(s, LocalAdapter(), self.root / "budget", "lab")
        self.assertFalse((self.root / "budget").exists())
        summary = {"resources": {"reserved_jobs": 1, "reserved_shots": 128}}
        row = {"case_id": s["cases"][0]["id"], "repetition": 0, "records": []}
        session = Session(LocalAdapter(), s, self.root, summary, row, time.monotonic())
        with self.assertRaises(ExecutionStopped):
            session.execute({"kind": "analog.evolve.v1"}, "test")
        self.assertEqual(row["records"], [])

    def test_no_overwrite(self):
        self.complete()
        with self.assertRaises(QTestError):
            self.complete()

    def test_task_and_result_bundle_roundtrip(self):
        s, _, files = self.complete()
        write_bundle(self.root / "task.zip", "task", {"suite.json": canonical(s)})
        self.assertEqual(read_suite(self.root / "task.zip"), s)
        write_bundle(self.root / "result.zip", "result", files)
        verified = verify_files(read_run(self.root / "result.zip"), s)
        self.assertTrue(verified["verification"]["bound_to_expected_suite"])

    def test_archive_traversal_and_unlisted_file_rejected(self):
        for name in ("../escape.json", "hidden.py", "C:/escape.json"):
            target = self.root / (str(abs(hash(name))) + ".zip")
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("manifest.json", canonical({"format": "qtest-bundle-1", "kind": "task", "files": {}}))
                archive.writestr(name, "{}")
            with self.subTest(name=name), self.assertRaises(QTestError):
                read_bundle(target, "task")

    def test_archive_hash_and_duplicate_entries_rejected(self):
        target = self.root / "hash.zip"
        manifest = {"format": "qtest-bundle-1", "kind": "task", "files": {"suite.json": "incorrect"}}
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("manifest.json", canonical(manifest))
            archive.writestr("suite.json", canonical(suite()))
        with self.assertRaisesRegex(QTestError, "hash mismatch"):
            read_bundle(target, "task")
        target = self.root / "duplicate.zip"
        with warnings.catch_warnings(), zipfile.ZipFile(target, "w") as archive:
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("manifest.json", canonical(manifest))
            archive.writestr("suite.json", "{}")
            archive.writestr("suite.json", "{}")
        with self.assertRaisesRegex(QTestError, "Duplicate archive"):
            read_bundle(target, "task")


if __name__ == "__main__":
    unittest.main()
