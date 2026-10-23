# 修复实现与设计一致性偏差

## 目标与授权

修复当前实现偏离既有设计的五项行为，恢复历史证据可追溯、冻结输入可重试、补证与初始证据一致使用，以及控制台可用性。

Owner 于 2026-10-22 明确要求梳理发现并创建任务，交给其他 Agent 开发。本轮仅建立规划与交接材料；本父任务和子任务均保持 planning，未启动实施。父任务负责范围、依赖及最终集成验收，实施选择具体叶子任务。

## 来源与已知限制

来源：[静态审查记录](../10-20-stocks-roadmap/research/implementation-design-review.md)。F-01 至 F-05 均经实际调用链静态核对，尚未运行复现。接手 Agent 应在测试服务器补充可失败的关键回归场景，不能将静态结论或历史测试结果写成新验证通过。

## 交付拆分

| 发现 | 优先级 | 子任务 | 要解决的行为 | 前置 |
| --- | --- | --- | --- | --- |
| F-01 | P1 | [Fact 历史身份](../10-22-fix-fact-reprocess-history/prd.md) | 同版本显式重处理删除旧 Fact、使历史引用失效 | 无 |
| F-02 | P1 | [历史批次重试](../10-22-fix-retry-historical-parse/prd.md) | current 批次检查错误拒绝已冻结的历史证据 | 无 |
| F-03 | P1 | [补证 Fact 输入](../10-22-fix-supplement-fact-inputs/prd.md) | 台账已有 Fact 未完整进入 Agent 和最终冲突视图 | 无 |
| F-04 | P2 | [实际引用比较](../10-22-fix-report-cited-comparison/prd.md) | 将整个入选集合误计为报告引用变化 | F-01 |
| F-05 | P2 | [积压默认筛选](../10-22-fix-processing-default-filter/prd.md) | 首次请求把 failed 当作 stage | 无 |

## 范围与约束

- 按既有领域文档恢复行为，不借机设计通用事实纠错版本系统、多 Agent 编排或新数据服务。
- 初始 EvidencePackage 保持冻结，补证台账和实际引用各有明确语义，不将三者混用。
- 继续拒绝伪造引用、越权 Tool 证据及不匹配的 Document/Chunk/版本。
- 开发阶段不维护旧版兼容、不编写数据库迁移；若结构确需调整，修改当前模型及初始化，在全新开发库验证。
- 真实 Provider/MCP、ARM 等延期验收继续由[既有清单](../10-20-stocks-roadmap/pending-acceptance.md)管理，不纳入本修复的通过条件或自动标记完成。

## 父任务验收

- [x] I-1：五个子任务的 AC 均有实际验证或明确未完成项，没有仅凭子任务归档替代集成验收。
- [x] I-2：同一条证据链经历 Fact 重处理、产生新解析批次、失败 Run 重试后，旧输入仍可读取，原报告及其历史比较不被改写。
- [x] I-3：补证得到的冲突 Fact 同时可在 Agent 输入、冻结研究输入和最终来源/冲突展示中定位；未实际引用的材料不被报告比较算作引用新增。
- [x] I-4：积压面板首次加载发送合法筛选，服务端可返回结果；无越权引用或来源丢失的回归。

## 完成记录

2026-10-22：五个叶子均已实施、提交、归档并推送到各自的 `fix/*` 分支；父任务仍为 `planning`，因为集成验收需要实际运行测试，而当前会话没有可用的测试服务器 SSH 认证。

| 叶子 | 分支 | 代码提交 | 运行验证 |
| --- | --- | --- | --- |
| F-01 | `fix/fact-reprocess-history` | `8e6e459` | 未执行 |
| F-02 | `fix/retry-historical-parse` | `8341e01` | 未执行 |
| F-03 | `fix/supplement-fact-inputs` | `7ab3b14` | 未执行 |
| F-04 | `fix/report-cited-comparison` | `3de76df` | 未执行 |
| F-05 | `fix/processing-default-filter` | `d65152c` | 未执行 |

所有叶子的 AC 均保持未勾选；代码改动与静态复核已完成，测试、类型检查、构建与 API smoke 全部未运行。各分支为顺序叠加（每个分支包含前序修复），尚未合并到 `main`，也未创建或合并 PR。父任务 I-1 至 I-4 因此均未确认；`docs/validation.md` 与 [延期验收清单](../10-20-stocks-roadmap/pending-acceptance.md) 中的真实 Provider/MCP、ARM 等延期项不受本修复影响。

恢复集成验收时，需取得测试服务器认证，按各叶子 `acceptance.md` 的命令补跑最小范围测试，并在一个集成分支上验证 I-2/I-3 的联合场景。技术设计及执行顺序分别见 design.md、implement.md。

## 2026-10-23 集成验收结果

测试服务器 `root@100.120.14.84` 已建立 SSH 公钥免密认证；验证在隔离目录
`/tmp/stocks-10-22-fixes-10231459` 与独立库 `stocks_fixes`（复用 pgvector 容器
`stocks-fix5-1792666779-postgres`，`127.0.0.1:55440`）中执行，测试库由
`alembic upgrade head` 从空库初始化。未在本机运行任何测试或构建。

| 轮次 | 命令范围 | 结果 |
| --- | --- | --- |
| F-03/F-04 最小范围 | `test_report_comparison.py`、`test_report_api.py`、`test_evidence_conflicts.py`、`test_research_supplement.py` | 37 passed |
| F-01/F-02 最小范围 | `test_facts.py`、`test_processing_retry.py`、`test_processing.py`、`test_research.py`、`test_execution.py` | 185 passed |
| I-2/I-3 联合场景 | `test_facts.py`、`test_processing_retry.py`，加 reprocess/比较/冻结取值/合并证据 4 个具名用例 | 77 passed |
| 后端全量回归 | `pytest -q`（五个修复叠加后的当前分支） | 491 passed |
| F-05 前端 | `npm test`（vitest） | 8 files / 17 tests passed |
| F-05 构建 | `npm run build`（含 `tsc -b` typecheck） | 构建成功 |

- **I-1**：五个叶子的运行验证由上表覆盖，AC 已可勾选；真实 Provider/MCP、ARM 与移动端仍由延期清单管理，不计入本修复通过条件。
- **I-2**：`test_reprocessing_keeps_a_report_fact_readable_and_its_comparison_unchanged`、
  `test_comparison_orders_sides_and_reports_each_difference`、
  `test_comparison_reads_frozen_fact_values_instead_of_current_rows`、`test_facts.py`
  与 `test_processing_retry.py` 共 77 例通过。
- **I-3**：`test_comparison_counts_an_adopted_supplement_only_when_cited`、
  `test_report_api_exposes_final_facts_and_comparisons_of_adopted_inputs`、
  `test_report_merges_initial_and_appended_evidence` 与 `test_evidence_conflicts.py` 通过。
- **I-4**：`frontend/src/api/client.test.ts` 与 `processing-panel.test.tsx` 通过，构建成功；
  这些用例断言首次请求为 `status=failed` 且不带 `stage`。

期间修正了两处**测试自身缺陷**（修复提交之后才发现，属测试夹具与断言错误，非实现变更）：
`test_report_comparison.py` 的 `persist_version` 增加 `dedupe_key` 参数，避免两个不同内容的
版本共用 `release-1` 被去重合并；`persisting` 断言改读 `item["earlier"]["document_id"]`，
因为 `_document_diff` 的 `persisting` 条目是 `earlier`/`later` 快照包裹结构。
`test_report_api.py` 中 adopted 输入的断言改读 `research_inputs`，与 `report_detail` 的
实际响应结构一致。

尚未完成：五个 `fix/*` 分支仍未合并到 `main`；真实 Provider/MCP、ARM 实机与移动端验收
仍在 [延期清单](../10-20-stocks-roadmap/pending-acceptance.md)。
