# 在测试服务器部署真实运行环境

在 `100.120.14.84` 用仓库 `compose.yaml` 部署一套长期运行的 Stocks 全栈，供 Owner 实际使用。
模型 API 端点与凭据由 Owner 稍后在服务器上填写，本次只保证部署就绪、可登录、端点可配。

## 关键环境事实（已核实）

- 服务器：`om`，amd64；磁盘 `/` 452G 已用 38G。
- **`/tmp` 是 tmpfs（7.8G），重启即失**，因此部署目录必须放在 `/opt` 或 `/srv`，不能放 `/tmp`。
- 现有 SeaweedFS 容器 `stocks-both-s3` 运行中，`127.0.0.1:18346` → 8333，bucket `stocks-both` 可列（200）。
  它当前在默认 `bridge` 网络（IP `172.17.0.10`），**不在** Compose 的 `internal` 网络内。
- 现有 `stocks-test-v0-03-postgres`（`55435`）承载真实验收库 `stocks_both` / `stocks_v2_acceptance` /
  `stocks_review_fixes`；**新部署使用独立 PostgreSQL 与独立数据卷**，不复用该容器。
- 端口：80/443/8000/8080 当前均未被占用；`stocks-both-s3` 只发布回环端口。
- 向量空间为 **1024** 维（`embedding_space.py`、`config/models.yaml`），`EMBEDDING_MODEL_NAME`
  必须与该维度匹配。

## 交付范围

1. 在 `/opt/stocks` 落地部署目录：仓库代码（当前 `main`）+ `.env` + 持久数据卷。
2. 复用现有 `stocks-both-s3` 作为对象存储：把该容器接入部署网络（或改用可路由地址），
   避免再起第二个 S3。
3. `docker compose up -d` 起 postgres / api / worker / web 四服务，全部 healthy。
4. 首次初始化数据库（`alembic upgrade head` + `alembic check` 无漂移）。
5. 冒烟：`/health`、登录页可访问、未认证 API 返回 401。
6. 交付一份 Owner 操作说明：改哪个文件填模型端点、如何重启、如何看日志。

## 约束

- 不覆盖、不删除现有 `stocks_both` / `stocks_v2_acceptance` 数据与 `stocks-both-s3` 数据。
- 不把真实凭据写入仓库；`.env` 只存在于服务器，权限 600。
- 数据库、API、Worker 不发布主机端口；只有 Web 入口发布。
- 不做公网 TLS 终结（需域名与证书，属 PA-3 未完成项）；本次只提供内网/Tailscale 访问。
- 模型端点未配置时服务仍应能起，采集可用、研究任务明确报配置不可用，不得静默失败。

## 验收

- 四服务 `healthy`；`alembic check` 无差异；`/health` 200；未认证 `/api/reports` 401。
- S3 联通性：能列出/写入部署所用 bucket。
- 记录实际端口、访问方式、数据卷名、日志路径与未完成项（TLS、真实模型端点）。
