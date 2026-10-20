# V0 阶段交付组织

本阶段保持既有系统边界，以子任务接入同一 Repository、Task/Run 与研究流程。目标：少量真实资料到可检索证据，再到手动研究、可阅读报告与实际引用原件。

父子关系只表示归属；依赖以子任务 prd.md / implement.md 和 task.json 的 meta.depends_on 为准，Trellis 不自动调度。按下列任务次序推进；编号是建议串行顺序，未相互依赖者可独立细化。

- [V0-01](../archive/2026-10/10-20-v0-01-raw-document-versions/prd.md)：原件持久化与 Document 不可变版本
- [V0-02](../archive/2026-10/10-20-v0-02-real-source-collection/prd.md)：统一采集入口与首个真实来源
- [V0-03](../archive/2026-10/10-20-v0-03-parsing-chunks/prd.md)：正文解析与可定位的持久 Chunk
- [V0-04](../archive/2026-10/10-20-v0-04-vector-search/prd.md)：Embedding 与 pgvector 语义和精确检索
- [V0-05](../archive/2026-10/10-20-v0-05-manual-task-agent/prd.md)：手动 Task Run 与可配置研究 Agent
- [V0-06](../archive/2026-10/10-20-v0-06-evidence-analysis-report/prd.md)：最小 Evidence Package 与 Analysis Report
- [V0-07](../10-20-v0-07-evidence-api-acceptance/prd.md)：报告证据读取 API 与 V0 真实闭环验收
- [V0-08](../10-20-v0-08-document-collection-fixes/prd.md)：原件版本与采集边界缺陷修复；依赖已归档 V0-01/V0-02，须在 V0-07 最终验收前完成。

阶段父任务不直接实施；各子任务写有建议落点、契约来源和风险。初始化结构直接修改当前定义，不迁移旧库；运行期原件和引用持续保留。更换已确认的阶段范围必须回到领域文档和阶段验收一起调整。
