# QTest：跨技术路线量子测试工具链

当前版本：0.4.0。设备代号统一使用 QX；在 0.3 的多人 Web 管理台与综合对比之上，新增 1D Ising 链材料动力学联调样例（连通关联矩阵与等时结构因子轨迹）。

资源预算仅指作业次数、shots 和运行时长上限。报告模板为 v3，综合 JSON 使用 `qtest-comparison-2`，以 `incomplete_jobs` 统计无完整有效结果的作业；任务执行、原始证据及指标算法保持原有定义。

一个面向主办方与主机厂商共用的 Python 工具链原型。统一测试题目、设备适配、预算管理、原始证据和独立验收。支持主办方直连执行，以及厂商接收测试包后自测回传。

**请先阅读 [整体工具链框架](docs/toolchain-framework.md)**；设备合作方阅读 [厂商接入说明](docs/vendor-integration.md)。原有 [Benchmark 建设方案](benchmark-proposal.md) 继续定义正式项目范围。

## 当前可交付范围

| 模块 | 已实现 | 边界 |
| --- | --- | --- |
| 协议与任务包 | 严格 JSON 校验、冻结题目哈希、数据型 ZIP | 尚无签名或隐藏题库 |
| 适配器 | 插件发现、设备描述、提交／查询／取回／取消接口 | 内置仅理想本地模拟器；未连接真机 |
| 执行器 | 重复实验、调用及 shots 预算、协作式超时、提交前留档 | 不自动重试，不保证中断阻塞的厂商 SDK |
| L1 样例 | 小规模 Ising 演化、Fock 玻色采样 | 精确参考规模受限；Fock 不等于 GBS |
| L2 样例 | MIS-QAOA 完整优化；两比特 VQE 完整迭代；1D Ising 链材料动力学（关联 + 结构因子） | VQE 与材料动力学均为联调样例，不是分子基准或真实材料预测 |
| 验收 | 独立重放控制器、检查请求、计数和指标 | 能验证一致性，不能认证设备身份或数据来源 |
| 报告 | 配置、预算、逐次结果、训练轨迹、逐作业证据；自包含 Markdown + JSON | 当前自动填充已实现任务，后续应用提供设计模板 |
| 综合对比 | 批量读取报告、证据重放、去重、可比性分组；Markdown + JSON + CSV | 描述性对比，不提供跨任务总分或自动发布 |
| Web 管理台 | 登录与三级权限、用例版本管理、执行预检、持久队列、状态监控、取消、结果下载与综合对比；指标汇总表、主指标高亮、演化／训练轨迹图及观测最佳标记 | 单服务器、单 Worker；共享实验室工作区，不是多租户服务 |

分类、真实分子 VQE、原子原生 MIS、门模型数字模拟及条件适配的光子应用仍在首期目标中；材料动力学当前为 1D Ising 链联调样例，二维阵列与真实材料模型未实现。当前代码是验证接口和交付流程的骨架，不是正式首期套件的完成声明。

## 安装

要求 Python 3.11+；依赖 NumPy 与 jsonschema。以下为 PowerShell 命令，使用项目虚拟环境，无需激活脚本：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\qtest --help
```

## Web 管理台

调研与架构见 [Web 实施方案](docs/web-management-plan.md)，内网 HTTPS 配置、账号、设备与运维见 [Web 部署与使用说明](docs/web-management.md)。Web 依赖为可选项；原 CLI 不要求安装 Web 框架。

```powershell
.\.venv\Scripts\python -m pip install -e ".[web]"
# 交互设置首个管理员密码，无默认密码。
.\.venv\Scripts\qtest-web --data web-data init --username admin
# 在两个终端分别启动，使用相同的数据目录。
.\.venv\Scripts\qtest-web --data web-data serve
.\.venv\Scripts\qtest-web --data web-data worker
```

本机访问 `http://127.0.0.1:8765`。多人内网部署使用 HTTPS 反向代理，并把 `serve --origin` 设置为浏览器实际访问地址。数据目录应位于服务器本地磁盘。页面可新建/编辑/复制/归档用例，创建测试任务、申请取消、查看原始证据验收结果，选择多个任务生成对比报告。

后台执行独立于页面，关闭浏览器或重启 Web 服务不会主动停止 Worker。Worker 中断的任务标为中断并保留证据，设备状态核实后再继续；不自动重复提交硬件作业。

## 方式一：主办方直接测试

```powershell
.\.venv\Scripts\qtest catalog
.\.venv\Scripts\qtest validate examples/smoke-suite.json
.\.venv\Scripts\qtest adapter-check local
.\.venv\Scripts\qtest plan examples/smoke-suite.json --adapter local
.\.venv\Scripts\qtest run examples/smoke-suite.json --adapter local --operator organizer-demo --mode organizer --output runs/organizer-demo
.\.venv\Scripts\qtest verify runs/organizer-demo --expected-suite examples/smoke-suite.json
.\.venv\Scripts\qtest report runs/organizer-demo --expected-suite examples/smoke-suite.json --output reports/organizer-demo.md
```

## 方式二：厂商收到标准包后测试

```powershell
# 主办方制作题目包；厂商另行安装相同版本的工具与适配器。
.\.venv\Scripts\qtest pack examples/smoke-suite.json --output packages/task-v1.zip
# 这条命令用本地模拟器演示厂商执行。接真机需将 local 替换为审核过的适配器名称。
.\.venv\Scripts\qtest run packages/task-v1.zip --adapter local --operator vendor-demo --mode vendor --output runs/vendor-demo
.\.venv\Scripts\qtest export runs/vendor-demo --expected-suite packages/task-v1.zip --output packages/result-v1.zip
# 主办方以自己保留的题目副本验收，而非仅相信回传包里的题目。
.\.venv\Scripts\qtest verify packages/result-v1.zip --expected-suite examples/smoke-suite.json
.\.venv\Scripts\qtest report packages/result-v1.zip --expected-suite examples/smoke-suite.json --output reports/vendor-demo.md
```

运行目录和 ZIP 不覆盖已有结果；报告默认也不覆盖，可用 `--force` 明确重新生成 Markdown 及伴随数据。`run` 的退出码：0 表示未发生执行故障（可能含不兼容项目），3 表示执行失败／超时等；输入或验收错误为 2。`verify` 成功表示证据一致，不表示所有项目完成或达到质量阈值。

## 详细模板与综合对比

[模板索引](docs/report-templates/README.md) 包含单机总览、哈密顿量、玻色采样、优化、VQE、材料动力学、分类、光子应用及综合对比，共 9 类。自动输出报告包含可见详细表格及机器可读证据，单个 Markdown 或 JSON 文件均可独立导入。

```powershell
# 生成或更新模板；未实现的场景在模板中明确标为设计内容。
.\.venv\Scripts\qtest templates --output docs/report-templates --force
# 用保留的原始运行升级旧版报告，同时生成 organizer-demo.report.json。
.\.venv\Scripts\qtest report runs/organizer-demo --expected-suite examples/smoke-suite.json --output reports/organizer-demo.md --force
.\.venv\Scripts\qtest report runs/vendor-demo --expected-suite examples/smoke-suite.json --output reports/vendor-demo.md --force
# 扫描报告目录并重新验收；同一运行的 Markdown 和 JSON 自动去重。
.\.venv\Scripts\qtest compare reports --recursive --expected-suite examples/smoke-suite.json --output reports/comparison.md --force
```

输出 `comparison.md`、`comparison.comparison.json` 和 `comparison.comparison.csv`。不同题库批次可省略 `--expected-suite`，但此时只重放报告内证据，不声称已绑定本次主办方的外部题目副本。

工具支持多个目录／文件、原始运行目录及结果 ZIP。不同协议、模型、预算、设备类型或缓解／后选择条件分组，未完成项不填零，原始能量不按越低越好排序。完整参数、数据合同、旧报告升级与退出码见 [报告和对比工具说明](docs/reporting-and-comparison.md)。

## 开发与验证

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m pip wheel . --no-deps --wheel-dir dist
```

包发现采用 Python 标准 entry points；协议输入采用 JSON Schema 2020-12。安装适配器会运行受信任的本地代码，因此应先审核；测试 ZIP 只包含数据，不携带可执行插件。

目录：`src/qtest_bench/` 为执行与验收实现，`examples/` 为联调题目，`vendor_adapter_template/` 为厂商模板，`tests/` 为功能验证，`docs/` 为设计与接入规范。
