# V0：真实研究闭环

## 目标

少量真实资料到可检索证据，再到手动研究、可阅读报告与实际引用原件。

## 阶段边界与依据

以 `docs/architecture.md` 的分阶段交付与 `docs/validation.md` 的 V0 章节为准。领域细节继续归属 data/execution/research/interface，不在阶段任务重新定义字段。此父任务只负责交付映射及最终集成验收，实际实施选子任务。

## 前置依赖

无前置交付阶段。

## 子任务与交付范围

| 任务 | 交付 | 依赖 |
| --- | --- | --- |
| [V0-01](../archive/2026-10/10-20-v0-01-raw-document-versions/prd.md) | 原件持久化与 Document 不可变版本 | 无 |
| [V0-02](../archive/2026-10/10-20-v0-02-real-source-collection/prd.md) | 统一采集入口与首个真实来源 | V0-01 |
| [V0-03](../archive/2026-10/10-20-v0-03-parsing-chunks/prd.md) | 正文解析与可定位的持久 Chunk | V0-01 |
| [V0-04](../archive/2026-10/10-20-v0-04-vector-search/prd.md) | Embedding 与 pgvector 语义和精确检索 | V0-03 |
| [V0-05](../archive/2026-10/10-20-v0-05-manual-task-agent/prd.md) | 手动 Task Run 与可配置研究 Agent | 无 |
| [V0-06](../archive/2026-10/10-20-v0-06-evidence-analysis-report/prd.md) | 最小 Evidence Package 与 Analysis Report | V0-04, V0-05 |
| [V0-07](../10-20-v0-07-evidence-api-acceptance/prd.md) | 报告证据读取 API 与 V0 真实闭环验收 | V0-02, V0-06, V0-08 |
| [V0-08](../10-20-v0-08-document-collection-fixes/prd.md) | V0-01/V0-02 归档审查缺陷修复与回归 | V0-01, V0-02 |

V0-08 为归档审查后追加的修复任务，须先于 V0-07 最终验收完成；编号不表示执行顺序。

## 阶段退出条件

- [ ] 本阶段每个子任务的 PRD 验收已完成，且真实运行/外部限制有记录。
- [ ] 对照 `docs/validation.md` 的 V0 章节执行端到端验收；仅 mock 测试不能代替真实闭环。
- [ ] 失败路径不破坏原件、旧版本及最后成功报告，未知/缺失未冒充事实不存在。
- [ ] quickstart 只说明已可运行能力，阶段父任务不因规划文件齐备而标记业务完成。

## 排除项

定时调度、Fact、可靠增量、完整控制台和外部补证。

各阶段共同不建设证券主数据、Fan-out、全文检索、自动交易或复杂编排；开发迭代不迁移不兼容。各任务当前状态以其 task.json 为准，本次追加的 V0-08 保持 planning。
