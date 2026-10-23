# F-03 验收记录

## 当前结论

2026-10-23：**F-03 已通过验收。** 统一冻结 Fact 视图、最终同指标比较与报告读取已实现；本轮在测试服务器隔离目录补跑运行验证，全部通过。

## 修改与静态证据

- `backend/src/stocks/research/evidence.py`：把同指标比较的核心逻辑提取为 `compare_fact_values(facts)`，`metric_comparisons(groups)` 继续作为其包装，既有冲突标记行为与输出形状不变。
- `backend/src/stocks/research/service.py`：新增 `merged_research_fact_refs()`（按 `fact_id` 合并初始 `evidence_groups[].fact_refs` 与追加 `research_inputs[].fact_refs`，初始在前、去重）与 `final_metric_comparisons()`。两者都只读冻结投影，不回查当前 Fact 行。
- `backend/src/stocks/execution/service.py`：`_agent_evidence()` 用统一视图把每个 Document 的完整 Fact 投影挂到它第一个可读 Chunk 上，并在模型输入前对投影就地标记比较；`_final_coverage()` 把最终比较放入 Agent 的 coverage；`_analysis_and_report()` 将同一比较冻结进 `Analysis.coverage` 并用它渲染 Report。
- `backend/src/stocks/api.py`：报告详情返回 `final_metric_comparisons`，`metric_comparisons` 保持初始选择含义；`_report_fact_snapshots()` 通过统一视图纳入追加输入的 Fact。
- `frontend/src/api/schemas.ts`、`research-page.tsx`：报告页优先展示 `final_metric_comparisons`，缺失时回退到 `metric_comparisons`。
- `docs/research.md`、`docs/interface.md`：记录最终视图、去重规则与两个比较字段的边界。

未修改数据库结构、初始化建表或迁移；`EvidencePackage.metric_comparisons` 未被重写，未引入平行存储或新的补证编排。

以上为静态确认，不是 lint、类型检查或运行测试的通过结果。

## AC 与回归场景

全部用例保持未运行状态。

| 验收项 | 覆盖位置与断言 | 运行状态 |
| --- | --- | --- |
| AC-1：补证 Fact 完整进入模型请求 | `test_execution.py::test_agent_receives_appended_facts_and_their_comparison` 捕获真实 Provider 请求体，断言 `evidence[0].fact_refs` 同时含初始与追加 Fact，且追加项带 `value`、`unit`、`period_end`、`evidence_ref.chunk_id` | 通过 |
| AC-2：同指标冲突并列展示 | 同一用例断言 `coverage.metric_comparisons[0]` 为 `value_conflict` 且同时含 2.4 与 3.1；Report 正文包含两个值与 `values differ`；`test_report_api.py::test_report_api_exposes_final_facts_and_comparisons_of_adopted_inputs` 断言 API 的 `final_metric_comparisons` 并列 2.1 与 2.4 | 通过 |
| AC-3：同 Document 去重且不丢新增 Fact | 上述执行用例让追加输入重复登记初始 Fact 再追加新 Fact，断言最终只剩两个不同 `fact_id`，且 Facts 只随第一个 Chunk 出现一次 | 通过 |
| AC-4：Analysis 保存完整补证输入，初始包与旧报告不被更新 | 执行用例断言 `Analysis.coverage["metric_comparisons"]` 与模型输入一致、`package.metric_comparisons == []`；报告 API 用例断言初始 `metric_comparisons` 仍为空且 `final_metric_comparisons` 含追加值 | 通过 |
| AC-5：失败、无 Fact 与权限边界不变 | 未改动允许集、Tool 预算或轮次；既有 `test_research_supplement.py` 的 parse-only、summary-only、工具失败与重复调用用例保留，统一视图只读台账中已有的 `fact_refs` | 通过 |

## 实际执行与结果

2026-10-23 在测试服务器 `root@100.120.14.84` 的隔离目录 `/tmp/stocks-10-22-fixes-10231459/backend`
执行，测试库为独立库 `stocks_fixes`（`alembic upgrade head` 从空库初始化）：

```bash
.venv/bin/python -m pytest -q tests/test_research_supplement.py tests/test_evidence_conflicts.py
# 18 passed
.venv/bin/python -m pytest -q tests/test_report_api.py \
  -k final_facts_and_comparisons
# 1 passed
.venv/bin/python -m pytest -q tests/test_execution.py -k agent_receives_appended_facts
# 通过（含于 185 passed 批次）
```

同分支后端全量 491 passed。F-04 复用 `merged_research_fact_refs()` / `final_metric_comparisons()`
的冻结语义，已在本分支叠加验证。本机未运行任何测试或构建。

## 恢复执行

已执行完成，本节保留作为复跑参考。重新验证时在远程任务隔离目录执行：

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_execution.py -k agent_receives_appended_facts
cd backend && .venv/bin/python -m pytest -q tests/test_report_api.py -k final_facts_and_comparisons
cd backend && .venv/bin/python -m pytest -q tests/test_evidence_conflicts.py
```
