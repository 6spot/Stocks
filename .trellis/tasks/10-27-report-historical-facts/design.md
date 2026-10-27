# MP-02 修复设计：历史语义证据携带已有 Facts

## 缺陷与根因

`select_evidence` 的两条取材路径行为不对称：

- 增量路径（`backend/src/stocks/research/service.py:305-308`）：对 change page 的每个 Document 用 `facts_for_documents()` 批量加载 Fact，经 `_unique_facts_by_signature` + `mark_conflicting_facts` 投影后传入 `collector.add_hit(..., facts=fact_refs)`。
- 语义路径（`service.py` 中 `collector.add_hit(hit, role=HISTORICAL_CONTEXT, ...)` 调用，约 :412）：不传 `facts`，`EvidenceCollector.add_hit` 只合并调用方传入的 Fact，因此仅经语义召回入选的历史 Document 漏带已保存的 Fact。`EvidencePackage.evidence_groups[].fact_refs` 为空，Agent 输入与报告的 `merged_research_fact_refs` 随之为空，指标比较缺失。

审查证据：`.trellis/tasks/archive/2026-10/10-26-application-pipeline-audit/audit.md` MP-02 节；探针 `research/audit_probes.py::test_historical_semantic_document_keeps_saved_facts`。

## 修复方案

在语义命中路径为**实际入选**的历史 Document 有界加载并合并其 Facts，与增量路径共用同一投影管线。

### service.py 语义查询循环内

语义查询返回 `result.hits` 后，本查询轮内对未在 collector 中的新入选 Document 收集 `document_id`，然后：

1. 用 `facts_for_documents(session, new_document_ids)` 一次批量读取（复用现有函数与 `MAX_FACT_QUERY_LIMIT` 有界约束，不逐 Document 查询）。
2. 经 `_unique_facts_by_signature` + `mark_conflicting_facts` 投影（与增量路径完全一致），按 `document_id` 分组。
3. 对每个通过预算检查、调用 `collector.add_hit(hit, role=HISTORICAL_CONTEXT, ...)` 的命中传入对应 `facts=fact_refs_by_document.get(hit.document_id, [])`。
4. 命中因 `token_budget` 被跳过时，其 Document 的 Facts 不附加——Facts 跟随其 Chunk 入选，不产生无文本载体的历史组（与增量路径 `add_fact_only` 仅服务"新信息"的语义一致：历史纯结构化材料另无入口，审查明确这不在本次修复范围内制造虚假向量或 Fact-only 背景组）。
5. `collector.add_facts` 已按 `fact_id` 全局去重，增量与语义双路径命中同一 Document 时不会重复附加。
6. 每个查询轮内只对本轮新增 Document 查询 Fact；跨查询轮重复命中的 Document 由 collector 去重，不重复读库。

### 边界与不变量

- 只读已保存的 Fact 行，不重新抽取、不改写值；投影含 `evidence_ref`、期间、单位、`available_at`，可回溯原件。
- 预算边界不变：token 预算只计 Chunk 文本，Fact 投影沿用增量路径的既有行为（附着于组、不计入选中 token）。
- 冲突标记由 `mark_conflicting_facts` 决定，与增量路径一致；`compare_fact_values` 在 Agent 输入组装时统一执行。
- 覆盖统计 `fact_count` 由 `collector.fact_count` 自动反映新增合并，不需要单独改动。
- 不新增配置项、不新增表、不做迁移。

## 测试设计

远程（100.120.14.84 隔离目录）运行：

1. `backend/tests/test_incremental_evidence.py` 新增回归：历史语义命中的 Document 携带其已保存 Facts（断言 `evidence_groups[].fact_refs` 的 fact_id 集合与库中一致）；同 Document 双路径命中不重复；无 Fact 文档正常入选且 `fact_refs == []`；token 预算淘汰的语义命中不产生 Fact。
2. 复用审查探针 `audit_probes.py::test_historical_semantic_document_keeps_saved_facts` 作为独立复核（应从失败转通过）。
3. 相关既有文件：`test_incremental_evidence.py`、`test_evidence_conflicts.py`、`test_research.py`、`test_execution.py`（受影响路径的定向回归）。

## 文档归属

`docs/research.md` V1-06 段补一句：历史语义入选的 Document 同样携带其已保存 Fact（有界批量加载、同投影管线）；不新设规范。
