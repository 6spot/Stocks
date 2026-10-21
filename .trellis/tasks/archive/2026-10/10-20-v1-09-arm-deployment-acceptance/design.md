# V1-09 技术方案

## 边界与数据流

- 保持单机和现有服务边界；部署工作不新建 Source Monitor、队列或数据库。
- `compose.yaml` 运行 PostgreSQL、API、Worker 和生产 Web。API 与 Worker 共用后端
  镜像；Web 镜像仅执行前端依赖安装与 Vite 构建，运行阶段使用 Nginx，不常驻 Node。
- Nginx 是唯一对外入口：静态文件必须保留 SPA fallback，`/api` 与 `/health`
  同源代理到 API；API、Worker 与 PostgreSQL 不发布主机端口。Nginx 只发布绑定
  地址可配置的 HTTP 端口，TLS 与私网路由由目标主机入口负责。
- 数据库继续使用命名卷。S3 仍是外部标准 S3 API，由 `.env` 提供地址、bucket 与
  成对凭据；S3 凭据不得成为 Compose 构建参数或进入镜像。
- Owner Cookie 使用 `HttpOnly`、`SameSite=Strict` 和可配置 `Secure`。生产跨设备
  访问设置 `OWNER_COOKIE_SECURE=true`；CSRF token 只经会话响应进入浏览器内存。
  健康检查公开，其余入口保持后端应用级认证和 CSRF 检查。

## 代码落点

- `compose.yaml`
- `backend/Dockerfile`
- `frontend/Dockerfile`
- `deploy/nginx.conf`
- `deploy/healthcheck`（仅在基础镜像缺少 curl/wget 时使用）
- `docs/quickstart.md`

## 输入输出与契约

`docs/architecture.md`, `docs/execution.md`, `docs/interface.md`, `docs/validation.md`, `docs/quickstart.md` 是唯一业务契约来源。输入引用前置任务的持久记录，输出满足 PRD 各 AC；不把本任务文档变成另一套字段规范。跨层 Schema、事务及失败语义在开工复核中结合已落地代码确定。

部署验收先以 `docker compose config` 校验解析，再在全新开发卷上启动完整服务栈，
执行新库初始化、健康检查、未认证拒绝、登录与 CSRF、报告/原件保护和 Worker
单轮运行。V1 日常场景复用现有测试、CLI 与 API 行为，不复制新执行引擎。

## 构建与运行形态

- 后端目标平台由 Compose `platform` 或 `TARGET_PLATFORM` 控制，ARM 构建用于目标
  部署；测试服务器没有 ARM 执行能力时只能验证构建产物，不能宣称容器已运行。
- 前端构建使用仓库根上下文与 allowlist `.dockerignore`，只允许 `frontend/` 和
  `deploy/nginx.conf`；多阶段 Dockerfile 安装锁文件依赖、构建 `dist`，并将结果
  复制到 Nginx。Nginx 配置关闭目录列表，静态资源有缓存控制，不缓存 API 响应。
- API 保持应用容器网络内的 `0.0.0.0:8000` 监听，仅供 Nginx/Worker 网络访问；
  PostgreSQL 同样仅内部网络。Compose 只暴露 Web 入口。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。外部服务凭据/目标环境缺失时保留验证限制，不标记实际接通。后续任务不得把本任务未完成能力当已实现。
部署回退到上一组镜像或 Compose 配置即可；命名卷与外部 S3 不随应用容器删除。
本任务不自动删除含证据的卷或 S3 object。真实 Provider/S3、真实手机、目标 ARM 与
公网发布不能由本地/amd64 模拟结果替代，缺失时明确 pending。
