# QTest Web 管理台：部署与使用

版本 0.4.0 · 2026-11-05。适用于一台实验室服务器，多个浏览器账号共享用例和结果。

## 1. 已实现的工作流

1. 管理员创建测试员和查看者账号。
2. 测试员在用例库使用模板创建、编辑或复制测试用例；参数保存前经过现有 QTest 契约校验。
3. 创建任务，选择用例、设备和资源上限，先检查执行计划，再提交。
4. Worker 领取任务，调用现有 Engine 与 Adapter；页面每 2 秒更新活动任务。
5. 结束后独立重放验收并生成 Markdown 报告、JSON 证据报告和结果 ZIP；任务详情页按用例分组展示指标汇总表（各重复数值、均值、中位数、标准差，主指标高亮并标注单位与方向）以及演化、训练轨迹折线图，执行中即可看到每个已完成重复的主指标。
6. 在结果页选择 2–20 个已有已验证报告的测试任务，异步生成分组对比及 Markdown/JSON/CSV 下载；对比页显示中文指标名、单位、方向和有效样本数，并标记组内观测最佳。

五种可执行协议为 Ising 哈密顿模拟、Fock 玻色采样、MIS-QAOA、双比特模型 VQE 和一维 Ising 链材料动力学。新建用例是在已实现的协议下定义实例，不是增加任意新算法；新协议需扩展 Python 注册表、契约、指标及验证器。所有页面数据来自真实任务记录，内置设备明确标为模拟器。

## 2. 安装与本机试用

Python 3.11+。先安装依赖，再在无外网的实验室运行也可；页面字体与脚本不调用 CDN。真实设备是否需要外网取决于厂商 SDK。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[web]"
.\.venv\Scripts\qtest-web --data web-data init --username admin
```

`init` 交互要求输入两次密码，长度 12–200 字符。不提供默认账号密码组合；再次运行不会重置现有管理员或覆盖用例。初始化会注册本地理想模拟器及五个示例用例。

在两个独立终端中分别执行：

```powershell
# 终端一：Web API 与页面
.\.venv\Scripts\qtest-web --data web-data serve
```

```powershell
# 终端二：任务执行器，和 Web 使用同一个绝对数据目录
.\.venv\Scripts\qtest-web --data web-data worker
```

访问 `http://127.0.0.1:8765`。修改端口时 `--origin` 也必须一致，例如 `serve --port 8877 --origin http://127.0.0.1:8877`。HTTP 模式只允许回环监听；不要将试用地址直接提供给其他电脑。

仅验证一次队列领取可用 `worker --once`，最多执行一个可领取任务后退出。正式运行 Worker 不使用此参数。网页显示“执行器离线”时仍允许提交任务，它会留在数据库队列等待执行。

## 3. 实验室内网部署

推荐拓扑：浏览器 → 内网 HTTPS 入口 → 同机 `127.0.0.1:8765` → FastAPI；Worker 在同机独立运行。

1. 为服务器分配内网 DNS 名称，例如 `qtest.lab.example`，并获取该名称的受信任证书。
2. 在服务账户可写的服务器本地磁盘建立数据目录。**SQLite 数据库不要放在 SMB/NFS 共享盘。**
3. 启动 Web，指定公开给实验室的精确 origin：

```powershell
.\.venv\Scripts\qtest-web --data C:/QTestData serve --host 127.0.0.1 --port 8765 --origin https://qtest.lab.example
.\.venv\Scripts\qtest-web --data C:/QTestData worker
```

两行表示两个独立进程，不能在同一个前台终端连续运行。使用 Windows 服务管理工具或 Linux 服务管理器分别托管；第一版不自动安装系统服务。给两个进程配置相同环境、Python 包、数据目录和运行账户。数据库仅一台服务器访问，允许多个浏览器连接 Web。

Caddy 的配置示例在 [deploy/Caddyfile.example](../deploy/Caddyfile.example)。管理员替换 DNS 名和证书路径后启动已有反向代理；该示例不自动申请证书、改防火墙或改系统信任库。已有 Nginx/IIS 等反向代理也可使用，须保留浏览器 Host、Origin 和 Cookie，入口限制在实验室网络。Caddy 的 `reverse_proxy` 和显式证书配置见 [反向代理文档](https://caddyserver.com/docs/quick-starts/reverse-proxy)、[TLS 文档](https://caddyserver.com/docs/caddyfile/directives/tls)。

HTTPS origin 下 Cookie 自动加 Secure。登录会话最长 8 小时，退出、停用账号、修改角色或重设密码会撤销相应会话。写请求要求 JSON、自定义请求头和精确同源；没有开放跨域 CORS。登录按用户名及服务器所见的客户端地址限速，5 分钟内最多 20 次尝试；同机代理下地址限制共享，较大实验室需在受信任代理侧规划限速策略。密码用 Argon2id 保存散列。

## 4. 账号、设备和权限

管理员通过“系统管理”创建账号、调整角色、停用或恢复账号，也可以在服务器交互操作：

```powershell
.\.venv\Scripts\qtest-web --data C:/QTestData user-add researcher --role operator
.\.venv\Scripts\qtest-web --data C:/QTestData user-add observer --role viewer
.\.venv\Scripts\qtest-web --data C:/QTestData password-reset admin
```

查看者只能读取；测试员可管理共享用例、提交测试和对比、取消本人任务；管理员还可管理账号、取消所有任务和解除设备阻塞。系统保护最后一位启用的管理员。第一版所有登录用户都能读取和下载共享实验室的结果，不提供项目或组织数据隔离。

接入设备先安装并验证厂商提供的 Adapter 插件，再执行：

```powershell
# adapter 名称必须是已安装的 qtest_bench.adapters 入口点。
.\.venv\Scripts\qtest-web --data C:/QTestData device-add qx-lab-a --name "QX 实验室设备 A" --adapter vendor_adapter_name --config C:/QTestSecrets/qx.local.json
```

注册是只读描述校验，不提交作业。服务端保存本地配置位置，Web API 不返回该位置或内容；用户只能选择白名单设备。注册的设备描述冻结进任务，执行前重新核对。修改设备接入配置时应使用新注册 ID，避免旧排队任务的含义发生变化；配置文件中的密钥轮换由服务器管理员维护。

代码没有预装 QX 或其他真机驱动。仍须按 [厂商接入说明](vendor-integration.md) 实现 SDK 映射、编译检查、原始计数和超时。浏览器设备状态“已注册”表示配置登记完成，不代表一次实时的 QPU 健康探测。

## 5. 任务语义与异常处理

| 状态 | 含义与处理 |
|---|---|
| 排队中 | 已冻结输入，等待 Worker；此时取消不会调用设备。 |
| 执行中 | 已领取任务，可能在厂商队列、实验计算或独立验收阶段。 |
| 取消处理中 | 执行器在下一协作检查点停止继续提交；厂商 SDK 阻塞时不会立刻返回。 |
| 已完成 | 所有重复实验完成且独立验收通过；对比任务完成也使用此状态。 |
| 部分完成 | 存在已完成实验，其余失败、超时、不支持等；结果不补零。 |
| 已取消 | 已停止推进本次任务；若向厂商提交过作业，其最终状态仍需核实。 |
| 失败 | 执行或验收未成功；查看事件及保留的本地证据。 |
| 异常中断 | 新 Worker 在获取独占锁后发现上次未正常结束的任务，不自动重试。 |

Worker 单实例锁由操作系统管理，不依赖心跳超时来抢任务。心跳来自独立线程，因此“在线”不保证 SDK 未阻塞。Web 服务重启不触发执行恢复；Worker 重启才检查遗留任务。

故障作业和异常中断会阻塞对应注册设备。管理员先到厂商控制台核查作业 ID / request ID，确认其终态，再在系统管理中记录核查说明并解除阻塞。解除阻塞不重放旧任务，随后符合条件的排队任务可继续执行。所有解除操作写入审计。

取消与成功结束存在正常竞态：若取消到达时计算已经完成，保留成功结果；对比计算属于有限的本地重放，不能强行中断正在执行的计算，结束后显示最终状态。SDK submit/status/result/cancel 均应有自身超时，当前 Python 执行器不保证硬中断阻塞调用。

用例更新使用版本检查，过期编辑返回 409，防止覆盖其他人的更新。任务使用幂等提交标识，同一标识重复请求返回原任务；同一标识携带不同输入返回 409。任务输入冻结在库中，后续编辑或归档用例不修改旧实验。

## 6. 结果、存储与兼容性

```text
web-data/
  qtest.sqlite3            # 用户、会话、版本、队列与审计
  qtest.sqlite3-wal        # SQLite 自动管理，运行时不可单独删除
  qtest.sqlite3-shm
  worker.lock              # OS 独占锁文件，存在不等于已锁定
  tasks/<task-id>/
    run/suite.json
    run/run.json
    run/observations/*.json
    result.json            # 已验收的页面结果与指标解释
    report.md
    report.report.json
    result.zip
    # 对比任务改为 comparison.md / .comparison.json / .comparison.csv
```

报告继续采用 `qtest-report-1`，对比采用 `qtest-comparison-2`。Web 的文件可以交给现有 `qtest verify/report/compare` 工具继续处理。当前 Web 结果列表针对通过 Web 创建的任务；已有外部报告仍使用 CLI 批量导入和对比，不在网页任意读取服务器目录。

首版边界：最多 200 个待执行/活动任务，2000 个用例；单任务 1–64 个用例、最多 2000 次计划作业；任务列表显示最近 200 个任务，旧任务仍可通过已保存的任务 URL 访问，文件不会因列表截断而删除。对比最多 20 个已验证测试任务。页面不提供硬删除，归档用例可恢复。

备份时先停止接收新任务，等待 Worker 完成或完成取消及厂商状态核查，停止 Web 和 Worker，再备份整个数据目录及单独管理的设备配置。数据库和任务证据须来自同一备份窗口。恢复到同一版本并验证数据目录访问权限后再启动；遗留执行状态会触发中断恢复规则。服务器账户需要本地磁盘读写权限与所需的设备网络访问，不要求浏览器电脑安装 Python 或 SDK。

## 7. 代码结构与接口

| 文件 | 职责 |
|---|---|
| `web/store.py` | SQLite 短事务、用例版本、设备目录、不可变任务快照、队列写入与幂等 |
| `web/auth.py` | 密码散列、登录限速、会话及角色 |
| `web/app.py` | REST API、权限、同源保护、静态资源与结果下载 |
| `web/worker.py` | 单进程锁、持久队列、状态恢复、调用现有执行与报告模块 |
| `web/cli.py` | 初始化、账号维护、设备注册、Web 和 Worker 启动 |
| `web/static/` | 无构建步骤的中文响应式页面 |
| `engine.py` | 保持算法定义，新增可选 `should_cancel` 回调与 provider_state 记录 |

主要 API：`/api/login`、`/api/me`、`/api/overview`、`/api/catalog`、`/api/cases`、`/api/devices`、`/api/plan`、`/api/tasks`、`/api/comparisons`、`/api/users`、`/api/audit`。任务详情位于 `/api/tasks/{id}`，结果与下载为 `/result`、`/artifacts/{name}`，取消为 POST `/cancel`。所有业务读取须登录；写请求由服务端角色检查，不依赖前端隐藏按钮。

用于开发验证：

```powershell
.\.venv\Scripts\python -m pip install -e ".[web,test]"
.\.venv\Scripts\python -m unittest discover -s tests -v
node --check src/qtest_bench/web/static/app.js
```

Node 只用于可选 JavaScript 语法检查，运行管理台无需 Node。此版本已做本地集成和浏览器验证，尚未在实验室实际 DNS/证书/网络或真实 QPU 上验证。
