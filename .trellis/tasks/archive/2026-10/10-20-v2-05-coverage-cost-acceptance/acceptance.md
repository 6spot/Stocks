# V2-05 覆盖费用与 V2 验收记录

- 验收日期：2026-10-22
- 提交：`ba03897`（`feat(console): show research coverage and provider-reported cost`）
- 环境：测试服务器 `100.120.14.84` 隔离目录 `/tmp/stocks-v2-05-coverage-usage`，
  独立 PostgreSQL 端口 `55442` 与任务自有对象存储 bucket `18339`。

## 结论

AC-1 至 AC-3 通过。真实返回费用的端点、真实远程 MCP 与真实连续两份报告的人工复核未执行，
按 Owner 授权延后到最终验收，已记录在 `docs/validation.md` 的 V2-01～V2-05 段落。

## AC-1：报告页与 Overview 可定位覆盖与调用，未知保持未知

结果：通过。

- 报告页显示 `coverage_gaps`（来源/解析/索引缺口）、研究覆盖（候选发现/筛选/入选、
  独立原件、未扫描候选、补证输入）与本次 Run 的模型用量，并可跳转到该 Run 的步骤与
  模型调用。
- Run 详情按步骤与每次调用显示实际 Provider、模型、Token、耗时、回退原因与费用。
- Overview 给出最近一份成功报告证据包的覆盖与近期 Run 用量；失败 Run 不充当最近成功覆盖
  （测试断言）。
- 未知候选剩余（`unexamined_count` / `candidates_discovered`）与未提供费用都返回 `null`
  并显示「未知」，不显示 0 或「已覆盖」。

## AC-2：费用与数值只来自已保存记录

结果：通过。

`usage` 汇总由 `ModelCall` 行确定性计算：调用数、Token、耗时、费用、未提供费用次数与
有界的按模型分解。`cost_usd` 只累加供应商在响应 `usage.cost` / `usage.cost_usd` 中返回的
金额，未返回时保持未知并标注次数，**不用本地价格表推算**。图表与文本不经过 LLM，报告
正文只由已存 Analysis 确定性生成。

`providers` 对非数值费用做拒绝（bool、字符串、非有限、负数、超限），并按 6 位小数保存到
`numeric(14, 6)`。数据库只改 `0001` 初始化定义，无增量迁移。

## AC-3：V2 自动化验收在同一隔离环境重跑通过

结果：通过。

- `test_coverage_usage_api` + `test_providers`：12 passed
- `test_execution`：63 passed（含新增全流程用例：真实执行一次 Run 后 ModelCall 记录
  Provider 与供应商费用、步骤 usage 带费用与未知计数）
- `test_console_api` + `test_report_api` + `test_report_comparison` +
  `test_evidence_conflicts` + `test_collection` + `test_research` + `test_api`：通过
- 全新空库 `alembic upgrade head` + `alembic check`：通过，无差异
- 前端 `npm run typecheck` 通过、`vitest` 8 文件 15 tests passed、生产构建通过

## 未执行与限制

按 Owner 授权延后到最终验收，均未视为已通过：

- **真实返回费用的端点**：费用是否可得取决于供应商响应；当前 OpenAI 兼容端点通常不返回
  `cost`，需要真实返回费用的端点才能确认费用落地与非零显示。
- **真实远程 MCP**：见 V2-01。
- **真实连续两次成功研究的人工报告复核**：见 V2-04，阻塞于模型 Provider 凭据。
- **真实同源冲突样例**：见 V2-03。
- **调度与限流下的长期有界终止**：需真实持续运行观察，不用模拟结果代替。

自动投资评分、动态成本路由与独立监控平台明确不在范围。
