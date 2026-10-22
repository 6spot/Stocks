# V2-05 覆盖费用与异常分析及 V2 验收

## 目标与用户价值

Owner 能理解研究覆盖、模型使用和异常成本，完成研究深化的阶段验收。

## 背景与依据

阶段：V2。归属契约：`docs/interface.md`, `docs/research.md`, `docs/execution.md`, `docs/validation.md`。代码现状见[基线](../../../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V2-02](../10-20-v2-02-source-parser-expansion/prd.md), [V2-04](../10-20-v2-04-report-comparison/prd.md)

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- 扩展既有控制台展示候选发现/筛选/入选/独立来源组、来源可用性、未扫描/索引缺口与补证信息。
- 按 Run/步骤展示实际 Provider、模型、Token、耗时、可得费用及回退原因；未知费用和候选剩余保持未知。
- 异常和积压操作复用已有重试/续扫/结束 Run 服务，不新增调度或告警平台。
- 依据 validation.md 完成授权补证、冲突、历史比较与覆盖费用的真实综合验收。

## 验收条件

- [x] AC-1: 报告页显示 `coverage_gaps`（来源/解析/索引缺口词表）、研究覆盖与本次 Run 的模型用量，并可跳转到该 Run 的步骤与模型调用；Run 详情按步骤与每次调用显示实际 Provider、模型、Token、耗时、回退原因与费用，Overview 给出最近一份成功报告证据包的覆盖与近期 Run 用量。未知候选剩余（`unexamined_count`/`candidates_discovered`）与未提供费用都返回 `null` 并显示「未知」，不显示 0 或「已覆盖」；失败 Run 不充当最近成功覆盖（测试断言）。
- [x] AC-2: 费用与数值只来自已保存记录：`usage` 汇总由 `ModelCall` 行确定性计算（调用数、Token、耗时、费用、未提供费用次数与有界的按模型分解），`cost_usd` 只累加供应商在响应 `usage.cost` / `usage.cost_usd` 中返回的金额，没有返回时保持未知并标注次数，不用本地价格表推算；图表/文本不经过 LLM，报告正文只由已存 Analysis 确定性生成。
- [x] AC-3: V2 相关自动化验收在同一隔离环境重跑通过（授权补证 `test_research`/`test_api`、同源冲突 `test_evidence_conflicts`、历史比较 `test_report_comparison`、执行与有界终止 `test_execution`、覆盖费用 `test_coverage_usage_api`）。真实第三方场景（真实 MCP 服务、真实返回费用的端点、真实连续两次研究的 Owner 人工复核）按 Owner 授权延后到最终验收，未完成/受限项已记录在 `docs/validation.md` 的 V2-01～V2-05 段落。

## 不在范围内

自动投资评分、动态成本路由、独立监控平台、工期或来源覆盖无限承诺。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。本任务已在 Owner「按任务列表逐个实施」的授权下完成：`model_calls` 增加 `provider` 与 `cost_usd` 时只改 0001 初始化定义（无增量迁移），契约写入 `docs/execution.md`、`docs/interface.md`、`docs/validation.md`，运行说明写入 `docs/quickstart.md`；验证只在测试服务器 `100.120.14.84` 隔离目录执行。真实第三方与人工验收项留给最终验收，不阻塞交付。
