"""Report semantics are explicit; metadata must never be ranked as a quality score."""
from __future__ import annotations

import statistics

# label, unit, direction, interpretation
METRICS = {
    "mean_population_mae": ("平均占据数绝对误差", "无量纲", "lower", "各演化时刻的局域占据数平均绝对误差"),
    "population_mae": ("单时刻占据数误差", "无量纲", "lower", "演化轨迹内单时刻数值，仅作轨迹展示，不单独参与排名"),
    "correlation_mae": ("单时刻关联误差", "无量纲", "lower", "物理量轨迹内单时刻数值，仅作轨迹展示，不单独参与排名"),
    "structure_factor_mae": ("单时刻结构因子误差", "无量纲", "lower", "物理量轨迹内单时刻数值，仅作轨迹展示，不单独参与排名"),
    "empirical_tvd": ("经验总变差距离", "无量纲", "lower", "相对理想分布的有限样本距离，有采样偏差"),
    "collision_probability": ("碰撞概率", "比例", "context", "输出中至少一个模式含多个光子的概率，依实例解释"),
    "collision_se": ("碰撞概率采样标准误", "比例", "context", "条件样本的采样不确定度，不是质量指标"),
    "acceptance": ("样本接受率", "比例", "higher", "接受数 / 总尝试数，应与条件质量共同解释"),
    "feasible_probability": ("原图可行解比例", "比例", "higher", "最终接受样本中满足原图约束的比例"),
    "best_feasible_size": ("最佳可行解节点数", "节点", "higher", "最终独立样本中最好的可行集合大小"),
    "exact_optimum": ("原问题精确最优值", "节点", "reference", "精确枚举参考，不能作为设备成绩排序"),
    "best_ratio": ("最佳可行解 / 最优值", "比例", "higher", "仅在最优值已知时定义；无可行解时缺失"),
    "optimal_sample_probability": ("最优解采样比例", "比例", "higher", "最终接受样本中达到精确最优值的比例"),
    "energy": ("最终独立评估能量", "模型能量单位", "context", "带噪能量可能低于基态，不能以越低越好直接排序"),
    "energy_se": ("能量采样标准误", "模型能量单位", "context", "由各测量组的样本方差估计"),
    "reference_ground_energy": ("参考基态能量", "模型能量单位", "reference", "同一模型精确对角化的最小本征值"),
    "absolute_energy_error": ("最终能量绝对误差", "模型能量单位", "lower", "独立评估能量与参考基态的绝对差"),
    "mean_correlation_mae": ("等时关联平均绝对误差", "无量纲", "lower", "冻结时空网格上连通关联 Cij 相对精确参考的平均绝对误差"),
    "mean_structure_factor_mae": ("结构因子平均绝对误差", "无量纲", "lower", "冻结 q,t 网格上 S(q,t) 相对精确参考的平均绝对误差"),
}

PRIMARY = {
    "kernel.hamiltonian.v1": "mean_population_mae",
    "kernel.boson_fock.v1": "empirical_tvd",
    "application.mis.v1": "best_ratio",
    "application.vqe_toy.v1": "absolute_energy_error",
    "application.material_dynamics.v1": "mean_structure_factor_mae",
}

DIRECTION = {"lower": "越低越好", "higher": "越高越好", "context": "依任务解释", "reference": "参考值"}


def describe(key):
    return METRICS.get(key, (key, "未定义", "context", "未注册的指标，不参与优劣排序"))


def statistics_of(values):
    finite = [v for v in values if type(v) in (int, float)]
    return {"n": len(finite), "missing": len(values) - len(finite),
            "mean": statistics.mean(finite) if finite else None,
            "median": statistics.median(finite) if finite else None,
            "sd": statistics.stdev(finite) if len(finite) > 1 else None,
            "minimum": min(finite) if finite else None, "maximum": max(finite) if finite else None}


def numeric_metrics(rows):
    names = set()
    for row in rows:
        for name, value in row.get("metrics", {}).items():
            if type(value) in (int, float) or value is None and name in METRICS:
                names.add(name)
    return {name: statistics_of([row["metrics"].get(name) for row in rows]) for name in sorted(names)}
