# V1 阶段交付组织

本阶段保持既有系统边界，以子任务接入同一 Repository、Task/Run 与研究流程。目标：独立计划采集与研究，可靠增量与 Fact，受保护控制台和目标 ARM 部署及恢复。

父子关系只表示归属；依赖以子任务 prd.md / implement.md 和 task.json 的 meta.depends_on 为准，Trellis 不自动调度。按下列任务次序推进；编号是建议串行顺序，未相互依赖者可独立细化。

- [V1-01](../10-20-v1-01-worker-scheduling/prd.md)：独立 Worker 定时调度与 Run 恢复
- [V1-02](../10-20-v1-02-processing-recovery/prd.md)：统一后台处理状态与积压重试
- [V1-03](../10-20-v1-03-classification/prd.md)：Decision Model 轻量分类与受控标签
- [V1-04](../10-20-v1-04-facts-api-mapping/prd.md)：按需 Fact 与结构化 API 字段映射
- [V1-05](../10-20-v1-05-committed-change-cursors/prd.md)：提交可见变化水位与可继续扫描
- [V1-06](../10-20-v1-06-incremental-evidence/prd.md)：Document Fact 统一增量取材与覆盖
- [V1-07](../10-20-v1-07-owner-console-api/prd.md)：Owner 访问控制与控制台后端 API
- [V1-08](../10-20-v1-08-web-console/prd.md)：受保护的响应式研究控制台
- [V1-09](../10-20-v1-09-arm-deployment-acceptance/prd.md)：ARM Compose 部署与 V1 日常运行验收

阶段父任务不直接实施；各子任务写有建议落点、契约来源和风险。初始化结构直接修改当前定义，不迁移旧库；运行期原件和引用持续保留。更换已确认的阶段范围必须回到领域文档和阶段验收一起调整。
