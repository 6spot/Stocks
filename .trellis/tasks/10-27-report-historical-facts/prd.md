# 修复 MP-02：历史语义证据携带已有 Facts

## 目标与事实

修复既有审查记录的 MP-02：历史文档未在本页变化扫描中、仅通过语义召回入选时，可能漏带已经保存的 Facts，导致个股研究取得文章却缺少对应结构化数据。依据：[既有审查](../archive/2026-10/10-26-application-pipeline-audit/audit.md)。

## 需求

选中的历史证据须携带适用且可追溯的已有 Facts；遵守研究 as_of、文档版本、观测期、单位、冲突与现有证据预算。不得借此把未入选的全库数据无界加入，也不在研究阶段偷偷重新抽取或篡改原始事实。

## 验收标准

- [x] 远程回归案例包含不在本页变化范围、仅通过语义检索入选的历史文档和它已保存的 Facts。`tests/test_incremental_evidence.py::test_semantic_only_document_carries_saved_facts` 与审查探针 `test_historical_semantic_document_keeps_saved_facts`（原失败，现通过）在测试服务器隔离目录通过。
- [x] Evidence Package 和实际研究输入包含适用 Fact 引用及来源身份，重复命中不重复附加。Fact 投影含 `evidence_ref`/期间/单位/`available_at`；双路径合并既有用例 `test_document_and_fact_merge_across_incremental_and_semantic_paths` 通过，collector 按 `fact_id` 去重。
- [x] 没有 Facts 的文档正常入选；时间、版本、冲突信息和预算边界经相关用例核对。`test_semantic_document_without_facts_stays_selectable`、`test_semantic_document_keeps_conflicting_fact_flags`（同一指标两个值仍带 `conflict` 标记）通过；token 预算淘汰的命中不产生组，自然不携带 Fact。
- [x] 报告中的相关数值可回溯至原文及对应数据，最小研究/增量证据检查通过，研究归属文档同步。定向回归 `test_incremental_evidence.py`(10)、`test_evidence_conflicts.py`+`test_research.py`+`test_research_supplement.py`(41) 全部通过；`docs/research.md` V1-06 已同步。

## 前置与非目标

可与 MP-01 独立推进，真实质量验收依赖本任务。不改实体推断策略，不扩大 SEC 全量 JSON 范围，不建设新事实数据库。

## 执行边界

本轮仅创建需求草案，状态保持 planning；未授权开始实现。启动前完成本任务必要的设计、执行计划与上下文，并复核需求。所有应用测试、静态检查和真实调用仅在测试服务器 `100.120.14.84` 的任务隔离目录执行；本机仅编辑和任务管理。不做数据迁移或旧版兼容，不引入额外服务、多 Agent 平台或证券主数据库。凭据不入库、不入任务记录或日志。

父任务：[真实来源驱动的市场与个股报告闭环](../10-27-real-source-report-loop/prd.md)。
