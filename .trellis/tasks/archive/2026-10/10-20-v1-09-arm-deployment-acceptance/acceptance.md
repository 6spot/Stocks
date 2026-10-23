# V1-09 部署与日常运行验收记录

- 验收日期：2026-10-22（Asia/Singapore）
- 测试服务器：`root@100.120.14.84`，架构 `x86_64`
- 隔离目录：`/tmp/stocks-v1-09-arm-deployment.15Ckjwkq`
- Compose project：`stocks_v109_smoke`
- Web 测试入口：`127.0.0.1:18081`，容器内 `8080`
- 数据库：本任务独立 PostgreSQL/pgvector 卷与测试库 `stocks_v109_test`
- 本机只用于读取、编辑、打包和传输；未在本机运行 pytest、Docker、Compose、
  Alembic、数据库、API 或外部集成验证。SSH 口令、数据库/Owner/session/Provider
  凭据只存在于临时环境，未写入仓库、本记录或验证输出。

## 结论

部署代码和可重复验证的边界已完成；AC-2、AC-3 通过。AC-1 的认证、端口隔离和
受保护读取已在任务独立的 amd64 Compose 栈上通过，但“目标 ARM 实机启动并跨设备
使用”仍未通过，因此 AC-1 保持 pending，不能把本任务标成目标环境验收完成。

## 实际验证

| 范围 | 实际结果 |
| --- | --- |
| Compose 解析 | `docker compose config --quiet` exit 0；`TARGET_PLATFORM=linux/arm64` 时同样 exit 0 |
| Web 镜像 | Node 20 多阶段构建、`npm ci`、内置类型检查与 `npm run build` 成功；Nginx 运行镜像成功 |
| API/Worker 镜像 | 同一后端镜像成功构建并启动；依赖下载增加 `--prefer-binary` 后，测试机上的可重复构建通过 |
| 生产依赖审计 | `npm ci --omit=dev` 后 32 个生产依赖、0 漏洞；构建告警来自开发依赖 |
| 全新数据库 | `alembic upgrade head` exit 0；`alembic check` 输出 `No new upgrade operations detected.` |
| 服务状态 | PostgreSQL、API、Worker、Web 全部启动；API/Web/数据库健康检查通过 |
| HTTP smoke | `/health` 200、`/` 200、深路径 SPA fallback 200；匿名报告和原件 401 |
| Owner 会话 | 登录 200；Cookie `HttpOnly`、`SameSite=strict`；会话 200；认证报告/Overview 200；登出后 401 |
| CSRF 与安全错误 | 无 CSRF 的变更请求 403；带 CSRF 但模型配置缺失 503 且错误码稳定；响应不含变量名、端点或异常文本；未创建 Run |
| 入口安全头 | 根页面、静态资源和认证 API 响应均含 `nosniff`、`Referrer-Policy: same-origin`、`X-Frame-Options: DENY`；HTML 不缓存，静态资源长缓存，API 响应 `no-store` |
| 端口边界 | Compose 只有 Web `127.0.0.1:18081->8080`；API、Worker、PostgreSQL 无发布端口；主机无 `5432`/`8000` 监听 |
| Worker 单轮 | `python -m stocks.worker --once` exit 0；空队列输出 scheduler/executed/processing 摘要 |
| 后端定向回归 | `tests/test_console_api.py tests/test_api.py tests/test_change_watermarks.py tests/test_worker_processing.py`：**70 passed, 1 warning** |
| 新 503 回归 | 触发、续扫和“活跃 Run 重复触发不解析配置”定向：**3 passed, 13 deselected** |
| 配置读取边界回归 | 新增缺失凭据、配置缺失、非 UTF-8 配置，以及活跃 Run 重复触发定向：**5 passed, 13 deselected, 1 warning** |
| Nginx 配置复检 | 新配置通过 `nginx -t`；重建 Web 镜像并更新隔离栈后，认证 `/api/reports`、`/api/overview` 和模型配置缺失的 503 均返回三项安全头及 `Cache-Control: no-store` |
| Python 编译检查 | 清理远端 `._*` 传输元数据后，`python -m compileall -q src tests alembic` exit 0 |
| ARM 构建尝试 | ARM 专用 buildx 构建器成功创建，但拉取 `node:20.19-bookworm-slim` 和 `python:3.12-slim` 元数据均因 Docker Registry `i/o timeout` 失败；跨平台镜像构建未开始 |
| 真实浏览器/手机 | 本任务未新增真机验收；V1-08 已有桌面与 390px Chromium smoke，真实手机视觉和跨设备入口仍 pending |

唯一测试警告为现有 FastAPI/Starlette TestClient 对当前 httpx 版本的弃用提示。
没有把 skip、未开始步骤或 amd64 运行结果计作 ARM/真实外部依赖已通过。

## 2026-10-23 补充：真实 arm64 镜像构建与全栈运行

上一轮 ARM 构建因 Registry `i/o timeout` 未开始。本轮在测试服务器安装 qemu
`binfmt`（`docker run --privileged --rm tonistiigi/binfmt --install arm64`）后重试，
在隔离目录 `/tmp/stocks-arm64-10231540` 完成真实跨平台构建与运行：

| 范围 | 实际结果 |
| --- | --- |
| 跨平台构建能力 | `docker buildx inspect` 列表加入 `linux/arm64`；此前只有 amd64 变体 |
| 后端镜像 | `docker compose build api`（`TARGET_PLATFORM=linux/arm64`）exit 0；镜像 `Architecture: arm64` |
| 后端容器内运行 | `platform.machine()` → `aarch64`，Python 3.12.15 |
| Web 镜像 | `docker compose build web` exit 0；镜像 `Architecture: arm64` |
| Web 容器内运行 | `uname -m` → `aarch64`，`nginx/1.27.5`，静态产物存在 |
| 全新库初始化 | arm64 容器内 `alembic upgrade head` exit 0；`alembic check` → `No new upgrade operations detected.` |
| 全栈启动 | `docker compose up -d --wait`：postgres、api、worker、web 全部 `healthy`，exit 0 |
| 入口探针 | `/health` 200、`/` 200、匿名 `/api/documents` 401 |
| Owner 会话（arm64 栈） | 登录 200 并返回 `csrf_token`；`/api/documents`、`/api/reports`、`/api/overview`、`/api/tasks` 均 200 |
| 筛选边界（arm64 栈） | `?status=failed&limit=100` → 200；`?stage=failed` → 422（非法阶段值） |
| CSRF 边界 | 无 CSRF 的 `POST /api/tasks/collect/pause` → 403 |

### 本轮发现并修复的部署缺陷

全栈首次可用后，重建 `api` 容器会拿到新地址，而 `web` 仍代理到旧地址：**`/health`
与 `/api/` 持续 502，但两个容器都报告 `healthy`、`compose --wait` 也返回成功**。
复现方式是用占位容器占住旧地址后再启动 `api`，修复前稳定返回 502。

原因：`deploy/nginx.conf` 的 `proxy_pass http://api:8000` 是字面上游名，Nginx 只在
worker 启动时解析一次并永久缓存。已改为 `resolver 127.0.0.11 valid=10s` 加变量
`proxy_pass`，并显式转发 `$request_uri`。修复后同一复现序列（不重启 `web`）返回
`/health` 200、匿名 `/api/documents` 401。提交 `7c334c4`，契约同步到
`.trellis/spec/backend/deployment-guidelines.md`。

### AC-1 状态更新

- **已从 pending 缩小到“仅剩实机与真机”**：跨平台镜像构建成功、arm64 容器内运行成功、
  全新库初始化无漂移、四服务全栈 `healthy`、入口/登录/受保护读取/CSRF 边界均在 arm64
  栈上通过。此前“ARM 镜像未构建”的阻塞已解除。
- **仍为 pending 的部分**：没有目标 ARM 实机，qemu 模拟不等于真实硬件；真实手机视觉、
  跨设备登录与公网/HTTPS 入口仍未执行。因此 AC-1 不改为通过。

本轮未修改应用代码以外的行为；未在本机运行 Docker、构建或任何项目验证。

## AC 对照

### AC-1：目标 ARM + Compose 可启动并跨设备认证使用；未认证不能读报告或原件，数据库未向公网开放 — pending

- 已在 amd64 任务栈通过：完整服务启动、Owner 登录、Cookie、受保护报告/原件、
  同源 Web 代理、SPA fallback、安全响应头，以及 API/数据库/Worker 无发布端口。
- 2026-10-23 已在 qemu 下补齐跨平台镜像构建与 arm64 全栈运行（见上节“补充”），
  此前“ARM 镜像未构建”的阻塞已解除；剩余项只有真实硬件、真机与公网入口。
- 真实手机、公网/TLS 入口也没有执行。生产部署需显式设置
  `TARGET_PLATFORM=linux/arm64`、`OWNER_COOKIE_SECURE=true`，并由目标主机提供
  HTTPS 或私网反向代理。

### AC-2：采集/研究按各自配置运行，报告失败保留最后成功版本；有限重试和人工恢复能完成 — 通过

- 本轮核对并引用 V1-01 的真实隔离 PostgreSQL/CLI 验收：采集任务不解析研究模型、
  定时与手动触发共享活跃 Run 约束、进程 kill 后 Owner 结束并重试、成功步骤不重复。
- V1-02、V1-03、V1-04 的已完成验收分别覆盖四阶段积压、有限重试与中断恢复、
  分类失败不阻塞索引、Fact 按需/结构化映射和旧报告引用保留。
- 本任务在实际镜像上验证 Worker 单轮正常退出；模型配置缺失时触发/续扫稳定 503，
  不会排入残缺 Run。真实 Provider 的完整研究成功链仍 pending，不能用 mock 代替。

### AC-3：晚提交事务、晚到公告、扫描触顶和 Scope 改变场景通过，所有 V1 子任务验收有实际记录 — 通过

- V1-05 已在真实 PostgreSQL 两连接事务中验证晚提交水位、固定窗口续扫、失败不推进、
  Scope 变化重新初始化和边界不丢不重。
- V1-06 验证扫描触顶时未知计数不填零、保留原始续点、Document/Fact 同一证据链不双算、
  filtered-out 不阻塞游标推进。
- V1-01/02/03/04/05/06/07 的 `acceptance.md` 均存在实际命令和结果；V1-08 的
  验收命令与结果记录在归档任务的 `implement.md`，该归档任务没有单独的
  `acceptance.md`。未把只有设计说明的能力列为完成。

## 已执行的关键命令

```text
docker compose --project-name stocks_v109_smoke config --quiet
TARGET_PLATFORM=linux/arm64 docker compose --project-name stocks_v109_arm config --quiet
docker compose --project-name stocks_v109_smoke build api worker
docker compose --project-name stocks_v109_smoke up -d --no-build --wait api worker web
sh ./smoke.sh
docker compose --project-name stocks_v109_smoke run --rm worker \
  python -m stocks.worker --once

TEST_DATABASE_URL=<temporary-test-db> \
  .venv/bin/python -m pytest -q -p no:randomly \
  tests/test_console_api.py tests/test_api.py \
  tests/test_change_watermarks.py tests/test_worker_processing.py

# 配置读取边界补测（当前隔离栈的 PostgreSQL 容器网络地址）：
TEST_DATABASE_URL=<temporary-test-db> \
  .venv/bin/python -m pytest -q --tb=short -p no:randomly \
  tests/test_console_api.py \
  -k 'model_configuration or duplicate_trigger_returns_active_run'
```

密码、完整连接串、模型密钥、S3 凭据和 session secret 均未记录。

## 未完成与待验清单

- 目标 ARM 实机：镜像构建、启动、新库初始化、四服务健康检查、跨设备登录与原件读取。
- 真实第三方 Provider：研究生成、embedding、Decision/Generation 的实际协议、认证、
  限流和结构化输出。
- 真实 S3：目标端点、bucket 权限、跨主机网络、快照读写与哈希核对。
- 真实手机/浏览器：跨设备导航、触控、键盘、TLS Cookie 与视觉布局。
- 公网/TLS：域名、证书、入口 ACL、反向代理和 `Secure` Cookie 的完整链路。
- 测试机到 Docker Registry 曾出现基础镜像元数据超时；本轮所有可开始的服务和测试均
  实际执行，未验证项保持 pending，不以本机或 amd64 结果替代。

## 资源处置

本轮保留 `stocks_v109_smoke` 栈和隔离目录，供 Owner 最终核对；未删除其他任务容器、
卷或目录。临时 buildx builder 已删除。测试结束后若要清理，只删除本任务的
Compose project、卷、目录和一次性凭据，并确认没有原始证据需要保留。
