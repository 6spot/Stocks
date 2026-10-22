# F-02：支持历史解析批次证据的 Run 重试

## 问题与目标

旧 Run 冻结的 Document/Chunk/parse_batch_id 都有效时，只要同 Document 重新解析成新 current 批次，重试就会拒绝旧批次引用。目标是让历史输入可复用，同时严格保留版本、身份和授权校验。

来源：[审查 F-02](../10-20-stocks-roadmap/research/implementation-design-review.md)。父任务：[修复计划](../10-22-implementation-design-fixes/prd.md)。优先级 P1，无功能前置。与 F-03 共享 research/service.py、execution/service.py，合并需协调。

## 范围与约束

- 新取材继续使用当前成功批次；重试读取已冻结的历史引用。
- 保留原 Run 证据包和追加台账，不在重试时用最新批次替换旧片段或重新取材。
- 不放松伪造引用、跨 Document、错误版本或越权补证校验；不修复 Fact 删除问题，后者属 F-01。

## 验收条件

- [ ] AC-1：失败 Run 使用批次 A；同 Document 成功解析为 B 后重试，重试继续读取 A 的 Chunk，原输入不变，可完成后续研究。
- [ ] AC-2：上述条件同时适用于初始选择和已采用补证台账中的合法历史引用。
- [ ] AC-3：伪造/缺失 Chunk、错误 Document、版本或 parse_batch_id 仍失败；非本 Run 允许的外部引用仍不能进入 Analysis/Report。
- [ ] AC-4：新建 Run 的正常证据检索仍选择当前成功批次 B；不能因允许历史重试而召回过时批次。
- [ ] AC-5：旧批次真实缺失/损坏时明确失败，不静默切换 B 或伪造证据。

## 状态

2026-10-22 已获 Owner 实施授权并进入 `in_progress`。修复代码、回归场景及归属文档已写入；缺陷基线与修复结果均尚未运行验证，测试服务器 SSH 认证受阻，验收项保持未勾选。详见 [实施记录](implement.md) 与 [验收记录](acceptance.md)。依据 docs/data.md 的历史批次保留及 docs/research.md、docs/execution.md 的冻结输入/重试契约。
