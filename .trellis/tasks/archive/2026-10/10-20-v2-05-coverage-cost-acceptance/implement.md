# V2-05 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务 V2-02 与 V2-04 已归档完成（提交 `511b677`）。
- [x] 重新检查现有代码及测试：`ModelCall` 已保存 alias/模型名/Token/耗时/回退原因但没有 Provider 与费用列；Overview 只有采集/处理/运行/报告聚合，没有研究覆盖与用量；报告页已有覆盖缺口、独立原件与补证台账，但无法从报告跳到 Run 的模型调用。结论：需要一次 `model_calls` 列变更 + 只读聚合接口 + 控制台展示，不需要新表或新服务。
- [x] 用户授权按 V2 任务列表逐个实施；真实第三方与人工验收按会话约定延后记录，不阻塞实现。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线，按实际改动裁剪复核。

## 执行顺序

- [x] 补齐覆盖与用量查询、空值语义及必要索引：`model_calls` 增加 `provider`、`cost_usd`（`numeric(14, 6)`，只改 0001 初始化定义）；`stocks.providers` 解析供应商返回的 `usage.cost` / `usage.cost_usd`（非数值拒绝，未返回保持 `None`），`_persist_model_attempts` 落库，`_usage_from_attempts` 与 Agent 步骤 usage 带 `cost_usd` / `cost_unknown_attempts`；`api._usage_summary()` 只从 `ModelCall` 行确定性汇总，`api._latest_research_coverage()` 读最近一份成功报告的证据包（失败 Run 不参与），`/api/overview`、`/api/runs/{run_id}`、`/api/reports/{report_id}` 返回 `research` / `usage` / `provider` / `cost_usd`。
- [x] 扩展现有控制台的分析和异常入口：Overview 新增「研究覆盖」与「近期模型用量」卡片（缺口用既有词表标签显示），Run 详情按步骤显示用量、调用表新增 Provider 与费用列并给出 Run 级汇总，报告页新增「本次研究用量」与跳转该 Run 的入口；重试/续扫/结束 Run/处理积压继续复用既有服务，未新增调度、告警或监控平台。
- [x] 执行 V2 阶段综合验收，保存真实操作与限制记录（详见下方最小必要验证与 `docs/validation.md` V2-05 段落）。

## 最小必要验证

- [x] 测试服务器 `100.120.14.84` 隔离目录 `/tmp/stocks-v2-05-coverage-usage`（独立 PostgreSQL `stocks-test-v2-05-postgres` 127.0.0.1:55442、自有 seaweedfs `stocks-test-v2-05-s3` 与 bucket 18339）：
      `tests/test_coverage_usage_api.py tests/test_providers.py` 12 passed（新增覆盖未知候选/未知费用保持未知、失败 Run 不充当最近成功覆盖、Run 与报告的 Provider/费用/缺口、供应商返回费用与非法费用拒绝）；
      `tests/test_execution.py` 63 passed（含新增全流程用例：真实执行一次 Run 后 ModelCall 记录 Provider 与供应商费用，步骤 usage 带费用与未知计数）；
      `tests/test_console_api.py tests/test_report_api.py tests/test_report_comparison.py` 与 `tests/test_evidence_conflicts.py tests/test_collection.py tests/test_research.py tests/test_api.py` 全部通过。
- [x] `model_calls` 结构变更按全新空库验证：`alembic upgrade head`（0001）后 `alembic check` 报告 `No new upgrade operations detected`。
- [x] 前端（同一远程目录）：`npm run typecheck` 通过；`npm run test` 8 文件 15 tests passed（含费用未知不显示为 0 的用例）；`npm run build` 通过。
- 未验收（不阻塞后续任务，留给最终验收）：真实返回费用的第三方端点与真实远程 MCP 服务未接通；真实连续两次研究的报告对照人工复核、真实同源冲突样例与调度/限流下的长期有界终止仍待 Owner 验收；自动投资评分、动态成本路由与独立监控平台不在范围内。

按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制（见 `prd.md` 验收条件与 `docs/validation.md` 的 V2-05 段落）。
- [x] 契约变更只更新唯一归属文档：`docs/execution.md`（ModelCall 与 usage 费用语义）、`docs/interface.md`（Overview/Runs/Research 展示）、`docs/validation.md`（验收边界）；运行能力更新 `docs/quickstart.md`（usage/research 字段与空值语义）。
- [x] 后续依赖检查：V2-05 是本阶段最后一个任务，之后只剩 Owner 最终验收；本任务未改写任何既有 Report、Evidence Package 或原始证据。
- [x] 完成实现与质量检查后按 Trellis 工作流交付：代码、文档、任务记录同一次提交，随后归档任务并记录交付提交号。
