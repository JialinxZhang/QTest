from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile

from .adapter import load_adapter
from .benchmarks import REGISTRY
from .bundles import read_run, read_suite
from .contracts import QTestError, canonical, read_json, require, validate_device
from .engine import verify_files
from .report_io import write_report
from .compare import write_comparison
from .templates import write_templates

# Test execution lives exclusively in the web console (docs/unified-testing-platform.md).
# This CLI only validates inputs and replays/verifies/reports existing evidence.


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qtest", description="Evidence validation and analysis tools; tests run in the web console")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="List implemented protocols by layer")
    item = sub.add_parser("validate", help="Check a suite JSON against the frozen contract")
    item.add_argument("suite")
    item = sub.add_parser("adapter-check")
    item.add_argument("adapter")
    item.add_argument("--config")
    for command in ("verify", "report"):
        item = sub.add_parser(command)
        item.add_argument("run")
        item.add_argument("--expected-suite", help="Trusted JSON suite or task bundle to bind against")
        if command == "report":
            item.add_argument("--output", required=True)
            item.add_argument("--force", action="store_true", help="Regenerate an existing report and JSON companion")
    item = sub.add_parser("templates", help="Write nine detailed Markdown report templates")
    item.add_argument("--output", required=True)
    item.add_argument("--force", action="store_true")
    item = sub.add_parser("compare", help="Replay and compare Markdown/JSON reports, run directories, or result ZIPs")
    item.add_argument("inputs", nargs="+")
    item.add_argument("--output", required=True, help="Markdown output; JSON and CSV are also produced")
    item.add_argument("--recursive", action="store_true", help="Scan input report directories recursively")
    item.add_argument("--expected-suite", help="Bind every input to a trusted suite")
    item.add_argument("--strict", action="store_true", help="Fail without output if any candidate report is rejected")
    item.add_argument("--force", action="store_true", help="Regenerate existing comparison outputs")
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
        elif args.command == "validate":
            suite = read_suite(args.suite)
            result = {"valid": True, "suite_id": suite["suite_id"], "cases": len(suite["cases"])}
        else:
            files = read_run(args.run)
            expected = read_suite(args.expected_suite) if args.expected_suite else None
            summary = verify_files(files, expected)
            if args.command == "verify":
                result = {"verified": True, "verification": summary["verification"],
                          "scored_repetitions": sum(c["status"] == "completed" for c in summary["cases"])}
            else:
                result = write_report(files, args.output, expected, args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (QTestError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(f"qtest: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("qtest: interrupted; preserve the evidence directory before retrying", file=sys.stderr)
        return 130
