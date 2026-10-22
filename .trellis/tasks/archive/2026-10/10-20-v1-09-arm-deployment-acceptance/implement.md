# V1-09 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务：
  [V1-08](../10-20-v1-08-web-console/prd.md)
- [x] 重新检查现有 Compose、Dockerfile、认证、健康检查、前端构建及测试环境。
- [x] Owner 已明确授权按 V1-07、V1-08、V1-09 顺序执行；外部验收允许 pending。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线，实施时按实际改动复核。

## 执行顺序

- [x] 补齐前端多阶段镜像与 Nginx 同源配置，Compose 增加 Web、健康检查和内部
  网络边界；API/Worker 保留独立进程。
- [x] 在测试服务器校验 Compose、构建镜像，以隔离新库启动完整栈；执行公开 health、
  未认证/认证/CSRF/原件保护与 SPA fallback smoke。
- [x] 运行最小后端及前端检查，覆盖 Session、报告/原件、恢复边界和 Worker。
- [~] 尝试 ARM 平台构建；目标 ARM 实机不可用时记录未验证。核对 V1 场景已有实际
  验收记录，补足截至 V1-09 的实现边界。跨平台构建因构建器无法解析基础镜像而未开始，
  实机运行仍为 pending；V1 场景记录已核对。
- [x] 更新 quickstart、部署契约必要内容、任务 AC 与实际限制。
- [ ] 完成最终质量检查、提交和任务归档（由主会话在检查通过后执行）。

## 最小必要验证

- 在测试服务器本任务隔离目录执行 `docker compose config --quiet`，构建 API、
  Worker 所共享后端镜像与 Web/Nginx 镜像。
- 以隔离数据库启动栈，执行 `alembic upgrade head`、`alembic check`、公开健康检查、
  未认证拒绝、登录、CSRF、报告/原件保护、SPA fallback 和 Worker 单轮 smoke。
- 前端执行 `npm ci --ignore-scripts`、`typecheck`、test、build 并审计生产依赖；
  后端只运行受部署影响的 API/Session/Worker 测试文件。
- 尝试目标 ARM 平台镜像构建；无 ARM 执行环境时不能将构建成功称为实机通过。
- 以最小组合覆盖 V1 集成场景，仅跨模块明确回归风险时扩大测试并说明原因。

新增测试名为计划，文件存在后才执行；不得把本文件命令写成已通过。按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 实际验证

- Compose 的 amd64 生产形态在测试服务器全新开发卷上启动，Web/API/PostgreSQL 健康，
  Worker 单轮空队列正常退出。
- Nginx smoke 通过公开 health、根页面、深路径 SPA fallback、匿名报告/原件拒绝、
  Owner 登录 Cookie、CSRF、认证读取、稳定 503、登出、安全头与端口边界。
- 后端定向回归为 `70 passed, 1 warning`；新增触发/续扫模型配置缺失用例与已有活跃
  Run 重复触发用例均通过真实 PostgreSQL。
- 最终审阅补齐了模型配置缺失/非 UTF-8 读取边界和 Nginx API 安全头；新增定向用例
  `5 passed, 13 deselected, 1 warning`，重建 Web 后认证 API 与 503 响应头复检通过。
- ARM Compose 配置解析通过；目标平台镜像构建因测试机无法从 Docker Registry 获取
  基础镜像元数据而未开始，ARM 实机和真实浏览器/Provider/S3 保留 pending。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制。
- [x] 契约变更只更新唯一归属文档，运行能力更新 quickstart。
- [x] 检查后续依赖与阶段验收；保留原始证据、输入快照及最后成功结果。
- [x] 已完成实现/质量检查；真实目标环境验收保留 pending，提交与归档由收尾流程记录。
