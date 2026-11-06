"""Portable reports carry the original JSON evidence, never executable code."""
from __future__ import annotations

import base64
import binascii
from pathlib import Path

from .bundles import check_files
from .contracts import canonical, digest, keys, loads, require, write_json
from .engine import verify_files

FORMAT = "qtest-report-1"
START = "<!-- QTEST_REPORT_V1\n"
END = "\nQTEST_REPORT_END -->"
MAX_REPORT_BYTES = 80 * 1024 * 1024


def make_report(files, expected_suite=None):
    summary = verify_files(files, expected_suite)
    payload = {"evidence": {name: loads(data) for name, data in sorted(files.items())},
               "declared_expected_suite_sha256": digest(expected_suite) if expected_suite is not None else None}
    artifact = {"format": FORMAT, "payload": payload, "sha256": digest(payload)}
    return artifact, summary


def inspect_report(artifact, expected_suite=None):
    keys(artifact, ["format", "payload", "sha256"])
    require(artifact["format"] == FORMAT, "Unsupported report format")
    payload = artifact["payload"]
    keys(payload, ["evidence", "declared_expected_suite_sha256"])
    require(digest(payload) == artifact["sha256"], "Report payload hash mismatch")
    require(isinstance(payload["evidence"], dict), "Invalid report evidence map")
    files = {name: canonical(value) for name, value in payload["evidence"].items()}
    check_files("result", files)
    summary = verify_files(files, expected_suite)
    declared = payload["declared_expected_suite_sha256"]
    require(declared is None or declared == summary["suite_sha256"], "Declared suite hash mismatch")
    summary["verification"]["report_claimed_suite_binding"] = declared is not None
    # Only an expected suite supplied by the current verifier establishes external binding.
    return summary


def embedded(artifact):
    return START + base64.b64encode(canonical(artifact)).decode("ascii") + END + "\n"


def read_report(path):
    path = Path(path)
    require(path.stat().st_size <= MAX_REPORT_BYTES, "Report exceeds size limit")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        return loads(text)
    require(text.count(START) == 1 and text.count(END) == 1,
            "Markdown lacks a unique QTest evidence block; regenerate legacy reports from their run directories")
    encoded = text.split(START, 1)[1].split(END, 1)[0]
    try:
        return loads(base64.b64decode(encoded, validate=True))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid report evidence encoding") from exc


def report_context(artifact):
    evidence = artifact["payload"]["evidence"]
    return evidence["suite.json"], {name: value for name, value in evidence.items() if name.startswith("observations/")}


def write_report(files, output, expected_suite=None, force=False):
    from .report import render
    output = Path(output)
    require(output.suffix.lower() == ".md", "Report output must end in .md")
    sidecar = output.with_suffix(".report.json")
    require(force or not output.exists() and not sidecar.exists(), "Report or JSON already exists; use --force to regenerate")
    artifact, summary = make_report(files, expected_suite)
    suite, records = report_context(artifact)
    content = render(summary, suite, records) + "\n" + embedded(artifact)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Both outputs contain exactly the same evidence payload; neither relies on its sibling.
    write_json(sidecar, artifact)
    temporary = output.with_suffix(".md.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(output)
    return {"report": str(output.resolve()), "json": str(sidecar.resolve()), "run_id": summary["run_id"]}
