# V2-01 授权 Tool MCP 与按需补证验收记录

- 验收日期：2026-10-22
- 提交：`0359a6f`（`feat(tools): add authorized MCP retrieval with a research input ledger`）
- 环境：测试服务器 `100.120.14.84` 隔离目录 `/tmp/stocks-v2-01-tools-mcp`，独立
  PostgreSQL/pgvector 与任务自有对象存储 bucket；未在本机执行任何测试。

## 结论

AC-1 至 AC-3 的代码与自动化验证通过。**真实远程 MCP 服务与凭据未提供**，真实供应商
接通、真实原件字节链路与第三方限流行为仍为待验收项，已登记在
`docs/validation.md`「待最终验收」。

## AC-1：存量足够不调用外部；只调用获授权工具

结果：通过。

- `test_repository_sufficient_run_does_not_call_a_tool`：计划判定存量充分时零 Tool 调用。
- `tests/test_tools.py`：授权与参数边界。拒绝路径包括未授权、已停用与参数非法，均在
  发出请求前以显式缺口拒绝。
- `tests/test_research_supplement.py`：计划与参数校验。

## AC-2：先入库再追加台账，引用可追到版本与原件字节

结果：通过。

- 补证取得的原件先经 `ingest_document()` 入库并处理，再追加 `research_inputs`；
  初始 Evidence Package 保持冻结，不被改写。
- `test_research_supplement.py` 断言字节、哈希与 Document 字段；
  `test_retry_keeps_the_ledger_and_appended_evidence_stays_citable` 断言追加证据可被
  报告引用。
- 仅返回摘要或 Facts 而无全文时记为 `tool_content_unavailable` 缺口，不能冒充全文引用。

## AC-3：失败/超时/预算耗尽/重复请求有界终止

结果：通过。

- 超时与传输失败只做单次有界尝试；重复请求与预算耗尽不再触网，均留下具名缺口并终止
  本轮。
- `test_second_planning_round_continues_the_attempt_sequence` 断言重试沿用来源 Run 的
  初始包、保留追加输入并继续尝试序号。

## 未执行与限制

- **真实远程 MCP 服务未接通**：没有可用的服务地址与凭据，因此真实供应商发现与调用、
  真实原件字节链路、第三方限流与超时行为未在真实服务上验证。测试使用固定替身。
- 真实供应商凭据只从命名环境变量读取，端点、请求头与请求体不进入快照、ModelCall 或日志；
  本记录不含任何凭据。
