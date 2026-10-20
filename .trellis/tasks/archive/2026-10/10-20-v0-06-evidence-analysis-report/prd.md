# V0-06 最小 Evidence Package 与 Analysis Report

## 目标与用户价值

从已保存资料形成可复查证据包，生成带有效引用的结构化研究和报告。

## 背景与依据

阶段：V0。归属契约：`docs/research.md`, `docs/data.md`, `docs/execution.md`, `docs/validation.md`。代码现状见[基线](../../../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V0-04](../10-20-v0-04-vector-search/prd.md), [V0-05](../10-20-v0-05-manual-task-agent/prd.md)

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- 实现统一 select_evidence，从当前已存 Chunk 选择资料；市场、行业、个股复用同一流程。
- 保存目标、run.scope、as_of、规则版本、实际 Document/Chunk 版本、查询与选择理由和覆盖缺口；预算有界，未知数量不填零。
- 直接提及与相关背景分开，Scope 各维度不机械全 AND；不按报告类型硬编码配额。
- ResearchContext 输入证据包，输出结构化 Analysis/findings/open_questions 与可阅读 Report；区分事实、来源观点和模型推断。
- 确定性验证引用存在、版本匹配和必要结构；非法引用有限重试，不能静默编造。成功持久化前不替换最后成功报告；失败保留证据包。

## 验收条件

- [x] AC-1: 真实已入库资料可产出一份研究报告；每条事实/数值引用可追到本 Run 实际采用的片段及原件。
  结果：真实 PostgreSQL 中的 Document/Chunk/解析批次走通统一取材到 Analysis 与 Report 的持久闭环；
  Provider 响应使用 `httpx.MockTransport`，真实外部模型调用未验收，由 V0-07 端到端补做。
- [x] AC-2: 无可用资料、资料不足或无显著变化如实说明；候选截断、来源或索引失败可见。
- [x] AC-3: 虚构引用被拒绝；模型失败不破坏已保存的包和旧报告，新版原文不会改变旧版输入。

## 不在范围内

Fact、since-last-success、外部补证、历史报告比较、强制复核/批准发布流程。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。已按授权实施并在测试服务器验证，结果见[脱敏验收记录](acceptance.md)。真实模型 Provider 与真实来源端到端阅读仍属于 V0-07 范围。
