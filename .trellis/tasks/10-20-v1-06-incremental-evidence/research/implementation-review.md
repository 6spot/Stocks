# Research: V1-06 Document/Fact 统一增量取材

- Query: 在 V1-05 已落地的变化扫描、游标与检查点之上，如何以最小改动让 `select_evidence` 同时读取新增变化与历史背景，合并 Fact 与来源 Document，并如实记录覆盖统计。
- Scope: internal
- Date: 2026-10-21

## 现状事实

- `stocks.research.select_evidence()` 目前把 `search_changes()` 的结果只用于推进原始分页检查点：`change_page` 记录 `raw_changes_scanned` / `candidate_documents_scanned` / `scanned_document_ids`，但**没有任何增量 Document 或 Fact 进入候选或 Evidence Package**。语义查询独立产出 `selected`（全部是 Chunk 引用）。
- `stocks.changes.changed_candidates()` 已能把一页原始变化按 `Document` 聚合并带回 `fact_ids`；其文档字符串明确说 candidate grouping 是页内概念，其 watermark 只用于排序，绝不用于续扫，因此可安全用于**选取**，不能用于分页身份。
- `EvidencePackage.selected_chunks` 是 `list[dict]`；`Fact` 模型有 `document_id`、`evidence_ref`（`kind=chunk` 时带 `chunk_id`/`parse_batch_id`/`position`，或 `kind=json` 时带字段路径）、`statement`/`value`/`unit`/`period_*`/`attributes`。
- `validate_selected_versions()` 目前只接受 `chunk_id`/`document_id`/`parse_batch_id`/`document_version` 四件套；Research Agent 由它的返回值构建 `evidence`。
- Research Agent 的 output schema（仓库 `config/tasks.yaml` 与多数测试）要求每条 finding 的 `evidence_refs[]` 至少一项且必须含 `chunk_id`+`document_id`。引用校验在 `parse_agent_output()` 内按 `(chunk_id, document_id)` 键匹配。
- `TaskRun`/`ResearchCursor`/`ChangeRecord` 已由 V1-05 建立，本任务不新增表，只扩展 `EvidencePackage` 的 JSONB 形状与 `select_evidence` 的执行。

## 关键设计决定

1. **保留 Chunk 作为 Agent 的引用单元**
   报告与 finding 的引用仍以「持久 Chunk + Document 版本」为最小可读证据；Fact 是这组 Chunk 上的结构化整理，不单独进入 `evidence_refs`，从而不改变 Agent 输出契约与既有报告读取路径。Fact 与来源 Chunk 的关联记录在 Evidence Package 的证据组里，保证 Document + Fact 只算一条证据链。

2. **统一候选分组，组内保留所有原始引用**
   每个入选证据组记录：代表 Chunk `ref`、`role`（`new_information` / `historical_context`）、`document_id`、`chunk_ids`、`fact_refs`、`source_id`、`kinds`、`reason`、`dimension`（Scope 可声明；未声明留空）。`fact_refs` 直接带 Fact id 与结构化字段，供报告与人工复查；`chunk_ids` 保留该组所有可读片段。

3. **增量与历史以两组输入合并**（AC-2）
   - 增量候选：本页 `changed_candidates()` 的 Document 集合，取其当前成功解析批次的 Chunk 组成 `new_information` 组；若这些 Document 有 Fact 变化，则把对应 Fact 关联进同一组。
   - 历史候选：现有语义 `semantic_search()` 的 Chunk 命中，按 `historical_context` 组。
   - 合并规则：同一 `(document_id, chunk_id)` 只出现一次；同一 Document 的增量组吸收同 Document 上的历史命中（不重复计数）；同一 Fact 被增量与历史同时命中时只保留一份。仅标题或向量相似不合并。跨 Document 转载不做模糊识别（见未实现）。

4. **统计口径**
   - `candidates_discovered`：增量 Document 候选数 + 语义查询去重命中数；任一查询触顶或增量页未完成时为 `null`。
   - `candidates_screened`：本轮实际检查过的增量 Document 与完成评估的语义 Chunk 数。
   - `selected_count`：Evidence Package 实际采用的 Chunk 数（保持 V0 字段语义，Agent 每条 finding 仍需能引用它）。
   - `evidence_group_count`：合并后的证据链组数；同 Document 的 Fact+原文只算一组。
   - `unexamined_count`：仅当候选池已知时给出，否则 `null`。
   - `retrieval_truncated`：任一语义查询或原始增量页触顶/未完时为 true。
   - `filtered_out_count`：`candidates_screened - selected_count`（不小于 0），与 `unexamined_count` 分开，避免把「筛选后没选」写成「没扫到」。

5. **预算**
   现有 `max_tokens` 是对选中 Chunk 正文的预算。新增 Fact 不额外消耗正文预算（结构化字段很小，且其原文已由 Chunk 承载）；Fact 数量沿用当前 `limit_per_query` 与页上限，不引入新的扫描预算或复杂打分。冲突值、不同口径并列：合并键为 `(subject_name, symbol, metric_id, unit)`，同键但 `value` 或 `period_*` 不同则保留为独立 `fact_refs` 条目并标记 `conflict: true`。

6. **分类/索引失败不阻断语义候选**
   增量候选只要求存在可读当前 Chunk；无 Chunk 的 Document（如纯 JSON 响应）只保留 Fact 组、`chunk_ids=[]`。分类失败不进入任何筛选条件。

## 不在本任务范围

- 跨 Document 的转载识别与内容指纹合并；高级跨源冲突解释；历史报告比较 UI；外部补证。
- 控制台展示属于 V1-07/V1-08；本任务只保证 API/CLI 可读到这些 JSONB 字段。

## 风险

- 语义背景召回无法给出全库精确候选总数，`candidates_discovered` 必须允许 `null`，不能为“真实覆盖”偷偷填数。
- `selected_count` 与 `evidence_group_count` 必须同时记录，否则合并后会被误读为独立支持数。
