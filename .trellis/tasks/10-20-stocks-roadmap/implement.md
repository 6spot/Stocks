# 路线图执行顺序与验收映射

## 使用方式

路线图初始规划为 25 项记录，追加 V0-08 后共 26 项；各记录状态以 task.json 为准。本次仅创建修复规划，未激活任务。后续选择实际交付叶子复核规划、满足依赖并确认实施，再运行 task.py start。不要 start 路线图或阶段父任务来一次实现全部功能。V0-08 必须先于 V0-07 最终闭环验收完成。

## 完整任务与依赖

| 任务 | 交付 | 前置依赖 |
| --- | --- | --- |
| [V0-01](../archive/2026-10/10-20-v0-01-raw-document-versions/prd.md) | 原件持久化与 Document 不可变版本 | 无 |
| [V0-02](../archive/2026-10/10-20-v0-02-real-source-collection/prd.md) | 统一采集入口与首个真实来源 | V0-01 |
| [V0-03](../archive/2026-10/10-20-v0-03-parsing-chunks/prd.md) | 正文解析与可定位的持久 Chunk | V0-01 |
| [V0-04](../archive/2026-10/10-20-v0-04-vector-search/prd.md) | Embedding 与 pgvector 语义和精确检索 | V0-03 |
| [V0-05](../archive/2026-10/10-20-v0-05-manual-task-agent/prd.md) | 手动 Task Run 与可配置研究 Agent | 无 |
| [V0-06](../archive/2026-10/10-20-v0-06-evidence-analysis-report/prd.md) | 最小 Evidence Package 与 Analysis Report | V0-04, V0-05 |
| [V0-07](../10-20-v0-07-evidence-api-acceptance/prd.md) | 报告证据读取 API 与 V0 真实闭环验收 | V0-02, V0-06, V0-08 |
| [V0-08](../10-20-v0-08-document-collection-fixes/prd.md) | 原件版本与采集边界缺陷修复 | V0-01, V0-02 |
| [V1-01](../10-20-v1-01-worker-scheduling/prd.md) | 独立 Worker 定时调度与 Run 恢复 | V0-07 |
| [V1-02](../10-20-v1-02-processing-recovery/prd.md) | 统一后台处理状态与积压重试 | V1-01 |
| [V1-03](../10-20-v1-03-classification/prd.md) | Decision Model 轻量分类与受控标签 | V1-02 |
| [V1-04](../10-20-v1-04-facts-api-mapping/prd.md) | 按需 Fact 与结构化 API 字段映射 | V1-02, V1-03 |
| [V1-05](../10-20-v1-05-committed-change-cursors/prd.md) | 提交可见变化水位与可继续扫描 | V1-04 |
| [V1-06](../10-20-v1-06-incremental-evidence/prd.md) | Document Fact 统一增量取材与覆盖 | V1-05 |
| [V1-07](../10-20-v1-07-owner-console-api/prd.md) | Owner 访问控制与控制台后端 API | V1-06 |
| [V1-08](../10-20-v1-08-web-console/prd.md) | 受保护的响应式研究控制台 | V1-07 |
| [V1-09](../10-20-v1-09-arm-deployment-acceptance/prd.md) | ARM Compose 部署与 V1 日常运行验收 | V1-08 |
| [V2-01](../10-20-v2-01-authorized-tools-mcp/prd.md) | 授权 Tool MCP 与研究按需补证 | V1-09 |
| [V2-02](../10-20-v2-02-source-parser-expansion/prd.md) | 更多真实来源与内容解析适配 | V2-01 |
| [V2-03](../10-20-v2-03-source-conflicts/prd.md) | 来源独立性与冲突证据呈现 | V1-09 |
| [V2-04](../10-20-v2-04-report-comparison/prd.md) | 基于原始证据的历史报告比较 | V2-03 |
| [V2-05](../10-20-v2-05-coverage-cost-acceptance/prd.md) | 覆盖费用与异常分析及 V2 验收 | V2-02, V2-04 |

上述编号为建议顺序，不承诺并行调度或工期。依赖的阶段验收完成后才跨阶段开工。

## 设计验收覆盖映射

| 来源条件（docs/validation.md） | 主要责任任务 | 集成验收 |
| --- | --- | --- |
| V0 真实原件 | V0-01、V0-02、V0-08 | V0-07 |
| V0 身份与版本 | V0-01、V0-03、V0-08 | V0-07 |
| V0 可检索证据 | V0-03、V0-04 | V0-07 |
| V0 手动研究 | V0-05、V0-06 | V0-07 |
| V0 证据阅读、运行边界 | V0-06、V0-07 | V0-07 |
| V1 独立调度 | V1-01 | V1-09 |
| V1 统一处理 | V1-02、V1-03、V1-04 | V1-09 |
| V1 Fact 与数字 | V1-04 | V1-09 |
| V1 统一取材 | V1-06 | V1-09 |
| V1 可靠增量 | V1-05、V1-06 | V1-09 |
| V1 运行恢复 | V1-01、V1-02、V1-07、V1-08 | V1-09 |
| V1 日常界面与部署 | V1-07、V1-08、V1-09 | V1-09 |
| V2 授权补证 | V2-01、V2-02 | V2-05 |
| V2 来源独立性与冲突 | V2-03 | V2-05 |
| V2 历史和覆盖 | V2-04、V2-05 | V2-05 |
| 所有阶段共同遵守 | 全部任务的约束和排除项 | 每阶段最后一项 |

## 初始规划检查（历史记录）

- [x] 读取全部任务 JSON，验证父子互链、阶段标签和依赖无环。
- [x] 用 task.py validate 验证 implement/check 上下文文件存在且非占位。
- [x] 核对 Markdown 链接、PRD 占位及 docs/validation.md 全部阶段条目的归属。
- [x] 确认所有任务 planning、没有 active task，未修改产品代码或既有设计文件。

本轮仅做上述规划检查，不运行后端/前端测试或外部业务验证。各交付叶子的 implement.md 命令是未来验证计划。

## 上下文读取注意

`docs/data.md` 当前为 34,267 字节，超过默认单文件注入上限 32,768 字节。自动注入可能截断末尾；派发/开工时须完整读取原文件，尤其末尾检索与来源安全契约。不要把已注入前缀当成全文；无需为此修改领域文档或 Trellis 全局配置。
