# 单机 Compose 部署规范

## Scope / Trigger

- Trigger：修改 `compose.yaml`、后端/前端 Dockerfile、Nginx 配置、发布端口或生产
  健康检查时适用。产品边界仍由 `docs/architecture.md`、`docs/interface.md` 与
  `docs/quickstart.md` 维护。

## Signatures

- 服务：`postgres`、`api`、`worker`、`web`。`api` 与 `worker` 使用同一后端镜像；
  `web` 是多阶段前端构建，运行阶段只保留 Nginx 与静态产物。
- 唯一主机发布端口：
  `${WEB_BIND_ADDRESS:-0.0.0.0}:${WEB_PORT:-8080}:8080`。
- 内部健康检查：API `GET http://127.0.0.1:8000/health`；Web
  `GET http://127.0.0.1:8080/health`；PostgreSQL 使用 `pg_isready`。

## Contracts

- Nginx 必须提供 `/` 与 SPA fallback，并把 `/api/`、`/health` 代理到 `api:8000`；
  浏览器保持单源 Cookie 与 CSRF 语义，API 和数据库不得绕开认证直接暴露。
- PostgreSQL、API、Worker 不配置 `ports`。如需测试 SSH 访问，用 SSH 隧道或
  仅绑定 `127.0.0.1` 的临时 override，不改生产 `compose.yaml`。
- `TARGET_PLATFORM` 默认 `linux/amd64`；目标 ARM 部署显式设为 `linux/arm64`。
  平台构建成功不等于目标机器运行通过。
- `OWNER_COOKIE_SECURE=false` 只用于回环 HTTP 验证；跨设备 HTTPS 入口必须为
  `true`。生产 TLS 与反向代理由主机提供。
- 根构建上下文使用 allowlist，只允许 `frontend/` 与 `deploy/nginx.conf`；
  凭据、`.env`、`.git`、依赖目录、构建产物与 macOS `._*` 文件不得进入镜像。

## Validation & Error Matrix

| 条件 | 预期处理 |
| --- | --- |
| 缺少数据库密码或配置 | Compose 解析失败，不启动只完成一半的服务栈 |
| 前端资源缺失 | `/assets/` 返回 `404`，不得用 `index.html` 冒充脚本 |
| 已认证的 SPA 深链接 | 返回应用入口，刷新不丢当前阅读上下文 |
| 匿名访问 API 或原件 | `401` 稳定错误，不泄露存储或 Provider 细节 |
| 变更请求缺少 CSRF | `403`，状态不变 |
| 模型配置不可用 | 触发或续扫返回 `503 MODEL_CONFIGURATION_UNAVAILABLE`，固定消息，不创建 Run |
| Web 或 API 未就绪 | Compose `--wait` 返回失败；不能把单个容器 running 当作可用 |
| 目标 ARM 实机不可用 | 记录构建或运行哪一步未执行，保留 pending |

## Good / Base / Bad Cases

- Good：全新卷执行 `alembic upgrade head` 与 `check` 后启动完整栈，以 Web 端口完成
  health、登录、CSRF、报告/原件和 SPA smoke，并从 `ss`/Compose 输出确认只有 Web 发布。
- Base：无真实 Provider/S3 时仍验证受保护的读取与稳定失败边界，把真实外部接入标为 pending。
- Bad：发布 API `8000` 或 PostgreSQL `5432`；把 API 端口当作浏览器入口；仅在 amd64
  构建成功就声称 ARM 部署通过；在 Nginx 静态 `location` 中重新声明安全头导致 server
  层头被覆盖；为构建方便把整个仓库送入 Docker 上下文。

## Tests Required

- `docker compose config --quiet`，并断言 API/PostgreSQL/Worker 无发布端口、只有 Web
  有绑定入口。
- 在隔离数据库中跑新库初始化与 `alembic check`，然后启动完整栈执行 health、根页面、
  深路径 SPA fallback、匿名拒绝、登录、CSRF 顺序、认证读取、原件保护和登出。
- 检查响应头含 `nosniff`、`Referrer-Policy: same-origin` 与
  `X-Frame-Options: DENY`；API location 设置了自己的 `add_header` 后需重复继承所需
  安全头，并设置 `Cache-Control: no-store`。确认静态资源可缓存、HTML 入口不缓存。
- 运行 Worker `--once`，空队列应正常退出并输出 scheduler/execution/processing 摘要。
- ARM 验证区分“配置解析成功”“跨平台镜像构建成功”“ARM 实机启动与端点 smoke 成功”。

## Wrong vs Correct

### Wrong

```yaml
api:
  ports:
    - "8000:8000"
```

### Correct

```yaml
web:
  ports:
    - "${WEB_BIND_ADDRESS:-0.0.0.0}:${WEB_PORT:-8080}:8080"
```

API 只留在内部网络；浏览器通过 Web 同源代理访问。
