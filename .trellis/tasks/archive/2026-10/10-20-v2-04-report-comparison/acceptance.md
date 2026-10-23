# V2-04 历史报告比较验收记录

- 验收日期：2026-10-22
- 提交：`511b677`（`feat(research): compare saved reports against their own evidence`）
- 环境：测试服务器 `100.120.14.84` 隔离目录 `/tmp/stocks-v2-04-report-comparison`，
  独立 PostgreSQL 端口 `55441` 与任务自有对象存储 bucket `18338`。

## 结论

AC-1 至 AC-3 通过。「真实连续两份报告的人工对照」需要至少两次成功的真实研究运行，
未执行，登记为待最终验收。

## AC-1：接口与报告页可逐项对照两份成功报告

结果：通过。

- `GET /api/reports/{report_id}/comparison?other_report_id=...` 返回结论新增/消失/持续
  （含不确定性与引用变化）、Document 新增/消失/同一来源新版本、同指标取值修订、覆盖缺口
  增减与晚到资料。
- `documents.persisting[].earlier` / `.later` 分别给出两侧可打开的 Document 版本，
  测试断言两次实际原件入口。
- Web 报告页「历史报告对照」卡片可选择两份成功报告，默认预选同一 Task 中最近的一份成功
  报告；无对照对象时不显示卡片。

## AC-2：同指标修订与覆盖缺口计算规则

结果：通过。

- Fact 按 `(subject_name, symbol, metric_id)` 比较，标出 `value_changed` /
  `basis_changed` / `period_changed` 并给出两侧真实取值（示例 2.4 → 2.1，不出现中间值）。
- 晚于较早报告 `as_of` 发布或采集的 Document 列在 `late_material`，解释为真实来源变化。
- `unexamined_count` 只从 Evidence Package 读取，未知保持 `null` 不补 0；两侧 coverage
  与缺口增减分开显示；Task 或 Scope 不同、`as_of` 未推进时给出 `warnings[]`。
- 每组差异截断于 50 条并带 `truncated`，完整快照仍在各自报告与 Evidence Package 中。

## AC-3：失败 Run 与稳定性边界

结果：通过。

- 失败或未完成 Run 的报告返回 409，不能充当成功历史报告；同一报告自比返回 400，未知
  报告返回 404。
- 比较只读两份报告各自保存的 Analysis、Evidence Package 与引用版本，不重新研究、不调用
  Tool、不改写旧报告。
- 测试断言较早报告在新版本入库后仍引用当时版本。

## 实际执行的测试

测试服务器隔离目录：

- `test_report_comparison`：4 passed
- `test_report_api` + `test_api` + `test_research` + `test_execution` + `test_evidence_conflicts`：
  94 passed
- 全新空库 `alembic upgrade head` + `alembic check`：通过，无差异
- 前端 `npm run typecheck` 通过；`vitest` 7 文件 14 tests passed

该任务不涉及数据库结构变更。

## 未执行与限制

- **真实连续两份报告的人工对照未执行**：需要同一 Task 至少两次成功的真实研究运行，
  阻塞于模型 Provider 凭据（同 PA-1）。
- **真实修订样例未取得**：「同一 Document 新版本被后续报告引用」与「同一指标跨报告修订」
  需要真实来源与自然演进的数据。
- 逐条 Finding 关系图、严格历史知识回放、虚构价格或收益对比明确不在范围。
