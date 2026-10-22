# F-04 技术设计

## 当前路径

backend/src/stocks/api.py 的 _report_side() 用 _report_research_refs() 得到整个初始+补证合并输入，再调用 _report_document_refs() 和 _report_fact_snapshots()。后者另遍历全部初始 evidence_groups，并按 ID 从实时 Fact 表读取，未限制到报告实际引用。

## 构建比较侧

1. 从保存的 Analysis 规范化引用获取实际引用集合，并核对 Findings 的保存语义，复用现有规范化函数形成一致去重集合；不要假定仅看任意单一顶层数组即可代表全部引用。
2. 与该报告冻结的 EvidencePackage/Analysis.research_inputs 合并输入相交，按保存的 Document/Chunk/版本/Fact 身份解析。不能从其它 Run 或当前检索补齐未授权材料。
3. 只投影这些实际引用的 Document 以及保存引用明确关联的 Fact ID，Fact-only 场景仅在现有合法引用结构能够表达时纳入，不另造隐式引用。
4. Fact 的值、单位、期间及来源来自冻结的初始 fact_refs 或追加台账；保留稳定 ID 供详情定位。F-01 保证旧事实可读取，但这不能代替本任务的快照口径。
5. 复用 research/comparison.py 做差异分类，不改变原来的排序、告警和只比较成功报告的边界。

缺少冻结信息时返回现有缺口/未知语义，不用实时全量 Fact 静默替代。无历史兼容数据要求，不新增旧报告回填流程。

## 实施落点（2026-10-22）

`_report_side()` 以 `_cited_research_refs()` 合并 Analysis 顶层与 Finding 引用并去重，再与本 Run 冻结输入相交；不在输入内的保存引用返回 409。Document 投影只包含实际引用，Fact 由 `_cited_fact_snapshots()` 从 `merged_research_fact_refs()` 读取被引用 Chunk 携带的冻结投影，缺失时标 `unavailable` 而不回查当前行。旧的全组遍历 + 实时 Fact 查询函数已删除。

测试已编写但未运行：`test_report_comparison.py` 覆盖未引用入选材料、补证引用、冻结值稳定与既有 F-01 重处理场景。远程认证受阻，任务未验收，详见 [验收记录](acceptance.md)。

## 关键文件与依赖

- backend/src/stocks/api.py：比较侧、保存台账、引用/Fact 投影辅助函数。
- backend/src/stocks/research/comparison.py、service.py：现有比较与规范化引用逻辑，优先复用。
- backend/tests/test_report_comparison.py、test_report_api.py。
- docs/interface.md 维护比较语义，docs/research.md 只在冻结引用契约变化时更新。

F-01 是硬依赖；建议基于 F-03 最终投影实现，避免对追加 Fact 形成第二套读取逻辑。预期不需要结构变更或新服务。
