# QTest：跨技术路线量子测试工具链

当前版本：0.5.0。设备代号统一使用 QX。0.5.0 起测试执行统一收敛到 Web 管理台单一入口（不再区分主办方/厂商执行模式，CLI 只保留验收与分析），并新增 L0 基础能力层：镜像电路保真度与最大稳定线路深度基准。0.4.0 引入 1D Ising 链材料动力学样例与结果可视化。

资源预算仅指作业次数、shots 和运行时长上限。报告模板为 v3，综合 JSON 使用 `qtest-comparison-2`，以 `incomplete_jobs` 统计无完整有效结果的作业；任务执行、原始证据及指标算法保持原有定义，历史证据（0.1.0 起）仍可验收与对比。

一个面向主办方与主机厂商共用的 Python 工具链原型。统一测试题目、设备适配、预算管理、原始证据和独立验收。**测试执行只有 Web 管理台一个入口**：实验室成员在浏览器中完成建用例、提交任务、查看验收结果与横向对比的全流程；CLI 保留 validate / verify / report / compare / templates / adapter-check / catalog 作为输入校验与离线复核分析工具，不再执行测试。变更设计见 [统一入口与分层指标方案](docs/unified-testing-platform.md)。

**请先阅读 [整体工具链框架](docs/toolchain-framework.md)**；设备合作方阅读 [厂商接入说明](docs/vendor-integration.md)；负责部署的实验室管理员阅读 [Web 部署与使用说明](docs/web-management.md) 与 [Web 实施方案](docs/web-management-plan.md)。原有 [Benchmark 建设方案](benchmark-proposal.md) 继续定义正式项目范围。

## 当前可交付范围

| 模块 | 已实现 | 边界 |
| --- | --- | --- |
| 协议与任务包 | 严格 JSON 校验、冻结题目哈希、数据型 ZIP | 尚无签名或隐藏题库 |
| 适配器 | 插件发现、设备描述、提交／查询／取回／取消接口 | 内置仅理想本地模拟器；未连接真机 |
| 执行器 | 重复实验、调用及 shots 预算、协作式超时、提交前留档 | 不自动重试，不保证中断阻塞的厂商 SDK |
| L0 样例 | 镜像电路保真度与深度扫描（最大稳定线路深度） | 理想模拟器上成功概率≈1，真机噪声下才出现衰减；可用率／漂移遥测汇总在路线图 |
| L1 样例 | 小规模 Ising 演化、Fock 玻色采样 | 精确参考规模受限；Fock 不等于 GBS |
| L2 样例 | MIS-QAOA 完整优化；两比特 VQE 完整迭代；1D Ising 链材料动力学（关联 + 结构因子） | VQE 与材料动力学均为联调样例，不是分子基准或真实材料预测 |
| 验收 | 独立重放控制器、检查请求、计数和指标 | 能验证一致性，不能认证设备身份或数据来源 |
| 报告 | 配置、预算、逐次结果、训练轨迹、逐作业证据；自包含 Markdown + JSON | 当前自动填充已实现任务，后续应用提供设计模板 |
| 综合对比 | 批量读取报告、证据重放、去重、可比性分组；Markdown + JSON + CSV | 描述性对比，不提供跨任务总分或自动发布 |
| Web 管理台 | **唯一测试入口**；登录与三级权限、用例版本管理（L0/L1/L2 分层）、执行预检、持久队列、状态监控、取消、结果下载与综合对比；指标汇总表、主指标高亮、深度扫描／演化／训练轨迹图及观测最佳标记 | 单服务器、单 Worker；共享实验室工作区，不是多租户服务 |

分类、真实分子 VQE、原子原生 MIS、门模型数字模拟及条件适配的光子应用仍在首期目标中；材料动力学当前为 1D Ising 链联调样例，二维阵列与真实材料模型未实现。当前代码是验证接口和交付流程的骨架，不是正式首期套件的完成声明。

## 安装

要求 Python 3.11+；CLI 依赖 NumPy 与 jsonschema，Web 依赖为可选安装。以下为 PowerShell 命令，使用项目虚拟环境，无需激活脚本：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .          # 仅 CLI
.\.venv\Scripts\python -m pip install -e ".[web]"   # CLI + Web 管理台
.\.venv\Scripts\qtest --help
```

## CLI：验收与分析工具

测试执行在 Web 管理台完成（见下节）；CLI 用于输入校验和离线复核，可处理运行目录、结果 ZIP 与报告文件：

```powershell
.\.venv\Scripts\qtest catalog                       # 按层级列出已实现协议（L0/L1/L2）
.\.venv\Scripts\qtest validate examples/smoke-suite.json
.\.venv\Scripts\qtest adapter-check local           # 只读校验适配器描述，不提交作业
.\.venv\Scripts\qtest verify runs/organizer-demo --expected-suite examples/smoke-suite.json   # 独立重放验收
.\.venv\Scripts\qtest report runs/organizer-demo --expected-suite examples/smoke-suite.json --output reports/organizer-demo.md
.\.venv\Scripts\qtest compare reports --recursive --output reports/comparison.md
```

`verify` 成功表示证据一致，不表示所有项目完成或达到质量阈值。报告默认不覆盖已有文件，可用 `--force` 明确重新生成；输入或验收错误退出码为 2，对比含被拒绝输入时为 3。0.5.0 之前的运行记录、结果 ZIP 与报告（含 organizer/vendor 执行模式字段）均可继续验收、报告与对比，不作改写。

## Web 管理台

面向实验室内网的多人协作界面：多个浏览器账号共享用例库、任务队列和结果，替代"每人各自跑 CLI 再互发文件"的协作方式。Web 层调用与 CLI 完全相同的执行器、验收器和指标定义——同一任务的证据既能从页面查看，也能用 `qtest verify/report/compare` 离线复核。

### 功能一览

| 页面 | 功能 |
| --- | --- |
| 总览 | 任务状态计数、最近任务、执行器心跳、设备能力与阻塞提示 |
| 用例库 | 从 6 种协议模板（L0 基础能力、L1 内核、L2 场景应用三层）新建用例；编辑名称、说明、shots、重复次数和协议参数（JSON），保存前经过与 CLI 相同的契约校验；复制、归档／恢复；带版本号，过期编辑返回冲突而不是静默覆盖 |
| 新建测试任务 | 选择多个用例、目标设备、随机种子和作业／shots／时限预算；先"检查执行计划"预检能力缺项与资源数量，再提交；输入提交时冻结，后续改用例不影响旧任务；重复点击不重复创建（幂等提交标识） |
| 任务详情 | 实验进度条（已结束／计划重复、成功数）、已提交作业与预留 shots、最近一次厂商作业的 ID 与状态、执行事件时间线；执行中即可取消 |
| 测试结果 | 独立验收后按用例分组展示指标；下载 Markdown 报告、机器可读 JSON 与结果 ZIP |
| 结果与对比 | 选择 2–20 个有已验证报告的任务，异步生成综合对比；按可比条件分组展示并标记组内观测最佳 |
| 系统管理 | 管理员创建／停用账号、分配角色、查看操作审计；对故障设备核实后解除阻塞 |

任务结束后，任务详情页直接呈现验收结果：

- **验收结果概览**：完成作业、已回收尝试／接受 shots、无完整结果作业四项总量。
- **按用例分组的指标汇总表**：每个指标一行（中文名 + 单位 + 方向标注，如"结构因子平均绝对误差 · 无量纲 · 越低越好"），列为各重复的实测值和均值、中位数、标准差；主指标行高亮，并在面板右上以大数字显示主指标均值。未完成重复不填零，n=1 不显示标准差。
- **轨迹折线图**：材料动力学／哈密顿模拟的演化轨迹（关联误差、结构因子误差、TVD 随时间）和 VQE／MIS 的训练曲线，以内联 SVG 绘制，无 CDN 依赖，离线内网可用；图旁注明"训练估计不是最终成绩"。
- **执行中的实时结果**：实验进度明细表为每个已完成的重复即时显示其主指标值，不必等任务结束。
- 底部保留折叠的完整指标 JSON，供逐项核查。

综合对比沿用 CLI 的严格可比规则：协议、原问题参数、种子、预算、设备类型、缓解／后选择声明完全一致才进入同一条件组，不同条件自动拆组并在页面说明差异字段；组内显示每个运行的中文指标名、有效样本数、统计量与"观测最佳"标记（并列最佳同时标出）。观测最佳仅为描述性结果，不代表统计显著优势。

### 快速启动（本机试用）

```powershell
.\.venv\Scripts\python -m pip install -e ".[web]"
# 交互设置首个管理员密码（12–200 字符），无默认密码；同时注册本地模拟器和 5 个示例用例。
.\.venv\Scripts\qtest-web --data web-data init --username admin
# 终端一：Web API 与页面；终端二：任务执行器。两者使用同一个数据目录。
.\.venv\Scripts\qtest-web --data web-data serve
.\.venv\Scripts\qtest-web --data web-data worker
```

本机访问 `http://127.0.0.1:8765`。修改端口时 `--origin` 必须与浏览器实际访问地址一致；HTTP 模式只允许回环监听，不要把试用地址直接提供给其他电脑。`worker --once` 只验证一次队列领取。页面显示"执行器离线"时仍可提交任务，任务留在数据库队列中等待 Worker 启动。

### 账号与权限

| 操作 | 查看者 | 测试员 | 管理员 |
| --- | --- | --- | --- |
| 查看设备、用例、任务、结果与下载 | ✓ | ✓ | ✓ |
| 新建／编辑／归档用例，提交测试与对比 | | ✓ | ✓ |
| 取消自己的任务 | | ✓ | ✓ |
| 取消他人任务、解除设备阻塞 | | | ✓ |
| 创建／停用账号及分配角色 | | | ✓ |

首个管理员由 `qtest-web init` 交互创建；后续账号在"系统管理"页或服务器命令行（`user-add`、`password-reset`）管理，系统保护最后一位启用的管理员。第一版所有登录用户都能读取共享工作区的结果，不提供项目或组织数据隔离。密码以 Argon2id 散列保存；会话最长 8 小时，退出、停用账号、改角色或重设密码会立即撤销相应会话。

### 设备接入

页面只能选择管理员注册的设备，不能指定模块路径或服务器文件。注册前先安装并审核厂商 Adapter 插件，然后：

```powershell
# adapter 名称必须是已安装的 qtest_bench.adapters 入口点；配置文件留在服务器，Web API 不返回其位置或内容。
.\.venv\Scripts\qtest-web --data C:/QTestData device-add qx-lab-a --name "QX 实验室设备 A" --adapter vendor_adapter_name --config C:/QTestSecrets/qx.local.json
```

注册仅做只读描述校验，不提交作业；设备描述冻结进任务，执行前重新核对。修改接入配置应注册新设备 ID，避免旧排队任务含义变化。内置设备为本地理想模拟器，页面明确标注模拟器／QPU；"已注册"不代表实时健康探测。

### 任务状态与异常处理

| 状态 | 含义与处理 |
|---|---|
| 排队中 | 已冻结输入，等待 Worker；此时取消不会调用设备 |
| 执行中 | 已领取任务，可能在厂商队列、实验计算或独立验收阶段 |
| 取消处理中 | 执行器在下一协作检查点停止继续提交；厂商 SDK 阻塞时不会立刻返回 |
| 已完成 | 所有重复实验完成且独立验收通过；对比任务完成也使用此状态 |
| 部分完成 | 存在已完成实验，其余失败、超时、不支持等；结果不补零 |
| 已取消 | 已停止推进本次任务；若向厂商提交过作业，其最终状态仍需核实 |
| 失败 | 执行或验收未成功；查看事件及保留的本地证据 |
| 异常中断 | 新 Worker 获取独占锁后发现上次未正常结束的任务；不自动重试 |

后台执行独立于浏览器：关闭页面或重启 Web 服务不会停止 Worker；Worker 崩溃后由下一次启动检查遗留任务并标记"异常中断"。提交结果未知的故障作业会阻塞对应设备，管理员先在厂商控制台核实作业终态，再在系统管理中记录说明并解除阻塞——解除不重放旧任务，之后排队任务继续执行，全部操作写入审计。不自动重复提交硬件作业。

### 实验室内网 HTTPS 部署

推荐拓扑：浏览器 → 内网 HTTPS 入口（如 Caddy）→ 同机 `127.0.0.1:8765` → FastAPI；Worker 同机独立运行。

```powershell
.\.venv\Scripts\qtest-web --data C:/QTestData serve --host 127.0.0.1 --port 8765 --origin https://qtest.lab.example
.\.venv\Scripts\qtest-web --data C:/QTestData worker
```

- `--origin` 必须是浏览器实际访问的完整地址；HTTPS 下 Cookie 自动加 Secure。
- 数据目录必须位于服务器本地磁盘，**SQLite 数据库不要放在 SMB/NFS 共享盘**。
- 写请求要求精确同源、JSON 内容类型和自定义请求头；未开放跨域 CORS；登录按用户名和客户端地址限速（5 分钟 20 次）；响应带 CSP、nosniff 等安全头。
- Caddy 配置示例见 [deploy/Caddyfile.example](deploy/Caddyfile.example)；已有 Nginx/IIS 亦可，须保留 Host、Origin 和 Cookie。

### 数据目录与结果互通

```text
web-data/
  qtest.sqlite3            # 用户、会话、用例版本、队列与审计
  qtest.sqlite3-wal / -shm # SQLite WAL 自动管理
  worker.lock              # OS 独占锁文件
  tasks/<task-id>/
    run/suite.json · run.json · observations/*.json   # 与 CLI 运行目录同构的原始证据
    result.json            # 已验收结果与指标定义（页面数据源）
    report.md · report.report.json · result.zip        # 可直接下载
    # 对比任务为 comparison.md / .comparison.json / .comparison.csv
```

报告与对比沿用 `qtest-report-1`、`qtest-comparison-2` 格式，Web 产物可交给现有 `qtest verify/report/compare` 继续处理，反之 CLI 生成的报告仍走命令行批量对比（页面不任意读取服务器目录）。备份只需停机复制整个数据目录。

### 边界

单服务器、单 Worker：任务串行执行，无分布式调度和高可用；Web 结果列表只覆盖通过 Web 创建的任务。设备身份来自适配器自报，软件验收不认证真实硬件；这些与整体框架第 12 节的正式建设边界一致。

## 详细模板与综合对比

[模板索引](docs/report-templates/README.md) 包含单机总览、哈密顿量、玻色采样、优化、VQE、材料动力学、分类、光子应用及综合对比，共 9 类。自动输出报告包含可见详细表格及机器可读证据，单个 Markdown 或 JSON 文件均可独立导入。

```powershell
# 生成或更新模板；未实现的场景在模板中明确标为设计内容。
.\.venv\Scripts\qtest templates --output docs/report-templates --force
# 用保留的原始运行升级旧版报告，同时生成 organizer-demo.report.json。
.\.venv\Scripts\qtest report runs/organizer-demo --expected-suite examples/smoke-suite.json --output reports/organizer-demo.md --force
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

包发现采用 Python 标准 entry points；协议输入采用 JSON Schema 2020-12。安装适配器会运行受信任的本地代码，因此应先审核；测试 ZIP 只包含数据，不携带可执行插件。各版本验证记录见 [原型验证记录](docs/validation.md)。

目录：`src/qtest_bench/` 为执行与验收实现（`web/` 为管理台前后端），`examples/` 为联调题目，`vendor_adapter_template/` 为厂商模板，`tests/` 为功能验证，`docs/` 为设计与接入规范，`deploy/` 为反向代理示例。
