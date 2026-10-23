# V2：研究深化

## 目标

按需授权补证，更多实际来源、来源独立性、冲突、历史比较与覆盖费用分析。

## 阶段边界与依据

以 `docs/architecture.md` 的分阶段交付与 `docs/validation.md` 的 V2 章节为准。领域细节继续归属 data/execution/research/interface，不在阶段任务重新定义字段。此父任务只负责交付映射及最终集成验收，实际实施选子任务。

## 前置依赖

[V1-09](../archive/2026-10/10-20-v1-09-arm-deployment-acceptance/prd.md)

## 子任务与交付范围

| 任务 | 交付 | 依赖 |
| --- | --- | --- |
| [V2-01](../archive/2026-10/10-20-v2-01-authorized-tools-mcp/prd.md) | 授权 Tool MCP 与研究按需补证 | V1-09 |
| [V2-02](../archive/2026-10/10-20-v2-02-source-parser-expansion/prd.md) | 更多真实来源与内容解析适配 | V2-01 |
| [V2-03](../archive/2026-10/10-20-v2-03-source-conflicts/prd.md) | 来源独立性与冲突证据呈现 | V1-09 |
| [V2-04](../archive/2026-10/10-20-v2-04-report-comparison/prd.md) | 基于原始证据的历史报告比较 | V2-03 |
| [V2-05](../archive/2026-10/10-20-v2-05-coverage-cost-acceptance/prd.md) | 覆盖费用与异常分析及 V2 验收 | V2-02, V2-04 |

## 阶段退出条件

- [x] 本阶段每个子任务的 PRD 验收已完成，且真实运行/外部限制有记录。
- [ ] 对照 `docs/validation.md` 的 V2 章节执行端到端验收；仅 mock 测试不能代替真实闭环。
- [x] 失败路径不破坏原件、旧版本及最后成功报告，未知/缺失未冒充事实不存在。
- [x] quickstart 只说明已可运行能力，阶段父任务不因规划文件齐备而标记业务完成。

## 2026-10-23 阶段状态

五个子任务均已实施、归档并通过 PRD 验收，外部限制逐项写进 `docs/validation.md` 与
[延后验收清单](../10-20-stocks-roadmap/pending-acceptance.md)：真实远程 MCP（V2-01）、
真实同源转载与冲突样例（V2-03）、真实连续两份报告的人工对照（V2-04）、
真实返回费用的端点（V2-05）、调度与限流下的长期运行（V2-02/V2-05），以及 SEC CIK
映射与联系身份（V2-02）。

**第二条退出条件仍不成立**：上述六项都依赖真实第三方服务与长期运行观察，未执行。
其中真实模型闭环与 PA-1 同阻塞（需 Owner 提供 Provider 凭据），同源冲突样例与费用端点
需 Owner 选定实际来源。因此本阶段保持 `planning`。

## 排除项

自动交易、证券主数据、复杂 DAG、多 Agent 编排和平台化。

各阶段共同不建设证券主数据、Fan-out、全文检索、自动交易或复杂编排；开发迭代不迁移不兼容。所有任务当前仅 planning。
