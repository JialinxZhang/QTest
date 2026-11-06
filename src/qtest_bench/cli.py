from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile

from .adapter import load_adapter
from .benchmarks import REGISTRY
from .bundles import read_run, read_suite, write_bundle
from .contracts import QTestError, canonical, read_json, require, validate_device
from .engine import plan, run_suite, verify_files
from .report_io import write_report
from .compare import write_comparison
from .templates import write_templates


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qtest", description="Portable quantum benchmark pilot toolchain")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="List actually implemented protocols")
    for command in ("validate", "plan", "run", "pack"):
        item = sub.add_parser(command)
        item.add_argument("suite")
        if command in ("plan", "run"):
            item.add_argument("--adapter", default="local")
            item.add_argument("--config", help="Local private adapter JSON; never included in result bundles")
        if command in ("run", "pack"):
            item.add_argument("--output", required=True)
        if command == "run":
            item.add_argument("--operator", required=True)
            item.add_argument("--mode", choices=("organizer", "vendor"), default="organizer")
    for command in ("verify", "report", "export"):
        item = sub.add_parser(command)
        item.add_argument("run")
        item.add_argument("--expected-suite", help="Organizer's trusted JSON suite or task bundle")
        if command in ("report", "export"):
            item.add_argument("--output", required=True)
        if command == "report":
            item.add_argument("--force", action="store_true", help="Regenerate an existing report and JSON companion")
    item = sub.add_parser("templates", help="Write nine detailed Markdown report templates")
    item.add_argument("--output", required=True)
    item.add_argument("--force", action="store_true")
    item = sub.add_parser("compare", help="Replay and compare Markdown/JSON reports, run directories, or result ZIPs")
    item.add_argument("inputs", nargs="+")
    item.add_argument("--output", required=True, help="Markdown output; JSON and CSV are also produced")
    item.add_argument("--recursive", action="store_true", help="Scan input report directories recursively")
    item.add_argument("--expected-suite", help="Bind every input to the organizer's trusted suite")
    item.add_argument("--strict", action="store_true", help="Fail without output if any candidate report is rejected")
    item.add_argument("--force", action="store_true", help="Regenerate existing comparison outputs")
    item = sub.add_parser("adapter-check", help="Read-only descriptor validation, does not submit jobs")
    item.add_argument("adapter")
    item.add_argument("--config")
    args = parser.parse_args(argv)
    try:
        if args.command == "templates":
            result = write_templates(args.output, args.force)
        elif args.command == "compare":
            expected = read_suite(args.expected_suite) if args.expected_suite else None
            result = write_comparison(args.inputs, args.output, args.recursive, expected, args.strict, args.force)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 3 if result["rejected"] else 0
        elif args.command == "catalog":
            result = {name: {"title": b.title, "capability": b.capability, "layer": b.layer} for name, b in REGISTRY.items()}
        elif args.command == "adapter-check":
            adapter = load_adapter(args.adapter, read_json(args.config) if args.config else {})
            result = adapter.describe()
            validate_device(result)
        elif args.command in ("validate", "plan", "run", "pack"):
            suite = read_suite(args.suite)
            if args.command == "validate":
                result = {"valid": True, "suite_id": suite["suite_id"], "cases": len(suite["cases"])}
            elif args.command == "pack":
                result = write_bundle(args.output, "task", {"suite.json": canonical(suite)})
            else:
                adapter = load_adapter(args.adapter, read_json(args.config) if args.config else {})
                if args.command == "plan":
                    result = plan(suite, adapter.describe())
                else:
                    summary = run_suite(suite, adapter, args.output, args.operator, args.mode)
                    result = {"output": str(Path(args.output).resolve()), "status": summary["status"],
                              "cases": [{"id": c["case_id"], "status": c["status"]} for c in summary["cases"]]}
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 3 if any(c["status"] in ("failed", "timeout", "budget_exhausted", "skipped_after_abort") for c in summary["cases"]) else 0
        else:
            files = read_run(args.run)
            expected = read_suite(args.expected_suite) if args.expected_suite else None
            summary = verify_files(files, expected)
            if args.command == "verify":
                result = {"verified": True, "verification": summary["verification"],
                          "scored_repetitions": sum(c["status"] == "completed" for c in summary["cases"])}
            elif args.command == "export":
                result = write_bundle(args.output, "result", files)
            else:
                result = write_report(files, args.output, expected, args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (QTestError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(f"qtest: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("qtest: interrupted; preserve the run directory and confirm unfinished provider job execution states before retrying", file=sys.stderr)
        return 130
