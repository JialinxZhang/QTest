# 厂商接入说明：QTest 0.3.0

Web 管理台复用本文的 Adapter 接口。由服务器管理员安装插件并通过 `qtest-web device-add` 注册设备；页面只引用注册设备 ID，不接收 SDK 代码或配置文件路径。Web 接入操作见 [部署说明](web-management.md)。

本文针对当前可执行原型。正式的跨路线能力矩阵、开放应用轨道和发布规则见 `toolchain-framework.md`。

## 1. 接入边界

厂商编写 Python 适配器，将标准工作负载映射到本机 SDK。测试规则、混合优化控制器和指标由公共工具执行。设备仅支持部分接口时只声明相应能力，其他项目显示 `unsupported`。

工具自身不需要厂商私有控制代码进入公开仓库。厂商可独立分发 wheel，固定依赖版本，在受控环境执行。主办方需要能够核查编译策略、运行参数和原始结果。当前插件系统仅覆盖设备适配器；新增 benchmark 仍需在公共注册表中实现并随工具发布，不支持结果包动态加载算法代码。

## 2. 插件安装与调用

复制 `vendor_adapter_template/`，实现 `vendor_adapter.py`，修改包名及 entry point 名称。模板会明确报错，不能作为假设备运行。

```toml
[project.entry-points."qtest_bench.adapters"]
vendor-example = "vendor_adapter:VendorAdapter"
```

```powershell
# 已先安装 qtest-bench；完成实现后才安装适配器。
.\.venv\Scripts\python -m pip install ./vendor_adapter_template
.\.venv\Scripts\qtest adapter-check vendor-example --config device.local.json
.\.venv\Scripts\qtest plan packages/task-v1.zip --adapter vendor-example --config device.local.json
.\.venv\Scripts\qtest run packages/task-v1.zip --adapter vendor-example --config device.local.json --operator vendor-lab --mode vendor --output runs/vendor-live-001
```

`device.local.json` 由厂商定义并保留本地；优先从环境变量或凭证存储读取密钥，不放进 suite、工作负载、设备描述、provenance 或日志。公共工具不主动导出该配置文件，但不能替厂商清理其自行写入的原始结果。

## 3. 五个方法

| 方法 | 返回 | 要求 |
| --- | --- | --- |
| `describe()` | 设备描述 dict | 只读，不提交采样任务；真实标注 qpu／simulator |
| `submit(workload, request_id)` | 厂商 job_id 字符串 | 固定超时；有幂等能力时利用 request_id；无能力时说明提交状态核实方法 |
| `status(job_id)` | queued／running／completed／failed／cancelled | 不隐式提交新任务，不因断网重跑 |
| `result(job_id)` | 标准测量结果 dict | 原始计数，逻辑顺序统一，所有接受／丢弃分母清楚 |
| `cancel(job_id)` | 无 | 尽力取消；取消请求成功不等于设备已停止执行，需核实最终状态 |

设备描述示例（仅字段示意，不能原样作为真实能力声明）：

```json
{
  "adapter": "vendor-example",
  "adapter_version": "0.1.0",
  "device_id": "actual-device-identifier",
  "kind": "qpu",
  "technology": "superconducting",
  "capabilities": ["gate.sample.v1"]
}
```

当前 `plan` 根据粗粒度能力与调用数判断，尚不检查设备具体拓扑、模型可达性和每任务 shot 上限。适配器必须在提交前做细粒度检查；正式版本将加入可结构化的能力约束与编译预检。

## 4. 标准任务与语义

所有请求有 `kind`、`shots`、`seed`。seed 表示测试控制器及可用采样种子，不要求真实硬件产生可复现的随机测量结果。

| kind | 其他字段 | 当前语义 |
| --- | --- | --- |
| `gate.sample.v1` | n_qubits、gates | 从全零态执行 H、X、Rx、Ry、Rz、CX、Rzz，Z 基读出；每个门有 name、qubits，旋转门另有 angle |
| `analog.evolve.v1` | n_qubits、hx、hz、edges、initial、time | 静态 H = Σ hxᵢ Xᵢ + Σ hzᵢ Zᵢ + Σ Jᵢⱼ ZᵢZⱼ，edges 为 [i,j,J]，执行 exp(-iHt) |
| `photon.fock.v1` | unitary_real、unitary_imag、input_occupation | 理想目标 U 的 Fock 输入采样；结果保留固定总光子数接受模式，接受率另报 |

门旋转为 `exp(-i angle P / 2)`，弧度；CX 的 qubits 为 `[control,target]`。比特串左侧为逻辑比特 0；对 QX 等设备 SDK 的原生位序要显式核实并转换。门路由、读出物理比特映射、校准 ID 和编译版本建议进入 `provenance`。

`analog.evolve.v1` 不保证可以转换为某个厂商的原生模型。不能表达相互作用符号、初态或控制项时要拒绝，不得删边、改系数或把不同模型当成相同任务。数字近似需另建冻结协议并记录分解误差，当前不允许悄悄加 Trotter 近似。

光学矩阵 U 的行对应输出模式，列对应输入模式。光子计数键格式为 `"2,0"`、`"1,1"` 等，每项为模式光子数。当前协议要求对 accepted 结果具有数分辨语义；不能把阈值点击直接视为光子数。`attempted_shots` 必须是协议约定的尝试总数；`accepted_shots` 可更小，不能隐式重采直到凑齐 accepted。

## 5. 测量结果

```json
{
  "counts": {"00": 120, "11": 128},
  "attempted_shots": 256,
  "accepted_shots": 248,
  "resources": {
    "qpu_seconds": null,
    "execution_seconds": null,
    "timing_scope": "provider timing unavailable"
  },
  "provenance": {
    "postselection": "8 attempts discarded; specify actual reason",
    "mitigation": "none",
    "calibration_id": "actual-calibration-id",
    "sdk_version": "actual-sdk-version",
    "mapping": "logical qubit 0 is leftmost"
  }
}
```

所有 counts 为正整数且总和等于 accepted_shots，accepted 不得大于 attempted。当前版本要求 attempted 等于请求 shots；未完成请求应报失败并保留局部数据到厂商执行记录，不得冒充完成。缓解后的准概率或负值不得放入原始 counts；正式缓解轨道需另行协议。

时间未知填 null；禁止以 0 代替未知。`execution_seconds` 由适配器声明时间范围，`qpu_seconds` 仅设备实际执行；运行器另记提交到结果的客户端墙钟。provider 内部校准／重试消耗尚未纳入结构化资源统计，正式测试前必须补充。

## 6. 故障、证据与验收

运行器在调用 SDK 前写入请求，获得 job_id 后再次落盘。超时或错误会停止后续实验并尝试取消；提交异常时可能根本未获得 job_id，必须利用 request_id 联系厂商核实是否已提交及执行状态。不自动重试、不自动续跑。`run.json` 仍为 running 的目录代表中断未收敛，当前不能导出为已验收的成绩包。

每个作业保存完整 workload、role、request_id、job_id（若已取得）、状态、result、哈希及客户端时间。当前协议一个请求对应一个独立厂商任务，不能把同一 job_id 重复用于多个完成的请求；批处理需要后续协议定义。角色明确区分训练和最终评估。verify 从冻结种子及算法重新生成请求序列，用回传原始计数推进优化器，最后独立计算指标；缺失、额外请求或改变算法参数均会导致完成项目验收失败。

run.json 记录 Python、NumPy、jsonschema 及操作系统版本。正式分发应冻结完整依赖和验收环境；当前请求序列采用严格匹配，跨版本数值差异可能触发拒绝，应回到原环境复核，不能跳过检查。

正式接入前请至少验证：已知基态与 Bell 态位序、旋转符号、双比特方向、物理／逻辑读出对应、模拟模型时间单位、光子模式顺序、丢弃分母、零样本、提交超时、取消失败、重复请求和厂商任务凭证。主办方依据来源等级决定是否发布，工具的 verify 通过不代替来源复核。

## 7. 报告交付与综合对比

使用 `qtest report` 生成详细 Markdown 与 `.report.json`，两者都包含可独立验收的证据；主办方可用 `qtest compare` 直接读取。不要修改原始计数后只改可见分数，也不要用同一 run_id 提交不同证据。

`provenance` 中明确提供 mitigation 和 postselection；未知要写明，不能默认解释为无缓解／无后选择。工具只在声明完全一致的条件组中汇总优劣；缺失这些字段不参加组内最佳摘要。正式发布的可信程度仍取决于原始任务凭证与来源核验。详见 `reporting-and-comparison.md`。
