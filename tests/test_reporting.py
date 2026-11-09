from __future__ import annotations

import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest

from qtest_bench.adapter import LocalAdapter
from qtest_bench.bundles import read_run, read_suite, write_bundle
from qtest_bench.cli import main
from qtest_bench.compare import build_comparison, csv_cell, execution_profile, render_comparison, write_comparison
from qtest_bench.contracts import QTestError, canonical, digest, write_json
from qtest_bench.engine import run_suite
from qtest_bench.report import usage
from qtest_bench.report_io import embedded, inspect_report, make_report, read_report, write_report
from qtest_bench.templates import write_templates

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/smoke-suite.json"


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.suite = read_suite(EXAMPLE)
        for case in cls.suite["cases"]:
            case["shots"] = 64
            if "iterations" in case["parameters"]:
                case["parameters"]["iterations"] = 2
        run_suite(cls.suite, LocalAdapter(), Path(cls.fixture.name) / "run", "report-test")
        cls.files = read_run(Path(cls.fixture.name) / "run")
        cls.artifact, cls.summary = make_report(cls.files, cls.suite)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def artifact_copy(self, run_id):
        artifact = copy.deepcopy(self.artifact)
        artifact["payload"]["evidence"]["run.json"]["run_id"] = run_id
        return artifact

    def save(self, artifact, name):
        artifact["sha256"] = digest(artifact["payload"])
        path = self.root / name
        if path.suffix == ".md":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# QTest 单机详细测试报告\n\n" + embedded(artifact), encoding="utf-8")
        else:
            write_json(path, artifact)
        return path

    def test_markdown_and_json_each_work_standalone(self):
        paths = write_report(self.files, self.root / "one.md", self.suite)
        for name in ("report", "json"):
            report = read_report(paths[name])
            verified = inspect_report(report, self.suite)
            self.assertEqual(verified["run_id"], self.summary["run_id"])
            self.assertTrue(verified["verification"]["bound_to_expected_suite"])
        content = Path(paths["report"]).read_text(encoding="utf-8")
        for section in ("冻结的原问题", "训练轨迹", "演化轨迹", "逐作业证据", "最小", "接受 shots"):
            self.assertIn(section, content)

    def test_report_claim_is_not_external_trust(self):
        result = inspect_report(self.artifact)
        self.assertTrue(result["verification"]["report_claimed_suite_binding"])
        self.assertFalse(result["verification"]["bound_to_expected_suite"])

    def test_corrupt_hash_and_resigned_metric_tamper_rejected(self):
        bad = copy.deepcopy(self.artifact)
        bad["sha256"] = "wrong"
        with self.assertRaisesRegex(QTestError, "payload hash"):
            inspect_report(bad)
        bad = copy.deepcopy(self.artifact)
        bad["payload"]["evidence"]["run.json"]["cases"][1]["metrics"]["mean_population_mae"] = 99
        bad["sha256"] = digest(bad["payload"])
        with self.assertRaisesRegex(QTestError, "recomputed"):
            inspect_report(bad)

    def test_display_edits_do_not_replace_evidence(self):
        paths = write_report(self.files, self.root / "one.md", self.suite)
        path = Path(paths["report"])
        text = path.read_text(encoding="utf-8").replace("# QTest 单机详细测试报告", "# An edited display title")
        path.write_text(text, encoding="utf-8")
        self.assertEqual(inspect_report(read_report(path))["run_id"], self.summary["run_id"])

    def test_legacy_and_duplicate_evidence_blocks_rejected(self):
        legacy = self.root / "legacy.md"
        legacy.write_text("# QTest 测试报告\n\n| score | 1 |", encoding="utf-8")
        with self.assertRaises(QTestError):
            read_report(legacy)
        path = self.root / "duplicate.md"
        path.write_text(embedded(self.artifact) * 2, encoding="utf-8")
        with self.assertRaises(QTestError):
            read_report(path)

    def test_same_run_duplicates_count_once(self):
        write_report(self.files, self.root / "one.md", self.suite)
        report = build_comparison([self.root], expected_suite=self.suite)
        self.assertEqual(len(report["sources"]), 1)
        self.assertEqual(len(report["import_summary"]["duplicates"]), 1)
        self.assertEqual(len(report["groups"]), 6)

    def test_compatible_runs_compare_with_correct_metric_directions(self):
        self.save(self.artifact_copy("run-a"), "a.report.json")
        self.save(self.artifact_copy("run-b"), "b.md")
        report = build_comparison([self.root], expected_suite=self.suite)
        self.assertEqual(len(report["groups"]), 6)
        vqe = next(g for g in report["groups"] if g["benchmark"] == "application.vqe_toy.v1")
        self.assertIn("absolute_energy_error", vqe["observed_leaders"])
        self.assertNotIn("energy", vqe["observed_leaders"])
        self.assertNotIn("reference_ground_energy", vqe["observed_leaders"])
        self.assertEqual(set(vqe["observed_leaders"]["absolute_energy_error"]["observed_best_run_ids"]), {"run-a", "run-b"})
        material = next(g for g in report["groups"] if g["benchmark"] == "application.material_dynamics.v1")
        self.assertIn("mean_structure_factor_mae", material["observed_leaders"])
        self.assertIn("只有一个声明设备身份", render_comparison(report))

    def test_different_budget_splits_groups(self):
        self.save(self.artifact_copy("base"), "a.md")
        other = self.artifact_copy("different-budget")
        evidence = other["payload"]["evidence"]
        evidence["suite.json"]["limits"]["max_shots"] += 1
        evidence["run.json"]["suite_sha256"] = digest(evidence["suite.json"])
        other["payload"]["declared_expected_suite_sha256"] = None
        self.save(other, "b.report.json")
        report = build_comparison([self.root])
        self.assertEqual(len(report["groups"]), 12)
        self.assertIn("limits", render_comparison(report))

    def test_qpu_and_simulator_do_not_mix(self):
        self.save(self.artifact_copy("simulation"), "a.md")
        other = self.artifact_copy("self-declared-qpu-test-fixture")
        other["payload"]["evidence"]["run.json"]["device"]["kind"] = "qpu"
        self.save(other, "b.md")
        report = build_comparison([self.root])
        self.assertEqual(len(report["groups"]), 12)
        self.assertTrue(all(len({e["device_kind"] for e in g["entries"]}) == 1 for g in report["groups"]))

    def test_missing_profile_prevents_best_observed_summary(self):
        for run in ("a", "b"):
            artifact = self.artifact_copy(run)
            for name, record in artifact["payload"]["evidence"].items():
                if name.startswith("observations/"):
                    record["result"]["provenance"].pop("mitigation")
                    record["result_sha256"] = digest(record["result"])
            self.save(artifact, run + ".md")
        report = build_comparison([self.root])
        self.assertTrue(all(not g["observed_leaders"] for g in report["groups"]))
        self.assertTrue(all(not e["profile_complete"] for g in report["groups"] for e in g["entries"]))

    def test_conflicting_run_id_rejects_all_instead_of_picking_first(self):
        self.save(copy.deepcopy(self.artifact), "a.md")
        conflict = copy.deepcopy(self.artifact)
        conflict["payload"]["evidence"]["run.json"]["operator"] = "different"
        self.save(conflict, "b.md")
        with self.assertRaisesRegex(QTestError, "Conflicting copies"):
            build_comparison([self.root])

    def test_rejected_input_is_reported_and_strict_has_no_outputs(self):
        self.save(copy.deepcopy(self.artifact), "valid.md")
        (self.root / "invalid.report.json").write_text("not-json", encoding="utf-8")
        report = build_comparison([self.root])
        self.assertEqual(len(report["import_summary"]["rejected"]), 1)
        self.assertIn("部分导入", render_comparison(report))
        with self.assertRaisesRegex(QTestError, "Strict import"):
            write_comparison([self.root], self.root / "strict.md", strict=True)
        self.assertFalse((self.root / "strict.md").exists())
        self.assertFalse((self.root / "strict.comparison.json").exists())

    def test_recursive_scan_and_comparison_outputs_not_reimported(self):
        self.save(self.artifact_copy("a"), "nested/a.md")
        self.save(self.artifact_copy("b"), "nested/b.report.json")
        output = self.root / "combined.md"
        result = write_comparison([self.root], output, recursive=True, expected_suite=self.suite)
        self.assertEqual(result["runs"], 2)
        self.assertTrue(Path(result["json"]).exists())
        self.assertIn("absolute_energy_error", Path(result["csv"]).read_text(encoding="utf-8-sig"))
        second = write_comparison([self.root], output, recursive=True, expected_suite=self.suite, force=True)
        self.assertEqual(second["runs"], 2)

    def test_compare_accepts_run_directory_and_zip(self):
        bundle = self.root / "result.zip"
        write_bundle(bundle, "result", self.files)
        report = build_comparison([Path(self.fixture.name) / "run", bundle], expected_suite=self.suite)
        self.assertEqual(len(report["sources"]), 1)
        self.assertEqual(len(report["import_summary"]["duplicates"]), 1)

    def test_unsupported_retains_coverage_without_zero_scores(self):
        class Partial(LocalAdapter):
            def describe(self):
                return dict(super().describe(), capabilities=["gate.sample.v1"])
        run_suite(self.suite, Partial(), self.root / "partial", "partial-lab")
        report = build_comparison([self.root / "partial"], expected_suite=self.suite)
        empty = [e for g in report["groups"] for e in g["entries"] if e["completed"] == 0]
        self.assertEqual(len(empty), 3)
        self.assertTrue(all(e["metrics"] == {} for e in empty))

    def test_unknown_qpu_time_is_missing_not_zero(self):
        jobs = [r for name, r in self.artifact["payload"]["evidence"].items() if name.startswith("observations/")]
        total = usage(jobs)
        self.assertIsNone(total["qpu_seconds"]["total"])
        self.assertEqual(total["qpu_seconds"]["missing"], len(jobs))
        self.assertEqual(total["incomplete_jobs"], 0)
        self.assertEqual(set(total), {
            "jobs", "completed_jobs", "requested_shots", "observed_attempted_shots",
            "observed_accepted_shots", "observed_acceptance", "incomplete_jobs",
            "client_job_wall_seconds", "qpu_seconds", "execution_seconds",
        })

    def test_output_templates_contain_execution_measures_only(self):
        paths = write_report(self.files, self.root / "run.md", self.suite)
        comparison = write_comparison([paths["report"]], self.root / "comparison.md", expected_suite=self.suite)
        write_templates(self.root / "templates")
        outputs = [Path(paths["report"]), Path(comparison["report"]), *self.root.joinpath("templates").glob("*.md")]
        for path in outputs:
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for term in ("费用", "币种", "计费", "未结算", "billing", "settlement"):
                    self.assertNotIn(term, content)
        data = build_comparison([paths["report"]], expected_suite=self.suite)
        self.assertEqual(data["format"], "qtest-comparison-2")
        self.assertEqual(data["sources"][0]["resources"]["requested_shots"], self.summary["resources"]["reserved_shots"])
        self.assertEqual(len(data["groups"]), 6)

    def test_explicit_unknown_mitigation_is_not_a_complete_profile(self):
        record = copy.deepcopy(self.artifact["payload"]["evidence"]["observations/000001.json"])
        record["result"]["provenance"]["mitigation"] = "unknown"
        _, complete = execution_profile([record])
        self.assertFalse(complete)

    def test_malformed_failed_provider_result_is_reportable(self):
        class InvalidResult(LocalAdapter):
            def result(self, job_id):
                return None
        run_suite(self.suite, InvalidResult(), self.root / "failed", "failure-lab")
        files = read_run(self.root / "failed")
        paths = write_report(files, self.root / "failure.md", self.suite)
        report = build_comparison([paths["report"]], expected_suite=self.suite)
        self.assertEqual(report["sources"][0]["resources"]["observed_attempted_shots"], 0)
        self.assertEqual(report["sources"][0]["resources"]["incomplete_jobs"], 1)
        self.assertTrue(all(not e["metrics"] for g in report["groups"] for e in g["entries"]))

    def test_write_protection_and_csv_formula_escaping(self):
        path = self.save(copy.deepcopy(self.artifact), "source.md")
        with self.assertRaisesRegex(QTestError, "Cannot overwrite"):
            write_comparison([self.root], path, force=True)
        self.assertEqual(csv_cell("=HYPERLINK('test')"), "'=HYPERLINK('test')")
        self.assertEqual(csv_cell(-2.5), -2.5)

    def test_cli_returns_partial_exit_code(self):
        self.save(copy.deepcopy(self.artifact), "valid.md")
        (self.root / "bad.report.json").write_text("broken", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(["compare", str(self.root), "--output", str(self.root / "summary.md")])
        self.assertEqual(code, 3)

    def test_cli_execution_commands_removed_and_analysis_kept(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for command in ("run", "plan", "pack", "export"):
                with self.subTest(command=command), self.assertRaises(SystemExit) as caught:
                    main([command, "examples/smoke-suite.json"])
                self.assertEqual(caught.exception.code, 2)
            self.assertEqual(main(["catalog"]), 0)
            self.assertEqual(main(["validate", str(EXAMPLE)]), 0)

    def test_nine_detailed_templates_and_unimplemented_labels(self):
        result = write_templates(self.root / "templates")
        self.assertEqual(result["templates"], 9)
        template = (self.root / "templates/06-material-dynamics.md").read_text(encoding="utf-8")
        self.assertIn("1D Ising", template)
        self.assertNotIn("尚未实现", template)
        self.assertIn("Cij", template)
        self.assertIn("原始请求和观测", template)
        self.assertGreater(len(template), 2500)
        classification = (self.root / "templates/07-classification.md").read_text(encoding="utf-8")
        self.assertIn("尚未实现", classification)


if __name__ == "__main__":
    unittest.main()
