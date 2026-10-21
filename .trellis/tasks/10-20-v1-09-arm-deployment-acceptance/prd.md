# V1-09 ARM Compose 部署与 V1 日常运行验收

## 目标与用户价值

在目标单机环境运行受保护控制台、API 与 Worker，完成日常运行和恢复验收。

## 背景与依据

阶段：V1。归属契约：`docs/architecture.md`, `docs/execution.md`, `docs/interface.md`, `docs/validation.md`, `docs/quickstart.md`。代码现状见[基线](../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V1-08](../archive/2026-10/10-20-v1-08-web-console/prd.md)

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- Compose 运行 API、独立 Worker、PostgreSQL/pgvector 和 Nginx 静态前端；生产不常驻 Node，S3 独立标准 API 接入。
- 跨设备入口认证后开放，数据库/管理接口不直接暴露公网；密钥不进 Git，PostgreSQL 持久化卷不放 S3。
- 目标 ARM 环境验证镜像、数据库初始化、服务连通与受保护证据下载；不以仅本机 amd64 构建代替。
- 按 validation.md V1 验证独立调度、Fact、可靠增量、续扫与恢复；记录真实覆盖和未验证限制。

## 验收条件

- [~] AC-1: 目标 ARM + Compose 可启动并跨设备认证使用；未认证不能读报告或原件，数据库未向公网开放。
- [x] AC-2: 采集/研究按各自配置运行，报告失败保留最后成功版本；有限重试和人工恢复能完成。
- [x] AC-3: 晚提交事务、晚到公告、扫描触顶和 Scope 改变场景通过，所有 V1 子任务验收有实际记录。

## 不在范围内

未经用户要求实际公网发布、生产历史数据迁移、高可用集群。TLS 证书管理与公网
DNS 不由本任务配置；生产入口由目标主机的 HTTPS/私网反向代理提供。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。
Owner 已明确授权按 V1-07、V1-08、V1-09 顺序执行，并允许真人视觉、
目标设备及第三方 Provider/S3 验收先记录为 pending。当前前置 API、Worker 与 Web
控制台已落地；本任务直接补齐和维护生产部署配置、入口边界与可执行验收记录。目标
ARM 实机若在完成可实现配置后仍不可用，保留明确的未验证状态，不以 amd64 结果冒充。实际结果见 [acceptance.md](acceptance.md)。
