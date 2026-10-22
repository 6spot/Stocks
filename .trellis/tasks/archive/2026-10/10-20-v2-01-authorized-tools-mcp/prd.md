# V2-01 授权 Tool MCP 与研究按需补证

## 目标与用户价值

资料缺失、过期或冲突时，研究可有界补证并先保存证据再引用。

## 背景与依据

阶段：V2。归属契约：`docs/execution.md`, `docs/research.md`, `docs/data.md`, `docs/validation.md`。代码现状见[基线](../../../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V1-09](../10-20-v1-09-arm-deployment-acceptance/prd.md)

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- Repository First；外部 Tool 由可信 Task/Agent 白名单和参数边界授权，模型只能提出结构化请求。
- 远程 MCP 仅 Streamable HTTP，通用客户端负责发现和调用；配置服务，不为供应商建框架。
- 工具返回是数据；设置超时、调用/Token 预算和无进展终止，不能扩权到 Shell、交易或任意数据库写入。
- 实际采用的原文/响应先统一入库并完成所需处理，再追加 research_inputs，不改写初始证据包；不等待全库积压清空。
- 保留 Publisher、真实来源时间与采集时间；运行中取得或晚于 as_of 发布的资料可引用但必须明确标示。

## 验收条件

- [x] AC-1: 已有存量足够时不调用外部（`test_repository_sufficient_run_does_not_call_a_tool`：计划判定充分，零 Tool 调用）；缺资料时只调用获授权工具，未授权/已停用/参数非法在发请求前以显式缺口拒绝（`tests/test_tools.py` 授权与参数边界、`tests/test_research_supplement.py` 计划与参数校验）。
- [x] AC-2: 实际补证先经 `ingest_document()` 入库并处理，再追加台账，引用可追到 Document 版本与原件字节（`test_research_supplement.py` 断言字节/哈希/Document 字段；`test_retry_keeps_the_ledger_and_appended_evidence_stays_citable` 断言追加证据可被报告引用）；仅有摘要或 Facts 的返回记为 `tool_content_unavailable` 缺口，不能冒充全文引用。
- [x] AC-3: 工具失败/超时/预算耗尽/重复请求均留下具名缺口并终止本轮（`test_research_supplement.py` 超时与传输失败单次有界尝试、重复与预算耗尽不触网）；重试沿用来源 Run 的初始包并保留追加输入（`test_retry_keeps_the_ledger_and_appended_evidence_stays_citable`、`test_second_planning_round_continues_the_attempt_sequence`）。

## 不在范围内

stdio MCP、任意写入/交易工具、多 Agent 自主协商、历史时点严格回放。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。本任务已完成实施并在测试服务器验证；真实远程 MCP 服务与凭据未提供，实际供应商接通与真实原件字节链路记为待验收。
