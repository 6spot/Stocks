# F-03 技术设计

## 现状与切入点

research/supplement.py 已把补证 Fact 投影写入 research_inputs.fact_refs；research/service.py 的 merged_research_refs() 合并身份，validate_selected_versions() 主要返回可读 Chunk 与 Fact ID。execution/service.py 的 _agent_evidence() 再从初始 package.evidence_groups 补充事实，最终渲染使用 package.metric_comparisons。

## 合并视图

优先扩展现有合并投影链路，让完整 Fact 投影随着已授权引用进入 Agent。字段来自冻结的初始组或追加台账，读取实时 Chunk 只用于身份/文本校验，不用实时 Fact 行覆盖冻结数值。

按真实 Document/Chunk/Fact 身份去重，兼顾补证新 Document 及同 Document 的新增 Fact。完整事实只附加到合法来源链路，不能通过随意放入 fact_refs 扩大允许集。保留 timing、origin、role、单位/期间、来源定位等既有上下文。

## 最终比较与存储

复用已有来源独立性、同指标比较和冲突标记函数，对最终采用的初始+补证投影计算一致视图，供生成提示及确定性来源/冲突段使用。覆盖/API 展示若读取该视图，应与同次研究一致；不得覆写初始 package.metric_comparisons 伪装成初始选择。

优先从既有冻结的 Run/Analysis 台账推导，不新建平行存储。若实际持久化/接口确需增加字段，明确其归属与生成时机，修改当前模型/初始化和对应文档，不做历史迁移或旧值回填。

## 关键文件

- backend/src/stocks/research/supplement.py、service.py、evidence.py；现有 group_origins()、metric_comparisons()、mark_conflicting_facts() 位于 evidence.py，开工时复核其最新实现。
- backend/src/stocks/execution/service.py 与报告渲染入口。
- backend/src/stocks/api.py（按合并输入/覆盖读取的实际影响调整）。
- backend/tests/test_research_supplement.py、test_execution.py、test_report_api.py 及已有冲突比较测试。
- docs/research.md、docs/data.md、docs/interface.md：只更新各自归属契约。

F-02 同时调整版本验证时，合并后确认历史批次合法性与 Fact 字段均保留。F-04 应读取同一冻结引用与事实语义，避免另造一套补证投影。
