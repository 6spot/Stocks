# 验收记录

## 结论：部署完成并运行中，模型端点待 Owner 填写

## 部署事实

| 项 | 值 |
| --- | --- |
| 部署目录 | `100.120.14.84:/opt/stocks`（持久盘，非 tmpfs） |
| 代码版本 | `b100e86`（`main`，打包上传，无 `.git`） |
| Compose 项目 | `stocks`，四服务 `postgres` / `api` / `worker` / `web` |
| 数据卷 | `stocks-prod-pgdata`（新建，空库起步） |
| 网络 | `stocks_internal`（Compose 内部网络） |
| Web 入口 | `0.0.0.0:8080`，仅此一个主机发布端口 |
| 对象存储 | 复用 `stocks-both-s3`，`http://stocks-both-s3:8333`，bucket `stocks-both` |
| 凭据 | `/opt/stocks/.env`，权限 600，未入库 |

## 实际验证

| 检查 | 结果 |
| --- | --- |
| `docker compose config --quiet` | 通过 |
| 镜像构建 | `stocks-api` / `stocks-worker` / `stocks-web` 全部成功 |
| 全新库 `alembic upgrade head` | `0001` 应用成功 |
| `alembic check` | `No new upgrade operations detected` |
| 四服务状态 | api/postgres/web `healthy`，worker 运行中 |
| `GET /health` | 200 |
| `GET /` | 200 |
| `GET /api/reports`（匿名） | 401 |
| `GET /research`（SPA fallback） | 200 |
| 安全头 | `nosniff`、`Referrer-Policy: same-origin`、`X-Frame-Options: DENY` 均在 |
| 登录 | 200，返回 `authenticated: true` 与 CSRF token |
| 认证读取 `/api/reports`、`/api/overview`、`/api/sources` | 200 |
| 端口暴露 | 仅 `0.0.0.0:8080`；5432/8000 未发布 |
| Worker `--once` | 正常退出，输出 scheduler/execution/processing 摘要 |
| 容器内 S3 连通 | `list_objects_v2` 返回对象，原 bucket 数据完整 |
| 真实采集冒烟 | SEC RSS 20 条入库，S3 对象数 61，Document 20、Chunk 60 |

## 过程中发现并处理的问题

1. **`/tmp` 是 tmpfs**（7.8G，重启即丢），因此部署目录改用 `/opt/stocks`。
2. **SeaweedFS 只绑定启动时已存在的网卡**：把 `stocks-both-s3` 接入 `stocks_internal` 后，
   容器内新网卡 `172.20.0.2` 上并未监听 8333，导致 `Connection refused`。
   重启容器也无效（重启后仍只绑 `bridge`）。最终以 `stocks_internal` 作为主网络重建该容器
   （保留同一数据卷 `35c7fa37…` 与 `127.0.0.1:18346` 端口），数据未受影响。
3. **镜像构建受网络限速影响**：pip 下载速率约 20–50 kB/s，单次 `docker compose build` 超时；
   改为后台构建并轮询，最终成功（约 22 分钟）。

## 当前状态与限制

- 采集、解析、S3 快照可用；20 份真实 SEC 文档已入库，其中 19 份 `complete`、
  1 份 `partial`（`TIMEOUT`，SEC 2026-109 页面）。
- `classify` / `facts` / `index` 全部 `failed`，错误码分别为
  `DECISION_ROUTING_UNAVAILABLE`、`FACT_ROUTING_UNAVAILABLE`、`EMBEDDING_ROUTING_UNAVAILABLE`——
  这是 `.env` 中模型端点留空时的**预期行为**，不是缺陷。填入端点后重试即可。
- **未配置模型端点与凭据**：由 Owner 在 `/opt/stocks/.env` 填写，见 `deploy/OPERATIONS.md`。
- **无公网 HTTPS**：当前仅 HTTP + `OWNER_COOKIE_SECURE=false`。跨设备经公网访问前必须加
  TLS 并把该值改为 `true`；属 PA-3 未完成项。
- 未验证：真实模型调用、真实研究 Run 产出报告、真实 MCP、费用端点、长期调度。

## 与验收环境的隔离

生产库 `stocks` 在新建卷 `stocks-prod-pgdata`；验收证据库 `stocks_both` /
`stocks_v2_acceptance` / `stocks_review_fixes` 仍在 `stocks-test-v0-03-postgres`（`55435`），
未改动。两者仅共享对象存储桶。
