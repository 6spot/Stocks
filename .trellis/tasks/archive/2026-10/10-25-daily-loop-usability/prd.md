# 日常闭环可用性补齐

## 目标与用户价值

当前部署技术上可用（4 容器 healthy、有 20 篇 Document、2 份报告），但 Owner 无法从 Web 启动任何数据流动，任务列表里只有演示用配置，处理失败原因不可见。本任务让「采集 → 处理 → 研究 → 阅读」这条日常闭环真正可以被 Owner 自己驱动和诊断。

## 背景：已确认的现状（服务器 100.120.14.84 实测）

| 事实 | 证据 |
| --- | --- |
| 4 个容器全部 healthy，Web 200，`/health` ok | `docker compose ps`、`curl` |
| 库内：20 documents / 1 source / 71 facts / 60 chunks / 2 reports / 5 runs | psql 计数 |
| chat 与 embedding 端点均返回 200，embedding 1024 维匹配 `VECTOR_DIMENSIONS` | 容器内 urllib 探测 |
| S3（SeaweedFS `stocks-both-s3`）在跑，桶 `stocks-both` 可用 | `docker ps`、settings 读取 |
| **Web 无任何采集入口**：`api.py` 只有 7 个 POST（login/logout、processing retry、task 触发、run retry/continue/end） | `grep @app.post` |
| `sources.yaml` 中 `fed_press_releases` `enabled: true` 但从未被采集，库里只有 `sec_press_releases` | psql `sources` 表只有 1 行 |
| `scheduled_collection` 与 `scheduled_market_brief` 均 `enabled: false` | `config/tasks.yaml` |
| `tasks.yaml` 中 enabled 的 3 个 Task 全是示例（名称含 "Condition Example" / "Empty Evidence Report Example"） | `config/tasks.yaml` |
| 5 个 `facts` 阶段 failed：3 个 `PROCESSOR_FAILED`、2 个 `EXTRACTION_INVALID`（period_start 非合法 ISO 时间） | `document_processing_states` |
| `runner.py` 的 `except Exception` 只写 `PROCESSOR_FAILED`，**不记录原始异常** | `processing/runner.py:264` |
| Worker 容器日志 0 行：`stocks/` 下无任何 `basicConfig`/`dictConfig` | `docker logs stocks-worker-1`、grep |
| `sources.yaml` 0 处 `expected_freshness`，来源新鲜度永远 `unconfigured` | grep + `_source_freshness` |

## 协作边界（硬约束）

另一 Agent 正在 `10-25-overview-data-exhibition` 任务上工作，其未提交改动为：

```
M backend/src/stocks/api.py
M backend/tests/test_console_api.py
M docs/interface.md
M frontend/package.json / package-lock.json
M frontend/src/api/schemas.ts
M frontend/src/features/overview/overview-page.tsx
M frontend/src/index.css
?? frontend/src/components/ui/chart.tsx
?? frontend/src/features/overview/overview-page.test.tsx
```

本任务**禁止修改**上述任何文件，也禁止 `git checkout` / `git stash` / `git reset` / 格式化或重排这些文件。

本任务预计改动的文件（与在途改动零重叠）：

```
config/tasks.yaml
config/sources.yaml
backend/src/stocks/worker.py
backend/src/stocks/processing/runner.py
backend/src/stocks/settings.py（若需日志级别配置）
backend/tests/（仅新增或改动本任务相关用例）
docs/quickstart.md / docs/data.md / docs/execution.md（契约变化时）
.trellis/tasks/10-25-daily-loop-usability/
```

若实施中发现必须改动 `api.py`（例如新增采集端点），**停止并交回**，不擅自改动。R1 采用方案 A 正是为避开该文件。

提交时只 `git add` 本任务实际改动的文件，不使用 `git add -A` / `git commit -a`。

## 范围（第一批，Owner 已确认）

### R1 采集可从 Web 触发（方案 A，Owner 已选定）

Owner 能在 Web 上为某个已配置来源发起一次采集，不需要 SSH 到服务器执行 CLI。采集仍由 Worker 执行，API 只排队（遵守 `docs/architecture.md` 的「API 只读取控制台状态并排队授权动作，从不执行 Task Run」）。

实现方式：在 `config/tasks.yaml` 增加 `type: collection` 的 Task，`sources` 列出已启用的来源，复用现有「任务」页「手动触发」按钮与 `POST /api/tasks/{task_id}/runs`。**不新增 API 端点，不改前端代码。**

依据：`execution/service.py:1736 _run_collection` 已完整实现按 source 采集、复用 `collect_source`、逐源提交与失败隔离；`TaskConfig`（`execution/config.py:342`）已支持 `type: collection` + `sources`。新增端点会形成平行实现。

### R2 存在真实可用的研究 Task

`config/tasks.yaml` 中至少有一个非示例的研究 Task，其 `scope` 与 objective 反映 Owner 的真实研究意图，可被 Web「任务」页手动触发并产出一份可阅读报告。示例 Task 的保留或移除由 Owner 决定。

### R3 处理失败可诊断

> **2026-10-25 范围收缩：** 并行任务 `10-25-fact-stage-robustness`（commit `377d17e`）已实现并部署 R3 的大部分：
> - PROCESSOR_FAILED 兕底现在保留异常类名（`detail.exception`）并写进程日志；
> - 期间表达式容错（年份/季度/半年→UTC 区间；不可识别→置空 + `attributes.period_raw`）；
> - 同一响应内重复 Fact 身份去重；
> - 5 个 failed 阶段已全部修复（服务器实测 0 failed，20/20 文档 facts 完成，Fact 71→130）。
>
> 本任务 R3 仅剩**进程日志配置**：`basicConfig` 仍缺失，Worker 容器日志为 0 行，`logger.exception` 的输出无处可看。

`facts` 阶段失败时，Owner 能知道真实原因，而不是只有 `PROCESSOR_FAILED` 兜底码；Worker 日志能在容器日志中看到。已存在的 5 个 failed 阶段能被识别并给出可执行的下一步。

### R4 数据流动被验证

在测试服务器上实际完成一次「采集 → parse/classify/facts/index → 检索命中」或「研究 Task → 报告可读」的端到端验证，并记录脱敏证据。

## 验收条件

- [x] AC-1（R1）：Web「任务」页出现 collection Task，点击「手动触发」返回 queued Run；Worker 执行后该来源 `latest_attempt_at` 更新，新 Document 入库并生成 4 个阶段待办。（探测库实测：Run `4ab85aa2` succeeded，40 文档四阶段完成）
- [x] AC-2（R1）：collection Task 引用的来源若未配置或已禁用，触发返回稳定错误码（非 500），且不产生半个 Run。（代码核实 `SOURCE_NOT_FOUND`/`SOURCE_DISABLED` 已实现；探测库 40 文档仅来自两个启用源）
- [x] AC-3（R2）：`tasks.yaml` 至少一个真实研究 Task 在 Web「任务」页可见且可触发，Run 成功后 `/api/reports` 返回可读报告。（Run `628a9522` succeeded，报告 28,227 字符、28 findings、71 证据引用）
- [x] AC-4（R3）：`facts` 阶段异常时，持久化的 error 含可区分的错误码，且 Worker 日志记录原始异常栈。（由 `377d17e` 实现并验收；本任务仅补进程日志配置）
- [x] AC-5（R3）：已 failed 的 5 个 `facts` 阶段可通过既有 `retry` 能力重新排队，且 Owner 能从界面或 CLI 看到当前失败原因。（`10-25-fact-stage-robustness` 已验收：0 failed）
- [x] AC-6（R4）：测试服务器上完成一次真实闭环，证据（脱敏的 run_id、计数、响应片段）记录在任务 `acceptance.md`。

## 不在范围内

- 不新增前端图表、不做概览页重构（属 `10-25-overview-data-exhibition`）。
- 不实现公网 HTTPS / 多用户 / 权限模型。
- 不实现外部 MCP Tool 接入（`config/tools.yaml` 保持 `tools: []`）。
- 不修改向量维度、不写数据迁移（开发阶段直接改当前建表定义）。
- 不改 Research Agent 的输出 schema 与提示词语义。
- 不实现定时调度长期运行验收。

## 依赖与顺序

R1 与 R2 可并行；R3 独立；R4 依赖 R1、R2、R3 完成后才能执行。
