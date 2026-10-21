# V1-03 Decision Model 轻量分类与受控标签

## 目标与用户价值

用一次正常分类请求得到可检索标签和 should_extract，避免逐篇深度分析。

## 背景与依据

阶段：V1。归属契约：`docs/data.md`, `docs/execution.md`, `docs/validation.md`。代码现状见[基线](../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V1-02](../10-20-v1-02-processing-recovery/prd.md)

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- 配置受控类别/标签和稳定 ID；预算内已解析片段输入低成本模型，一次正常调用给出分类及 should_extract。
- Decision 协议通过薄 Adapter 归一化，不假设任意决策服务都兼容 chat/completions；只支持实际验证的配置协议。
- 当前 classification JSONB 与状态记录在 Document 版本，不建分类历史表；重分类只更新当前结果。
- 公司/股票必须有原文依据，行业/主题只是背景关联；歧义留空，不补建证券身份系统。
- Schema/词表非法、超时或回退耗尽为可诊断失败，不能默认为 should_extract=false，原件仍保留和可检索。

## 验收条件

- [x] AC-1: 宏观无股票资料可成功分类并语义召回；行业政策不会自动标为直接提及某股票。（分类与标签保存已验证；按标签过滤的语义召回属 V1-06，见 [acceptance.md](acceptance.md)）
- [x] AC-2: 正常成功路径一次分类返回标签和抽取决策；超词表或无效结果进入有限重试/失败。
- [x] AC-3: 显式重分类更新当前 JSONB、不新增 Document，不自动重抽取已完成 Fact。

## 不在范围内

逐篇投资结论、情绪真值分数、分类历史、证券主数据。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。本任务已实施并验证，结果与限制见 [acceptance.md](acceptance.md)。
