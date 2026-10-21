# V1-04 按需 Fact 与结构化 API 字段映射

## 目标与用户价值

把文本或原始 API 响应中的可复用事实保存为带单位、口径和证据位置的 Fact。

## 背景与依据

阶段：V1。归属契约：`docs/data.md`, `docs/execution.md`, `docs/validation.md`。代码现状见[基线](../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V1-02](../10-20-v1-02-processing-recovery/prd.md), [V1-03](../10-20-v1-03-classification/prd.md)

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- 建立通用 Fact，不按金融指标建专表；引用具体 Document 版本和 Chunk/页码/JSON 字段位置。
- 适用文本仅 should_extract=true 时按需 LLM 抽取；结构化 HTTP API 保存原响应后由 Source Mapper 直接解析，不强制分类。
- 覆盖配置 API 查询参数、分页/游标、限额及明确更新；采集游标只在原件和状态可靠保存后推进。
- 保留原值、单位、观测期间、公开时间、必要口径及 origin_method；未知不猜、冲突不平均，修订引用新版本并保留旧值。
- 同版本成功处理复用；重试幂等，模型切换/新研究不自动重复抽取，抽取失败不影响原件与索引。

## 验收条件

- [x] AC-1: 一份真实或固定原始 API 响应可映射多条 Fact 并追到具体字段；文本 Fact 可定位原片段。
- [x] AC-2: 未知单位保持未知，修订值与旧报告引用并存，重复处理不增相同事实。
- [x] AC-3: should_extract=false 跳过 LLM 抽取但原文仍可检索；结构化响应不要求向量即可被 Fact 查询。

## 不在范围内

实时行情服务、指标时间序列平台、全部金融公式、自动补单位。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。本任务已实施并验证，结果与限制见 [acceptance.md](acceptance.md)。
