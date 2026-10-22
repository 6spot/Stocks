# V2-01 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务 [V1-09](../10-20-v1-09-arm-deployment-acceptance/prd.md) 已归档完成。
- [x] 重新检查现有代码及测试，落实路径、Schema 和必要技术决策（见 design.md）。
- [x] 用户授权按任务列表逐个实施；实施范围见 PRD“范围与要求”。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线。

## 执行顺序

- [x] 定义可信 Tool 配置（`config/tools.yaml` + `stocks.tools.config`）、稳定错误码（`stocks.tools.errors`）与远程 MCP Streamable HTTP 客户端（`stocks.tools.client`）。
- [x] `AgentConfig.tools` 改为有界去重授权列表；`Settings.tools_config_path`；Run 快照写入授权与配置身份。
- [x] 台账落在 `task_runs.research_inputs`（追加型 JSONB 数组 + `jsonb_typeof` 检查），执行时冻结到 `analyses.research_inputs`；`model_calls` 增加 `call_purpose` 并收紧唯一约束；同步修改 `0001_initial.py`（最终设计见 design.md，不再给 Evidence Package 增加列）。
- [x] `stocks.research.supplement`：结构化补证计划、调用边界、入库与处理、追加与缺口、无进展终止。
- [x] `_run_agent` 接入合并视图与补证循环；重试拷贝台账并重映射边界包 ID。
- [x] 报告详情、单条证据接口与缺口渲染改用合并视图。
- [x] 更新归属文档与 `config/tasks.yaml` 空证据语义。

## 决策记录

- 远程 MCP 固定 `2025-11-25`（initialize 握手 + `Mcp-Session-Id`）；不实现 stdio，不支持无状态新时代路由头。
- 单次 Run 的补证轮次上限 2，每次计划最多 4 个请求；单 Tool 预算由 `ToolBudgets` 决定，token 预算按 `estimate_tokens()` 的 4 字符≈1 token 估算。
- 补证输入落在 TaskRun.research_inputs，完成时冻结到 Analysis.research_inputs；Evidence Package 保持初始选择不变。重试分别深拷贝初始包与追加台账，并重映射边界包 ID。
- 执行注入点统一为 `ExecutionContext.tool_client_factory` / `stage_registry` / `snapshot_store`，测试与部署共用同一条执行路径。

## 最小必要验证

- 测试服务器 `100.120.14.84` 隔离目录：`python -m pytest -q tests/test_tools.py tests/test_research_supplement.py` + 受影响的 `test_execution.py` / `test_report_api.py` 用例。
- 全新开发库：`alembic upgrade head` 与 `alembic check`，确认当前模型与初始化结构一致。
- 用实际配置的远程 MCP 服务验证协议与原件链路；无凭据时明确标记实际接入未验收。

## 实际验证结果（2026-10-22，测试服务器隔离目录 `/tmp/stocks-v2-01-tools-mcp`）

- `pytest -q tests/test_tools.py tests/test_research_supplement.py tests/test_execution.py tests/test_report_api.py tests/test_research.py tests/test_console_api.py tests/test_api.py tests/test_scheduler.py tests/test_worker_processing.py tests/test_collection.py` → **229 passed**（唯一 warning 为第三方 Starlette `TestClient` 弃用提示）。
- 全新空库 `alembic upgrade head`（0001，exit 0）+ `alembic check`（No new upgrade operations detected，exit 0）。
- `python -m stocks.execution --help` 显示新增 `--tools-config`。
- 修复过程中额外覆盖：`_persist_model_attempts` 多轮 `tool_plan` 序号续排（`test_second_planning_round_continues_the_attempt_sequence`）、空证据 Run 成功路径、Repository 充分时不调用任何 Tool、重试保留台账并可引用追加证据。
- 未验证：真实远程 MCP 服务与凭据、真实原件字节链路、第三方限流行为。

新增测试名为计划，文件存在后才执行；不得把本文件命令写成已通过。按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制（见 PRD AC、上方远程验证记录与 task.json notes）。
- [x] 契约变更只更新唯一归属文档，运行能力更新 quickstart（归档 notes 已记录文档和规范同步）。
- [x] 检查后续依赖与阶段验收；保留原始证据、输入快照及最后成功结果（真实 MCP 接通仍待最终验收，未计作通过）。
- [x] 已完成实现/质量检查并按 Trellis 工作流交付归档；本项据既有归档记录补齐，不代表真实供应商验收通过。

## 上下文读取注意

`docs/data.md` 当前为 34,267 字节，超过默认单文件注入上限 32,768 字节。自动注入可能截断末尾；派发/开工时须完整读取原文件，尤其末尾检索与来源安全契约。不要把已注入前缀当成全文；无需为此修改领域文档或 Trellis 全局配置。
