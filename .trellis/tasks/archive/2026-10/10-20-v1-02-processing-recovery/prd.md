# V1-02 统一后台处理状态与积压重试

## 目标与用户价值

已可靠入库的资料由独立 Worker 自动整理；Owner 能查询各阶段积压并只重试未完成工作，处理失败不丢原件、不阻塞其他独立阶段，也不破坏旧报告证据。

## 背景与前置

唯一领域契约为 `docs/data.md`、`docs/execution.md`、`docs/validation.md`。V1-01 已交付独立定时 Worker 与 Run 恢复，见[前置任务验收](../archive/2026-10/10-20-v1-01-worker-scheduling/acceptance.md)。开工时已有手动解析/索引、不可变 ParseBatch/Chunk 及部分向量复用，尚无统一文档待办；分类与 Fact 算法仍由后续任务交付。开工依据见[复核记录](research/kickoff-review.md)，最终实现与验证见[验收记录](acceptance.md)。

## 范围与要求

- R1：统一 parse、classify、facts、index 的待处理、运行、完成、失败与跳过状态，保留最近尝试、错误、处理器/模型版本和重试信息。适用性不等于实现可用性：缺少处理器须明确待接入，不能伪造成功、不相关或跳过。
- R2：Worker 只处理已保存的 Document；原件与待办可靠提交后才领取。解析后分类/Fact 分支和索引分支独立推进，分类或抽取失败不阻塞索引，不逐篇触发研究。
- R3：有限自动重试后保留可查询失败；Owner 可按 Document/阶段重试或显式重处理。中断不自动接管，确认旧进程停止后才能恢复。普通重试复用成功结果和已提交的向量分批，从失败或未完成阶段继续。
- R4：完成资料不会因模型配置变化自动重处理。显式重索引只读取已有 Chunk；显式重分类不自动重开成功 Fact。新原文版本单独处理，旧原件、被引用 Chunk、报告输入和引用继续可读。
- R5：复用现有解析、索引与 Repository 入口；分类/Fact 本轮只建立处理接入、依赖与失败边界，其实际业务实现分别由 V1-03/V1-04 交付。

## 验收条件

- [x] AC-1（R1/R2/R5）：固定分类或抽取处理器失败时，原件/Chunk 仍可读且索引能完成；明确无需文本/向量的数值处理结果记为 skipped，不算索引失败。未实现处理器显示待接入，不能冒充已完成；此项验证编排边界，真实分类/Source Mapper 留给后续子任务。
- [x] AC-2（R2/R3）：新 Document 自动形成待办；重复轮询不重复成功处理，失败重试确实重执行；有限重试耗尽后停止自动领取。中断后经 Owner 确认恢复，只继续未完成阶段，并复用中断前已持久化的派生结果。
- [x] AC-3（R3/R4）：积压可按文档/阶段/状态查询并重试；切换 Embedding 模型不自动重开成功阶段，显式重索引复用既有 Chunk，重分类不重抽已完成 Fact，旧报告的引用 ID/文本/位置保持不变。
- [x] AC-4（R2/R3）：并发 Worker 不重复领取同一阶段，旧所有者不能覆盖恢复后的状态；文档积压与采集/研究 Run 均可取得执行进度，空闲等待有界。

勾选依据测试服务器 `100.120.14.84` 上本任务隔离目录的实际结果（命令与退出码见
[research/review.md](research/review.md)）。AC-1 的“明确无需向量记为 skipped”由固定处理器的适用性声明验证；
未把 `classify`/`facts` 写成已完成或跳过，也无真实 Decision Provider/Source Mapper 参与。AC-3 的
“旧报告引用保持不变”主要由 `test_owner_reprocess_index_reuses_existing_chunks` 和
`test_reparse_failure_keeps_current_chunks_and_report_references` 断言 Chunk ID/文本/批次不变验证；
报告 API 文件已包含在此前受影响范围回归中，末轮仅重跑受并发修复影响的五个文件。

## 不在范围内

Decision Provider 协议/词表及分类算法、Fact 表与抽取/Source Mapper 算法、新内容解析器、PA-2 既有 HTML 缺陷、提交可见变化水位、研究增量扫描、Web/写 API、ARM 部署验收、独立积压服务、自动回访历史文章、自动接管/租约/心跳。

## 约束与规划状态

直接修改当前模型与初始化，不做数据迁移或旧版兼容；保存运行时证据链。所有测试/静态检查/构建只在 `100.120.14.84` 的本任务隔离目录执行。PA-1 真实模型闭环继续待联合验收。

Owner 已在本次最终规划评审后回复“开始”，批准按当前 PRD/design/implement 实施。任务进入执行；验收勾选仅依据指定服务器上的实际验证结果。

最终 Check 记录见 [research/review.md](research/review.md)：已修复重建请求丢失、分批所有权、跳过传播、
CLI 退出码及 Session 缓存下的真实并发问题。最终五文件回归 106 passed，真实子进程 smoke 34/34 通过；
全新数据库结构验证通过。远程资源已清理，精确提交清单见 [commit-plan.md](commit-plan.md)；Owner 已授权“提交推送归档”。
