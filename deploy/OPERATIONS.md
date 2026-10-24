# Stocks 真实环境部署说明

部署位置：`100.120.14.84:/opt/stocks`（**不是 `/tmp`**——`/tmp` 是 tmpfs，重启即丢）。

## 访问

| 项 | 值 |
| --- | --- |
| Web 入口 | `http://100.120.14.84:8080`（同网段）；Tailscale 下 `http://100.120.14.84:8080` |
| API / PostgreSQL | 不发布主机端口，仅经 Web 同源代理访问 |
| 登录密码 | 见服务器 `/opt/stocks/.env` 的 `OWNER_PASSWORD`（权限 600，不入库） |

读取密码：`ssh root@100.120.14.84 'grep ^OWNER_PASSWORD= /opt/stocks/.env'`

## 填模型端点（你要做的唯一一步）

编辑 `/opt/stocks/.env`：

```dotenv
MODEL_API_BASE_URL=https://你的chat端点/v1
MODEL_API_KEY=你的key
MODEL_NAME=模型名

EMBEDDING_API_BASE_URL=https://你的embedding端点/v1
EMBEDDING_API_KEY=你的key
EMBEDDING_MODEL_NAME=模型名
```

注意：

- 两个 base URL **必须带 `/v1`**，且都必须是 OpenAI 兼容协议。
- **Embedding 必须是 1024 维**。当前向量空间固定为 1024（`embedding_space.py` 与
  `config/models.yaml` 的 `dimensions: 1024`）。换维度需要同时改建表定义并重建库。
- 未配置模型时：采集、解析、索引、检索仍可用；需要模型的研究任务会返回
  `503 MODEL_CONFIGURATION_UNAVAILABLE`，不会静默失败。

改完重启：

```bash
cd /opt/stocks && docker compose up -d
```

## 常用运维

```bash
cd /opt/stocks
docker compose ps                      # 状态
docker compose logs -f api worker      # 日志
docker compose restart worker          # 只重启 Worker
docker compose down                    # 停止（保留数据卷）
docker compose up -d --build           # 改代码后重建
```

数据卷：`stocks-prod-pgdata`（PostgreSQL）。**`docker compose down -v` 会删除全部数据。**

## 对象存储

复用既有 SeaweedFS 容器 `stocks-both-s3`，已接入 `stocks_internal` 网络：

- 容器内地址：`http://stocks-both-s3:8333`
- Bucket：`stocks-both`
- 主机回环端口：`127.0.0.1:18346`（仅本机）

该容器不是 Compose 管理的，`docker compose down` 不会删除它。若服务器重启后 S3 容器未自动
启动，需手动 `docker start stocks-both-s3` 并重新接入网络：

```bash
docker network connect stocks_internal stocks-both-s3
```

## 与验收环境隔离

| 环境 | 数据库 | 说明 |
| --- | --- | --- |
| **生产（本部署）** | `stocks`（新卷 `stocks-prod-pgdata`） | 独立 PostgreSQL 容器 |
| 验收证据 | `stocks_both` / `stocks_v2_acceptance` / `stocks_review_fixes` | 在 `stocks-test-v0-03-postgres`（`55435`），**不要删** |

两者只共享对象存储桶。生产库为空库，从零开始积累。

## 尚未完成

- **公网 HTTPS**：当前只有 HTTP。跨设备经公网访问前必须加 TLS 反向代理，并把
  `OWNER_COOKIE_SECURE=true`。属 PA-3 未完成项。
- **模型端点与凭据**：待你填写。
- 真实远程 MCP、费用端点、第三方 S3 等仍为 PA-3 待验收项。

## 已知环境特性（部署时实测）

- **`/tmp` 是 tmpfs（7.8G），重启即丢**，因此本部署在 `/opt/stocks`。不要把部署目录或数据卷
  放回 `/tmp`。
- **SeaweedFS 只在启动时绑定当时已存在的网卡。** 给 `stocks-both-s3` 追加新网络后，新网卡上
  不会监听 8333（重启容器也无效）。若必须换网络，需以目标网络作为主网络重建容器，并挂回原
  数据卷 `35c7fa37ebd1e641c184f17e74663314143a6b441ec783b07eb0d8c16d6c82a9`：

```bash
docker rm -f stocks-both-s3
docker run -d --name stocks-both-s3 --restart unless-stopped \
  --network stocks_internal \
  -v 35c7fa37ebd1e641c184f17e74663314143a6b441ec783b07eb0d8c16d6c82a9:/data \
  -p 127.0.0.1:18346:8333 \
  chrislusf/seaweedfs server -dir=/data -s3 -s3.port=8333
```

- 首次构建后端镜像时 pip 下载约 20–50 kB/s，全程约 20 分钟。后续重建有缓存，会快很多。

## 模型端点未配置时的表现（正常）

`classify` / `facts` / `index` 阶段会分别以 `DECISION_ROUTING_UNAVAILABLE` /
`FACT_ROUTING_UNAVAILABLE` / `EMBEDDING_ROUTING_UNAVAILABLE` 失败，这是预期行为。
采集与解析不受影响。填好 `.env` 后，用 `stocks.processing retry` 重新排队即可：

`retry` 必须按 Document 指定，没有 `--all-failed`。先列出失败项：

```bash
docker compose exec worker python -m stocks.processing status --status failed
```

再对每份 Document 的每个失败阶段重排队（可重复 `--document-id`）：

```bash
docker compose exec worker python -m stocks.processing retry \
  --document-id <DOC_ID> --stage classify --stage facts --stage index
```

不指定 `--execute` 时只重新排队，由 Worker 领取执行。
