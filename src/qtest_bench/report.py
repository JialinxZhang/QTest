from __future__ import annotations

from collections import Counter
import json

from .benchmarks import get_benchmark
from .contracts import QTestError, digest, validate_measurement
from .metrics import DIRECTION, describe, numeric_metrics


def cell(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;").replace("`", "\\`")


def fmt(value):
    if value is None:
        return "未提供 / 不适用"
    if isinstance(value, bool):
        return "是" if value else "否"
    if type(value) is float:
        return "0" if value == 0 else f"{value:.8g}"
    return cell(value)


def table(lines, headers, rows):
    lines.extend(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"])
    for row in rows:
        lines.append("| " + " | ".join(fmt(v) for v in row) + " |")
    lines.append("")


def known_total(values):
    known = [v for v in values if type(v) in (int, float) and v >= 0]
    return {"total": sum(known) if known else None, "known": len(known), "missing": len(values) - len(known)}


def valid_measurement(record):
    """Failed jobs may retain malformed provider payloads; never turn them into resource facts."""
    try:
        return validate_measurement(record["workload"], record["result"])
    except (QTestError, KeyError, TypeError, ValueError, AttributeError):
        return {}


def usage(records):
    rows = list(records)
    measurements = [valid_measurement(r) for r in rows]
    completed = [r for r, measured in zip(rows, measurements) if r.get("state") == "completed" and measured]
    attempted = sum(r["result"]["attempted_shots"] for r in completed)
    accepted = sum(r["result"]["accepted_shots"] for r in completed)
    return {"jobs": len(rows), "completed_jobs": len(completed),
            "requested_shots": sum(r["workload"]["shots"] for r in rows),
            "observed_attempted_shots": attempted, "observed_accepted_shots": accepted,
            "observed_acceptance": accepted / attempted if attempted else None,
            "incomplete_jobs": len(rows) - len(completed),
            "client_job_wall_seconds": known_total([r.get("client_wall_seconds") for r in rows]),
            "qpu_seconds": known_total([r.get("resources", {}).get("qpu_seconds") for r in measurements]),
            "execution_seconds": known_total([r.get("resources", {}).get("execution_seconds") for r in measurements])}


def metrics_table(lines, rows):
    stats = numeric_metrics(rows)
    table(lines, ["指标 / 单位", "方向", "有效 / 缺失", "均值", "中位数", "标准差", "最小", "最大"],
          [(f"{describe(k)[0]} / {describe(k)[1]}", DIRECTION[describe(k)[2]], f"{v['n']} / {v['missing']}",
            v["mean"], v["median"], v["sd"], v["minimum"], v["maximum"]) for k, v in stats.items()])
    for key in stats:
        lines.append(f"- **{cell(describe(key)[0])}**：{cell(describe(key)[3])}。")
    lines.append("")


def detailed_results(lines, rows):
    for row in rows:
        metrics = row["metrics"]
        lines.extend([f"#### 重复 {row['repetition']} 的结果", ""])
        table(lines, ["指标 / 字段", "值"], [(describe(k)[0], v) for k, v in metrics.items() if not isinstance(v, (dict, list))])
        if "trajectory" in metrics:
            first = metrics["trajectory"][0] if metrics["trajectory"] else {}
            if "structure_factor_mae" in first:
                lines.extend(["物理量轨迹（同一重复内各时刻，不视为独立重复；局域占据误差不作为材料动力学成绩）：", ""])
                table(lines, ["演化时间", "关联 MAE", "结构因子 MAE", "经验 TVD", "接受率"],
                      [(v["time"], v["correlation_mae"], v["structure_factor_mae"], v["empirical_tvd"], v["acceptance"]) for v in metrics["trajectory"]])
            else:
                lines.extend(["演化轨迹（同一重复内各时刻，不视为独立重复）：", ""])
                table(lines, ["演化时间", "占据数 MAE", "经验 TVD", "接受率"],
                      [(v["time"], v["population_mae"], v["empirical_tvd"], v["acceptance"]) for v in metrics["trajectory"]])
        for trace in ("training_objective", "training_energy"):
            if trace in metrics:
                lines.extend(["训练轨迹（独立最终评估见上表；最低训练估计不作为最终成绩）：", ""])
                table(lines, ["迭代", "训练估计"], list(enumerate(metrics[trace])))


def render(summary, suite=None, records=None):
    records = records or {}
    device = summary["device"]
    label = "本地模拟验证，不是真机成绩" if device["kind"] == "simulator" else "量子设备测试；设备身份仍需来源核验"
    statuses = Counter(row["status"] for row in summary["cases"])
    completed = statuses["completed"]
    total = len(summary["cases"])
    lines = ["# QTest 单机详细测试报告", "", "> 报告模板 v3 · " + label, "",
             "## 1. 执行摘要", "",
             f"本次登记 **{len(set(r['case_id'] for r in summary['cases']))} 个测试项目、{total} 次重复**，其中 {completed} 次完成并经过原始证据重放验收。",
             "该完成状态表示流程及指标可复核；质量是否达标需要预先冻结的验收阈值，本次协议未定义统一通过线。", ""]
    table(lines, ["结果分类", "重复次数", "说明"], [
        ("完成", completed, "质量指标仅由这些重复计算"),
        ("不支持", statuses["unsupported"], "能力不兼容，不记零分"),
        ("失败 / 超时 / 预算耗尽", sum(statuses[x] for x in ("failed", "timeout", "budget_exhausted")), "保留证据，不给完成分数"),
        ("中止后跳过", statuses["skipped_after_abort"], "计入计划覆盖分母，不计为已执行")])
    lines.extend(["## 2. 设备、软件与实验来源", ""])
    table(lines, ["字段", "记录"], [
        ("运行 ID", summary["run_id"]), ("设备 ID", device["device_id"]), ("类型 / 路线", f"{device['kind']} / {device['technology']}"),
        ("适配器 / 版本", f"{device['adapter']} / {device['adapter_version']}"), ("能力声明", ", ".join(device["capabilities"])),
        ("执行方式 / 执行者", f"{summary['mode']} / {summary['operator']}"), ("开始 UTC", summary.get("started_at")),
        ("结束 UTC", summary.get("finished_at")), ("工具 / 协议", f"{summary['tool_version']} / {summary['protocol_version']}"),
        ("操作系统", summary.get("environment", {}).get("platform")), ("Python", summary.get("environment", {}).get("python")),
        ("NumPy", summary.get("environment", {}).get("numpy")), ("jsonschema", summary.get("environment", {}).get("jsonschema")),
        ("题目 SHA-256", summary["suite_sha256"]),
        ("本次生成时绑定主办方题目", summary["verification"]["bound_to_expected_suite"]),
        ("硬件身份认证", "适配器声明；软件验收不认证真实设备"),
        ("专业经典性能基线", "本原型未运行；精确参考仅用于数值验收")])
    lines.extend(["## 3. 实验设计、预算与覆盖", ""])
    if suite is not None:
        table(lines, ["配置", "值"], [("suite_id", suite["suite_id"]), ("描述", suite.get("description", "未提供")),
              ("随机种子", suite["seed"]), ("轨道", "固定算法 pilot-closed；不是开放算法轨道"),
              *[(key, val) for key, val in suite["limits"].items()]])
    table(lines, ["项目", "benchmark", "重复", "状态", "作业数", "说明"],
          [(r["case_id"], r["benchmark"], r["repetition"], r["status"], len(r["records"]), r.get("reason", "完成并重放验收")) for r in summary["cases"]])
    lines.extend(["## 4. 全流程资源与时间口径", ""])
    if records:
        resource = usage(records.values())
        table(lines, ["资源", "值", "口径"], [
            ("预留作业", summary["resources"]["reserved_jobs"], "包括提交结果未知的调用"),
            ("预留 shots", summary["resources"]["reserved_shots"], "全部训练、最终评估和内核测量"),
            ("已回收尝试 shots", resource["observed_attempted_shots"], "仅状态 completed 的作业"),
            ("已回收接受 shots", resource["observed_accepted_shots"], "丢弃不隐式补采"),
            ("已回收样本接受率", resource["observed_acceptance"], "仅覆盖已取得完整有效测量结果的作业"),
            ("无完整结果作业", resource["incomplete_jobs"], "未完成或未取得有效测量结果；需核实执行状态与返回数据"),
            ("全流程客户端秒数", summary.get("client_wall_seconds"), "含编排和本地参考计算，不是纯 QPU 时间")])
        table(lines, ["时间项", "已知值之和 / 秒", "已知作业", "缺失作业"],
              [(field, resource[field]["total"], resource[field]["known"], resource[field]["missing"]) for field in ("client_job_wall_seconds", "qpu_seconds", "execution_seconds")])
        lines.extend(["各时间项范围可能重叠，不能相加得到端到端时间。不同厂商的 execution_seconds 须先核实 timing_scope。", ""])
        stage_rows = []
        for stage, prefix in (("训练", "train:"), ("最终评估", "final:"), ("演化轨迹", "trajectory:")):
            selected = [r for r in records.values() if r.get("role", "").startswith(prefix)]
            used = usage(selected)
            stage_rows.append((stage, used["jobs"], used["requested_shots"], used["observed_accepted_shots"]))
        table(lines, ["阶段", "作业数", "请求 shots", "已回收接受 shots"], stage_rows)
    else:
        lines.extend(["未提供作业明细；仅可读取 run.json 的资源请求总量，资源细分不作推测。", ""])
    lines.extend(["## 5. 各项目的详细测试结果", "",
                  "均值、极值及样本标准差均按完成的重复计算；n=1 不提供重复间标准差。未计算显著性检验或置信区间。", ""])
    case_ids = list(dict.fromkeys(r["case_id"] for r in summary["cases"]))
    for case_id in case_ids:
        rows = [r for r in summary["cases"] if r["case_id"] == case_id]
        scored = [r for r in rows if r["status"] == "completed"]
        b = get_benchmark(rows[0]["benchmark"])
        lines.extend([f"### {cell(case_id)} — {cell(b.title)}", ""])
        table(lines, ["任务要素", "内容"], [("层级 / 必要能力", f"{b.layer} / {b.capability}"),
              ("完成重复 / 计划重复", f"{len(scored)} / {len(rows)}"), ("算法协议", rows[0]["benchmark"])])
        if suite is not None:
            case = next(c for c in suite["cases"] if c["id"] == case_id)
            lines.extend([f"每个量子调用请求 **{case['shots']} shots**；参数哈希：`{digest(case['parameters'])}`。", "",
                          "冻结的原问题与算法参数：", "", "```json", json.dumps(case["parameters"], ensure_ascii=False, indent=2), "```", ""])
        if scored:
            metrics_table(lines, scored)
            detailed_results(lines, scored)
        else:
            lines.extend(["本项目没有完成重复，因此不生成质量分数。失败或不支持原因见覆盖表。", ""])
        case_records = [records[p] for row in rows for p in row["records"] if p in records]
        if case_records:
            r = usage(case_records)
            table(lines, ["项目资源", "数值"], [("请求作业", r["jobs"]), ("请求 shots", r["requested_shots"]),
                  ("已回收尝试 / 接受 shots", f"{r['observed_attempted_shots']} / {r['observed_accepted_shots']}"), ("无完整结果作业", r["incomplete_jobs"])])
    lines.extend(["## 6. 逐作业证据索引与异常", ""])
    if records:
        table(lines, ["证据文件", "厂商任务 ID", "阶段", "状态", "请求 / 接受 shots", "客户端秒数"],
              [(p, r.get("job_id"), r["role"], r["state"], f"{r['workload']['shots']} / {valid_measurement(r).get('accepted_shots', '未知')}", r.get("client_wall_seconds")) for p, r in records.items()])
        profiles = {json.dumps(valid_measurement(r).get("provenance", {}), ensure_ascii=False, sort_keys=True) for r in records.values() if "result" in r}
        lines.extend(["结果中的运行、缓解和后选择声明（不同声明分别保留）：", ""])
        for p in sorted(profiles):
            lines.append(f"- {cell(p)}")
        lines.append("")
        failures = [(p, r.get("error_type"), r.get("submission_outcome"), r.get("cancellation")) for p, r in records.items() if r.get("state") != "completed"]
        if failures:
            table(lines, ["证据文件", "错误类型", "提交结果", "取消状态"], failures)
        else:
            lines.extend(["没有记录到作业执行异常；这不构成设备长期可用率结论。", ""])
    lines.extend(["## 7. 结果解释、缺口与复测建议", "",
                  "- 精确参考用于数值验收，未提供公平调优的经典性能竞争基线。",
                  "- 经验 TVD 含有限样本偏差；条件样本质量必须与接受率一起解释。",
                  "- 最低训练能量不是最终成绩；独立能量可能因采样波动低于参考基态，需结合误差和诊断标记。",
                  "- 两比特 VQE 与 1D Ising 材料动力学均为联调样例，不是真实分子基准或材料预测；分类、GBS 应用仍未实测。",
                  "- 应按冻结协议增加独立重复和跨校准周期复测，再讨论稳定性及显著差异。",
                  "- 未测量的校准和编译数据记为缺失，不能推断为零或已满足要求。", "",
                  "## 8. 机器读取与复核", "",
                  "本报告附带完整 JSON 证据的机器可读块，同名 .report.json 含相同内容；任一文件均可交给 qtest compare。",
                  "对比工具从证据重新验收并计算指标，不从展示表格反向猜测数值。编辑展示文字不会改变用于对比的证据；修改结论应重新生成报告。",
                  "完整性哈希与重放仅验证数据及算法一致性，不认证真实硬件来源；公开发布仍需来源审核。", ""])
    return "\n".join(lines)
