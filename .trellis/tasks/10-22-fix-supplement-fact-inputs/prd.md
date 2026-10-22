# F-03：将补证 Fact 纳入研究与冲突视图

## 问题与目标

补证执行后 Fact 及其完整投影已进入追加台账，但 Agent 的结构化事实只从初始 evidence_groups 挂载，报告确定性指标比较也只读取初始 metric_comparisons。结果是追加 Fact 虽已保存，却可能不被模型和最终冲突视图完整使用。

来源：[审查 F-03](../10-20-stocks-roadmap/research/implementation-design-review.md)。父任务：[修复计划](../10-22-implementation-design-fixes/prd.md)。P1，无硬依赖；与 F-02 共享后端文件。建议先于 F-04 合入以便统一补证引用口径。

## 范围与约束

- 初始证据和本 Run 追加台账产生统一研究视图，保留 Fact 的 ID、值、单位、期间、可获知时间及 Document/证据位置。
- 同指标冲突必须在最终来源/冲突展示中保留双方原值和出处，复用既有可比性判断，不平均、不裁决。
- 初始 EvidencePackage 不可改写；已采用补证应冻结在 Run/Analysis 台账中，重试不得从当前数据库重建旧 Fact 投影。
- 不扩大 Tool 权限、轮次、调用或 token 预算；不引入独立研究引擎或新的补证编排。

## 验收条件

- [ ] AC-1：补证对新 Document 成功执行 parse/facts 后，Agent 实际模型请求包含该台账中完整的 Fact 字段及真实 Chunk/Document 引用，不只是 fact_ids。
- [ ] AC-2：初始 A 与补证 B 的同主体/指标/单位/期间数值不同时，最终报告确定性比较/冲突展示包含 A、B 和两者来源；不同口径/期间按既有规则区分，不误报可直接比较。
- [ ] AC-3：同 Document/Fact 被初始与补证路径重复命中不双算独立证据；不得因同 Document 已有初始分组而丢失新增 Fact。
- [ ] AC-4：最终 Analysis 保存完整补证输入；合法重试、报告证据读取和输入投影一致，原始 EvidencePackage 及既有报告不被更新。
- [ ] AC-5：parse-only、facts 失败、无 Fact、预算截断及越权/伪造引用继续保持真实缺口和原权限边界，不伪造值或擅自现场再抽取。

## 状态

仅规划，静态确认尚未运行复现。真实 MCP 接通继续保留既有延期验收，不作为本任务通过前提。
