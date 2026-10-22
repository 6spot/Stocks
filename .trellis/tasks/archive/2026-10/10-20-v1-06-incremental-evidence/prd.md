# V1-06 Document Fact 统一增量取材与覆盖

## 目标与用户价值

研究统一读取新增变化和历史背景，合并同一证据链并如实报告扫描覆盖。

## 背景与依据

阶段：V1。归属契约：`docs/research.md`, `docs/data.md`, `docs/execution.md`, `docs/validation.md`。代码现状见[基线](../../../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V1-05](../10-20-v1-05-committed-change-cursors/prd.md)

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- select_evidence 组合 search_changes 和历史 search，保存 new_information / historical_context、窗口和实际输入快照。
- Fact 与来源 Document/Chunk 合并关联，不当作两份独立支持；原文其他不重叠事实仍可保留。
- 同 Fact 多路径去重，已确认同源内容分组并保留所有引用；仅语义/标题相似不得强行合并，冲突值和不同口径并列。
- 统计实际 discovered、screened、selected、证据组、truncated 与未知 unexamined；筛选未入选和未扫描分别呈现。
- 配置预算/规则版本，保留一手资料及反向观点；分类失败/缺标签不构成唯一召回障碍。研究只读存量，游标遵循 v1-05。

## 验收条件

- [x] AC-1: Document 与其 Fact 同时命中只算同一证据链，其他事实仍进入候选；分类错误资料可通过语义召回。
- [x] AC-2: 增量和历史重复不双算，直接资料和宏观背景可进入同一研究且角色清楚。
- [x] AC-3: 触顶时报告真实覆盖缺口并保留续扫，未知剩余不填零；完成筛选未入选不阻止游标正常推进。

## 不在范围内

外部研究补证、高级跨源冲突解释、历史报告比较 UI。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。本任务已实施并在测试服务器全新数据库上验证，结果与限制见 [acceptance.md](acceptance.md)。
