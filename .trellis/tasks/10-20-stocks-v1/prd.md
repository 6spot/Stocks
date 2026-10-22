# V1：日常运行

## 目标

独立计划采集与研究，可靠增量与 Fact，受保护控制台和目标 ARM 部署及恢复。

## 阶段边界与依据

以 `docs/architecture.md` 的分阶段交付与 `docs/validation.md` 的 V1 章节为准。领域细节继续归属 data/execution/research/interface，不在阶段任务重新定义字段。此父任务只负责交付映射及最终集成验收，实际实施选子任务。

## 前置依赖

[V0-07](../archive/2026-10/10-20-v0-07-evidence-api-acceptance/prd.md)

## 子任务与交付范围

| 任务 | 交付 | 依赖 |
| --- | --- | --- |
| [V1-01](../archive/2026-10/10-20-v1-01-worker-scheduling/prd.md) | 独立 Worker 定时调度与 Run 恢复 | V0-07 |
| [V1-02](../archive/2026-10/10-20-v1-02-processing-recovery/prd.md) | 统一后台处理状态与积压重试 | V1-01 |
| [V1-03](../archive/2026-10/10-20-v1-03-classification/prd.md) | Decision Model 轻量分类与受控标签 | V1-02 |
| [V1-04](../archive/2026-10/10-20-v1-04-facts-api-mapping/prd.md) | 按需 Fact 与结构化 API 字段映射 | V1-02, V1-03 |
| [V1-05](../archive/2026-10/10-20-v1-05-committed-change-cursors/prd.md) | 提交可见变化水位与可继续扫描 | V1-04 |
| [V1-06](../archive/2026-10/10-20-v1-06-incremental-evidence/prd.md) | Document Fact 统一增量取材与覆盖 | V1-05 |
| [V1-07](../archive/2026-10/10-20-v1-07-owner-console-api/prd.md) | Owner 访问控制与控制台后端 API | V1-06 |
| [V1-08](../archive/2026-10/10-20-v1-08-web-console/prd.md) | 受保护的响应式研究控制台 | V1-07 |
| [V1-09](../archive/2026-10/10-20-v1-09-arm-deployment-acceptance/prd.md) | ARM Compose 部署与 V1 日常运行验收 | V1-08 |

## 阶段退出条件

- [ ] 本阶段每个子任务的 PRD 验收已完成，且真实运行/外部限制有记录。
- [ ] 对照 `docs/validation.md` 的 V1 章节执行端到端验收；仅 mock 测试不能代替真实闭环。
- [ ] 失败路径不破坏原件、旧版本及最后成功报告，未知/缺失未冒充事实不存在。
- [ ] quickstart 只说明已可运行能力，阶段父任务不因规划文件齐备而标记业务完成。

## 排除项

外部研究补证、高级冲突解释、历史比较和完整费用分析。

各阶段共同不建设证券主数据、Fan-out、全文检索、自动交易或复杂编排；开发迭代不迁移不兼容。所有任务当前仅 planning。
