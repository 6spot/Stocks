# 设计：来源级采集代理

## 配置契约

`SourceConfig.proxy_url: str | None`，长度上限 2048。允许 `http`/`https`、主机、可选端口和可选 userinfo；拒绝空白/控制字符、路径、query、fragment 与非法端口。字面量在加载时校验；`\${ENV}` 形式在 `resolved_proxy_url()` 时解析，缺失抛 `SOURCE_PROXY_UNAVAILABLE`，非法抛 `SOURCE_PROXY_INVALID`，错误只带 `source_id`，不带代理值。

代理按来源生效，不设全局默认，避免改变现有 SEC/Fed/CNBC/FRED 的直连行为。Reddit 配置显式引用 `\${COLLECTION_PROXY_URL}`。

## 请求路径

`SafeFetcher.fetch(..., proxy_url=None)`：

1. 先校验当前目标 URL 并解析/校验目标地址（现有 `_validate_destination`）。
2. 有代理且未注入测试 transport：`httpx.Client(proxy=proxy_url, follow_redirects=False, timeout=remaining)`，不使用 `_PinnedTransport`。
3. 无代理：保持现有 `_PinnedTransport` 固定地址连接。
4. 注入 transport（测试）优先，代理参数仍做格式校验，但不建立真实代理连接。

`collect_source` 在开始时解析一次 `proxy_url`，传给 feed、正文和 `_collect_structured_source` 的全部 fetch 调用；不把代理写进 `CollectionSummary`。

## SSRF 取舍

代理模式下应用仍校验目标域名解析出的地址，但实际连接由代理解析，无法保证代理解析结果与校验一致。因此代理必须由 Owner 显式配置、视为受信基础设施；不把该模式描述为“固定 IP 防重绑定”。直连模式继续固定地址。

## 错误与脱敏

代理配置错误使用 `ConfigurationError`；请求失败沿用 `TIMEOUT`/`REQUEST_FAILED`，错误详情只含目标 URL。任何路径都不记录 `proxy_url` 或 userinfo。

## 测试与验收

- 配置/解析单测；
- 用 monkeypatch 捕获 `httpx.Client` kwargs，证明有代理时传 `proxy`、无代理时传固定 transport；
- 用录制 fetcher 证明三类请求都带来源代理；
- 远程定向回归；真实 Reddit RSS 经测试部署代理入库并核对 Document；
- 重建 api/worker 镜像，记录代理环境变量与来源结果。
