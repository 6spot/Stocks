# V2-04 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务 [V2-03](../10-20-v2-03-source-conflicts/prd.md) 已归档完成（提交 `8a8bcc3`）。
- [x] 重新检查现有代码及测试：报告链路为 Report → Analysis → Evidence Package → `_report_research_refs`/`_resolve_evidence` 的引用视图，`_research_ledger` 提供最终输入台账；`origin_groups`/`metric_comparisons` 已在 V2-03 落库，因此比较只需读快照，无需新表或新列。
- [x] 用户授权按 V2 任务列表逐个实施；真实双报告人工对照与真实修订样例按会话约定延后记录，不阻塞实现。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线，按实际改动裁剪复核。

## 执行顺序

- [x] 定义有界历史查询及比较输入：新增 `stocks.research.comparison.compare_report_sides(earlier, later)`，输入是两侧已保存快照（Analysis findings/open_questions、Evidence Package 的 scope/as_of/coverage、引用 Document 版本与 Fact），每组差异 `_bounded()` 截断于 `MAX_COMPARISON_ITEMS = 50` 并带 `truncated`。
- [x] 实现原始证据差异与统一阅读界面的前后比较：`_finding_diff`（仅 `(kind, 规范化 statement)` 全等算持续，标 `uncertainty_changed`/`rationale_changed`/`evidence_changed`）、`_document_diff`（键为 `(source_id, dedupe_key)`，缺失时 `document:<id>`；合并同一 Document 的多段引用，标 `version_changed`/`content_changed`）、`_fact_diff`（按 `(subject_name, symbol, metric_id)`，标 `value_changed`/`basis_changed`/`period_changed` 并保留两侧取值）、`_coverage_diff`、`_late_material`、`_warnings`（`different_task`/`different_scope`/`as_of_not_advancing`）；接口 `GET /api/reports/{report_id}/comparison?other_report_id=...` 按 `created_at` 排序并给出 `ordering.swapped`，自比 400、未知 404、Run 非 `succeeded` 409；Web 报告页新增「历史报告对照」卡片（默认预选同一 Task 最近的成功报告，两侧原件可分别打开）。
- [x] 验证修订、缺口和旧引用不变并记录真实示例（详见下方最小必要验证）。

## 最小必要验证

- [x] 测试服务器 `100.120.14.84` 隔离目录 `/tmp/stocks-v2-04-report-comparison`（独立 PostgreSQL `stocks-test-v2-04-postgres` 127.0.0.1:55441、自有 seaweedfs `stocks-test-v2-04-s3` 与 bucket）：
      `tests/test_report_comparison.py` 4 passed（新增；覆盖排序与请求侧交换、结论新增/消失/持续与不确定性变化、Document 版本与内容变化、同指标修订取值、覆盖缺口与未知未扫描数量、晚到资料、自比 400 / 未知 404 / 失败 Run 409、旧报告引用不变，并打开两次实际原件）；
      `tests/test_report_api.py tests/test_api.py tests/test_research.py tests/test_execution.py tests/test_evidence_conflicts.py` 94 passed（1 个第三方 Starlette 弃用 warning）。
- [x] 该任务无数据库结构变更：全新空库 `alembic upgrade head`（0001）后 `alembic check` 报告无差异。
- [x] 前端（同一远程目录）：`npm run typecheck` 通过；`npm run test` 7 个文件 14 tests passed（含新增的报告对照用例，断言默认预选、两侧原件链接、无对照对象时卡片不渲染）。
- 未验收（不阻塞后续任务，留给最终验收）：真实连续 Run 的两份报告人工对照；真实「同一 Document 新版本被后续报告引用」与「同一指标跨报告修订」的成对样例；逐条 Finding 关系图、严格历史知识回放与虚构价格/收益对比不在范围内。

按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制（见 `prd.md` 验收条件与 `docs/validation.md` 的 V2-04 段落）。
- [x] 契约变更只更新唯一归属文档：`docs/research.md`（比较契约与匹配规则）、`docs/interface.md`（报告页对照入口与呈现）、`docs/validation.md`（验收边界）；运行能力更新 `docs/quickstart.md`（对照接口与状态码）。
- [x] 后续依赖检查：V2-05 覆盖费用与异常分析只读既有 Analysis/Report/覆盖统计，本任务未改数据模型，既有 Report 与 Evidence Package 快照不被改写。
- [x] 完成实现与质量检查后按 Trellis 工作流交付：代码、文档、任务记录同一次提交，随后归档任务并记录交付提交号。
