# 验收记录：采集来源代理支持

日期：2026-10-28。范围：RSS / HTTP API 采集连接器的来源级代理；不含 MCP/Tavily 与模型调用。

## 实现

- `SourceConfig.proxy_url`：可选 `http`/`https` 代理，支持 `${ENV_NAME}`；`resolved_proxy_url()` 在来源使用时解析，缺失/非法只使该来源失败。
- `SafeFetcher.fetch(proxy_url=...)`：有代理时使用 `httpx.Client(proxy=...)`；无代理保持 `_PinnedTransport` 固定地址连接。
- 代理模式先用同一代理经 Cloudflare DoH 解析并校验目标地址（`DohResolver`），再经代理请求；避免本地 DNS 污染直接阻断，同时保留目标地址校验。代理仍由代理解析并建立最终连接，因此只用于受信代理。
- `collect_source` 将同一 `proxy_url` 传给 feed、条目正文和结构化 API 请求；代理值不进入 Document、日志或错误详情。
- 新增 `reddit_stocks` RSS 配置（默认 `enabled: false`，需 `COLLECTION_PROXY_URL`）；`.env.example`、`docs/data.md`、`docs/quickstart.md` 同步。

## 验证

- 远程隔离目录（`/tmp/stocks-proxy-20261028`）运行 `tests/test_collection.py`：**62 passed**。覆盖配置解析、环境缺失、非法/脱敏、`httpx.Client` 代理参数、feed 与结构化 API 代理透传。
- 真实代理抓取：测试服务器 `SafeFetcher` 经 `http://127.0.0.1:7890` 获取 Reddit RSS，返回 `application/atom+xml`、76,019 bytes；DoH 解析与目标校验通过。
- 真实入库（测试部署 `/opt/stocks`，`COLLECTION_PROXY_URL=http://172.20.0.1:7890`，临时配置 `max_items=3`）：
  - 来源 `reddit_stocks`，3 个 Document，`completeness=partial`；
  - Document IDs：`86451411-484e-44bd-b74d-24b5b1d7ba64`、`089fa4f5-8f2d-4523-ad6c-2d20e1824680`、`4f39a2f4-4546-4dcd-a06c-ba1899992385`；
  - 每个条目的 Reddit 文章 HTML 返回 `HTTP_STATUS 403`，按既有语义保存 Feed 标题与帖子正文为 `partial`，未写成完整原文；
  - 3 个 S3 原件均可读取，字节 SHA-256 与 `documents.content_hash` 前缀一致（`8f65b2cc19aa`、`d1be5a781223`、`20cdb01bb493`）。
- 部署镜像重建：`stocks-api`/`stocks-worker` 从新镜像重建，api healthy；worker 内 `COLLECTION_PROXY_URL` 已加载，来源配置读取 8 项。
- 下游处理状态：3 篇 `parse` 完成；`classify` 与 `index` 因测试环境 provider 连接失败（`MODEL_CONNECTION_FAILED` / `EMBEDDING_REQUEST_FAILED`）未完成，`facts` 仍 pending。该失败与代理无关，但意味着这 3 篇尚未进入可检索研究输入；未把它写成完整可用。

## 限制

- Reddit HTML 文章页返回 403；当前只保存 Feed 标题与帖子正文（`partial`），没有评论页全文。是否接受该粒度需 Owner 决定；`reddit_stocks` 默认关闭，测试部署为验收临时启用。
- 3 篇 Reddit Document 的 classify/index 未完成（provider 连接失败），因此不能宣称论坛内容已可被研究检索。
- DoH 使用 Cloudflare `https://cloudflare-dns.com/dns-query`，经同一代理访问；代理不可达时该来源以 `DNS_FAILED` 失败。
- 代理模式无法固定代理解析出的目标 IP；已在设计/文档中记录为受信代理取舍。
- 未改 MCP/Tavily；未实现代理池、自动切换或代理认证管理。
