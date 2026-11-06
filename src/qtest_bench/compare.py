"""Read all submitted reports, replay evidence, and compare only compatible conditions."""
from __future__ import annotations

from collections import Counter
import csv
import json
import math
from pathlib import Path
import zipfile

from . import __version__
from .bundles import read_run
from .contracts import QTestError, digest, loads, require, write_json
from .metrics import DIRECTION, PRIMARY, describe, numeric_metrics
from .report import cell, fmt, table, usage, valid_measurement
from .report_io import MAX_REPORT_BYTES, START, inspect_report, make_report, read_report, report_context


def discover(inputs, recursive=False, exclude=()):
    found = {}
    excluded = {Path(p).resolve() for p in exclude}
    for value in inputs:
        path = Path(value).resolve()
        require(path.exists(), f"Input does not exist: {path}")
        if path.is_dir() and (path / "run.json").is_file():
            found[path] = True
        elif path.is_dir():
            candidates = path.rglob("*") if recursive else path.iterdir()
            for item in candidates:
                if item.is_file() and item.suffix.lower() in (".md", ".json", ".zip") and item.resolve() not in excluded:
                    found.setdefault(item.resolve(), False)
                    require(len(found) <= 500, "At most 500 input files per comparison")
        else:
            require(path not in excluded, "Comparison output cannot also be an explicit input")
            found[path] = True
    require(len(found) <= 500, "At most 500 input files per comparison")
    require(found, "No candidate reports found")
    return sorted(found.items(), key=lambda p: str(p[0]))


def source_artifact(path, explicit, expected_suite):
    if path.is_dir() or path.suffix.lower() == ".zip":
        return make_report(read_run(path), expected_suite)[0]
    require(path.stat().st_size <= MAX_REPORT_BYTES, "Report exceeds size limit")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".md" and START not in text:
        if explicit or text.startswith(("# QTest 测试报告", "# QTest 单机")):
            raise QTestError("Legacy or incomplete Markdown; regenerate it with qtest report from the original run")
        return None
    if path.suffix.lower() == ".json":
        data = loads(text)
        if isinstance(data, dict) and data.get("format") == "qtest-report-1":
            return data
        if explicit or path.name.endswith(".report.json"):
            raise QTestError("JSON is not a supported QTest test report")
        return None
    return read_report(path)


def execution_profile(records):
    profiles = []
    complete = True
    for record in records:
        measurement = valid_measurement(record)
        if not measurement:
            complete = False
            continue
        provenance = measurement.get("provenance", {})
        item = {key: provenance.get(key) for key in ("mitigation", "postselection")}
        unknown = {"unknown", "unspecified", "not reported", "not provided", "未知", "未提供", "未声明", "n/a"}
        if any(not isinstance(value, str) or not value.strip() or value.strip().lower() in unknown for value in item.values()):
            complete = False
        if item not in profiles:
            profiles.append(item)
    return sorted(profiles, key=digest), complete and bool(profiles)


def comparison_rules(summary, suite, case, records):
    profile, complete = execution_profile(records)
    return {"protocol_version": suite["protocol_version"], "metric_rules": "pilot-v1",
            "device_kind": summary["device"]["kind"], "case": case,
            "seed": suite["seed"], "limits": suite["limits"],
            "execution_profile": profile}, complete


def build_comparison(inputs, recursive=False, expected_suite=None, strict=False, exclude=()):
    candidates = discover(inputs, recursive, exclude)
    sources, rejected, ignored, duplicates, groups = [], [], [], [], {}
    seen = {}
    for path, explicit in candidates:
        try:
            artifact = source_artifact(path, explicit, expected_suite)
            if artifact is None:
                ignored.append({"path": str(path), "reason": "Non-test-report document or prior comparison output"})
                continue
            summary = inspect_report(artifact, expected_suite)
            evidence_hash = digest(artifact["payload"]["evidence"])
            run_id = summary["run_id"]
            if run_id in seen:
                require(seen[run_id] == evidence_hash, "Conflicting evidence for the same run_id")
                duplicates.append({"path": str(path), "run_id": run_id})
                continue
            suite, records = report_context(artifact)
            source = {"path": str(path), "run_id": run_id, "evidence_sha256": evidence_hash,
                      "suite_sha256": summary["suite_sha256"], "protocol_version": summary["protocol_version"],
                      "device": summary["device"], "operator": summary["operator"], "mode": summary["mode"],
                      "environment": summary.get("environment", {}), "started_at": summary.get("started_at"),
                      "finished_at": summary.get("finished_at"), "verification": summary["verification"],
                      "status_counts": dict(Counter(r["status"] for r in summary["cases"])),
                      "planned_repetitions": len(summary["cases"]), "resources": usage(records.values()),
                      "client_wall_seconds": summary.get("client_wall_seconds")}
            additions = []
            for case in suite["cases"]:
                rows = [r for r in summary["cases"] if r["case_id"] == case["id"]]
                scored = [r for r in rows if r["status"] == "completed"]
                jobs = [records[p] for r in rows for p in r["records"]]
                rules, profile_complete = comparison_rules(summary, suite, case, jobs)
                group_id = digest(rules)
                group = {"group_id": group_id, "case_id": case["id"],
                    "benchmark": case["benchmark"], "rules": rules, "entries": []}
                entry = {"run_id": run_id, "device_id": summary["device"]["device_id"],
                    "technology": summary["device"]["technology"], "device_kind": summary["device"]["kind"],
                    "mode": summary["mode"], "operator": summary["operator"], "planned": len(rows), "completed": len(scored),
                    "status_counts": dict(Counter(r["status"] for r in rows)), "profile_complete": profile_complete,
                    "metrics": numeric_metrics(scored), "resources": usage(jobs)}
                additions.append((group, entry))
            seen[run_id] = evidence_hash
            sources.append(source)
            for group, entry in additions:
                groups.setdefault(group["group_id"], group)["entries"].append(entry)
        except (QTestError, OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, zipfile.BadZipFile, RecursionError) as exc:
            rejected.append({"path": str(path), "reason": f"{type(exc).__name__}: {exc}"})
    if strict:
        require(not rejected, "Strict import rejected input: " + "; ".join(r["reason"] for r in rejected[:3]))
    require(sources, "No valid reports available; " + "; ".join(r["reason"] for r in rejected[:3]))
    # A conflicting run ID invalidates every copy, rather than selecting the first candidate.
    conflicting = {item["path"] for item in rejected if "Conflicting evidence" in item["reason"]}
    require(not conflicting, "Conflicting copies of a run were found; resolve them before comparison")
    for group in groups.values():
        leaders = {}
        for metric in sorted({m for e in group["entries"] for m in e["metrics"]}):
            direction = describe(metric)[2]
            eligible = [e for e in group["entries"] if e["completed"] == e["planned"] and e["profile_complete"]
                        and e["metrics"].get(metric, {}).get("n") == e["planned"]]
            if direction not in ("higher", "lower") or len(eligible) < 2:
                continue
            best = (min if direction == "lower" else max)(e["metrics"][metric]["mean"] for e in eligible)
            leaders[metric] = {"best_observed_mean": best, "eligible_run_ids": [e["run_id"] for e in eligible],
                "observed_best_run_ids": [e["run_id"] for e in eligible if math.isclose(e["metrics"][metric]["mean"], best, rel_tol=1e-12, abs_tol=1e-12)],
                "interpretation": "descriptive only; no statistical superiority or quantum advantage claim"}
        group["observed_leaders"] = leaders
    return {"format": "qtest-comparison-2", "tool_version": __version__, "sources": sources,
            "groups": sorted(groups.values(), key=lambda g: (g["benchmark"], g["case_id"], g["group_id"])),
            "import_summary": {"candidate_files": len(candidates), "unique_runs": len(sources), "duplicates": duplicates,
                               "rejected": rejected, "ignored": ignored},
            "trust": {"raw_evidence_replayed": True, "externally_bound_suite": expected_suite is not None,
                      "device_identity": "self-declared; independent hardware authentication is outside this tool"}}


def label(entry):
    return f"{entry['device_id']} / {entry['run_id']}"


def render_comparison(report):
    sources, groups, imports = report["sources"], report["groups"], report["import_summary"]
    identities = {(s["device"]["device_id"], s["device"]["kind"], s["device"]["technology"]) for s in sources}
    lines = ["# QTest 综合对比报告", "", "> 比较模板 v3 · 根据报告内原始证据重新验收及统计；不生成跨任务总分。", "",
             "## 1. 对比摘要", "",
             f"扫描 {imports['candidate_files']} 个候选文件，接受 {len(sources)} 个独立运行记录，涉及 {len(identities)} 个声明设备身份、{len(groups)} 个条件组。",
             f"去重 {len(imports['duplicates'])} 个副本；拒绝 {len(imports['rejected'])} 个文件；忽略 {len(imports['ignored'])} 个非测试报告文件。", ""]
    if len(identities) < 2:
        lines.extend(["**当前数据只有一个声明设备身份。本报告展示多次运行／执行方式的一致性，不构成多硬件横向比较。**", ""])
    if all(s["device"]["kind"] == "simulator" for s in sources):
        lines.extend(["**所有输入均为模拟器结果，不是真机榜单。**", ""])
    if imports["rejected"]:
        lines.extend(["**本报告为部分导入结果，必须结合第 8 节的拒绝清单解释覆盖范围。**", ""])
    lines.extend(["## 2. 参测系统、来源与覆盖", ""])
    table(lines, ["设备 / 运行 ID", "类型 / 路线", "执行方式", "完成 / 计划重复", "开始 UTC", "外部题目绑定"],
          [(f"{s['device']['device_id']} / {s['run_id']}", f"{s['device']['kind']} / {s['device']['technology']}", s["mode"],
            f"{s['status_counts'].get('completed', 0)} / {s['planned_repetitions']}", s["started_at"], s["verification"]["bound_to_expected_suite"]) for s in sources])
    lines.extend(["同一运行的 Markdown、JSON 与原始证据包只计算一次。同一设备不同 run_id 分别展示，不冒充不同设备，也不自动合并为独立统计重复。", "",
                  "## 3. 可比性规则与分组", "",
                  "只有协议、benchmark、原问题及算法参数、case_id、每次 shots、计划重复数、种子、全局预算、设备类型和缓解／后选择声明完全一致时才进入同一条件组。",
                  "模拟器与 QPU 分组；缺少缓解或后选择声明的结果可展示，但不参加组内观测最佳值摘要。不同技术路线在上述语义一致时可同组。", ""])
    table(lines, ["条件组", "项目 / benchmark", "类型", "输入数", "每次 shots", "重复数", "参数哈希"],
          [(g["group_id"][:12], f"{g['case_id']} / {g['benchmark']}", g["rules"]["device_kind"], len(g["entries"]),
            g["rules"]["case"]["shots"], g["rules"]["case"]["repetitions"], digest(g["rules"]["case"]["parameters"])) for g in groups])
    variants = {}
    for g in groups:
        variants.setdefault((g["case_id"], g["benchmark"]), []).append(g)
    for (case_id, _), related in variants.items():
        if len(related) > 1:
            fields = [k for k in related[0]["rules"] if len({digest(g["rules"][k]) for g in related}) > 1]
            lines.append(f"- {cell(case_id)} 分成 {len(related)} 组；差异字段：{cell(', '.join(fields))}。")
    lines.extend(["", "## 4. 分任务指标对比", "",
                  "所有数值为完成重复的统计；失败和不支持不填零。观测最佳均值仅是描述性结果，未做显著性检验。只有全部计划重复完成、指标完整的运行参与该摘要。", ""])
    for g in groups:
        lines.extend([f"### {cell(g['case_id'])} · 条件组 {g['group_id'][:12]}", ""])
        table(lines, ["设备 / 运行", "完成 / 计划", "状态分布", "缓解 / 后选择声明完整"],
              [(label(e), f"{e['completed']} / {e['planned']}", json.dumps(e["status_counts"], ensure_ascii=False), e["profile_complete"]) for e in g["entries"]])
        metrics = sorted({k for e in g["entries"] for k in e["metrics"]})
        if not metrics:
            lines.extend(["此组没有可评分的完成结果。", ""])
        for key in metrics:
            name, unit, direction, explanation = describe(key)
            lines.extend([f"#### {cell(name)}（{cell(unit)}；{DIRECTION[direction]}）", "", cell(explanation) + "。", ""])
            leader = g["observed_leaders"].get(key)
            values = []
            for e in g["entries"]:
                stat = e["metrics"].get(key, {})
                gap = None
                if leader and e["run_id"] in leader["eligible_run_ids"]:
                    gap = (stat["mean"] - leader["best_observed_mean"]) * (1 if direction == "lower" else -1)
                values.append((label(e), stat.get("n", 0), stat.get("missing", e["completed"]), stat.get("mean"), stat.get("median"),
                               stat.get("sd"), stat.get("minimum"), stat.get("maximum"), gap))
            table(lines, ["设备 / 运行", "有效 n", "缺失 n", "均值", "中位数", "标准差", "最小", "最大", "距观测最佳差值"], values)
            if leader and key == PRIMARY.get(g["benchmark"]):
                lines.extend(["本任务主指标的观测最佳运行：" + cell(", ".join(leader["observed_best_run_ids"])) + "。该描述不证明统计优越性。", ""])
    lines.extend(["## 5. 资源、稳定性与可用范围", ""])
    table(lines, ["设备 / 运行", "预留作业", "预留 shots", "已回收接受 / 尝试", "无完整结果作业", "全流程秒数", "QPU 已知秒数 / 缺失作业"],
          [(f"{s['device']['device_id']} / {s['run_id']}", s["resources"]["jobs"], s["resources"]["requested_shots"],
            f"{s['resources']['observed_accepted_shots']} / {s['resources']['observed_attempted_shots']}", s["resources"]["incomplete_jobs"],
            s["client_wall_seconds"], f"{fmt(s['resources']['qpu_seconds']['total'])} / {s['resources']['qpu_seconds']['missing']}") for s in sources])
    lines.extend(["以上是整次运行的资源清单，不计算跨题库加速比。未知 QPU 时间不能视为零；全流程时间包含本地参考计算。",
                  "跨日漂移、校准波动和设备长期可用率没有从少量重复中外推。", "",
                  "## 6. 结论与下一轮测试建议", "",
                  "- 先按相同条件组检查主指标，再联合接受率、可行率、完成率及资源判断；不对全部技术路线给出单一总排名。",
                  "- 不支持和失败揭示当前覆盖缺口，不能用零分补齐后求平均。",
                  "- 原始能量、参考最优值、碰撞概率、标准误不作为通用优劣指标；不将能量越低自动解释为越准确。",
                  "- n=1 无法估计重复间波动；差异接近采样误差时需增加重复并冻结统计检验方案。",
                  "- 完整硬件比较还需来源核验、专业经典基线及跨校准周期复测。", "",
                  "## 7. 来源与可复核性", ""])
    table(lines, ["运行 ID", "来源文件", "证据 SHA-256", "报告声明已绑定题目", "本次对比外部绑定"],
          [(s["run_id"], s["path"], s["evidence_sha256"], s["verification"].get("report_claimed_suite_binding", False),
            s["verification"]["bound_to_expected_suite"]) for s in sources])
    lines.extend(["报告声明不是外部信任来源。仅当本次 compare 提供 --expected-suite 时，才与当前主办方题目副本绑定。设备身份仍来自适配器声明。", "",
                  "## 8. 导入审计", ""])
    for title, field in (("重复副本（未重复计数）", "duplicates"), ("拒绝文件", "rejected"), ("忽略文件", "ignored")):
        lines.extend([f"### {title}", ""])
        if imports[field]:
            table(lines, ["文件", "原因 / 运行 ID"], [(v["path"], v.get("reason", v.get("run_id"))) for v in imports[field]])
        else:
            lines.extend(["无。", ""])
    return "\n".join(lines)


def csv_cell(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def write_comparison(inputs, output, recursive=False, expected_suite=None, strict=False, force=False):
    output = Path(output)
    require(output.suffix.lower() == ".md", "Comparison output must end in .md")
    json_path, csv_path = output.with_suffix(".comparison.json"), output.with_suffix(".comparison.csv")
    destinations = (output, json_path, csv_path)
    require(force or not any(p.exists() for p in destinations), "Comparison output exists; use --force to regenerate")
    if output.exists():
        require(START not in output.read_text(encoding="utf-8-sig"), "Cannot overwrite a source test report with a comparison")
    report = build_comparison(inputs, recursive, expected_suite, strict, destinations)
    content = render_comparison(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(json_path, report)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["group_id", "case_id", "benchmark", "run_id", "device_id", "device_kind", "completed", "planned", "metric", "unit", "direction", "n", "missing", "mean", "median", "sd", "minimum", "maximum"])
        for group in report["groups"]:
            for entry in group["entries"]:
                for metric, values in entry["metrics"].items():
                    row = [group["group_id"], group["case_id"], group["benchmark"], entry["run_id"], entry["device_id"], entry["device_kind"],
                           entry["completed"], entry["planned"], metric, describe(metric)[1], describe(metric)[2],
                           *[values[k] for k in ("n", "missing", "mean", "median", "sd", "minimum", "maximum")]]
                    writer.writerow([csv_cell(v) for v in row])
    temporary = output.with_suffix(".md.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(output)
    return {"report": str(output.resolve()), "json": str(json_path.resolve()), "csv": str(csv_path.resolve()),
            "runs": len(report["sources"]), "groups": len(report["groups"]), "rejected": len(report["import_summary"]["rejected"])}
