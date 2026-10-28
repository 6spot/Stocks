# 采集来源代理支持

## 目标

为采集连接器增加可选的来源级 HTTP 代理，使测试服务器无法直连的来源（首个为 Reddit RSS）可以经既有宿主代理获取并进入标准保存/处理链路。范围仅限 RSS 与 HTTP API 采集，不改变 MCP/Tavily、模型调用或研究流程。

## 需求

- `SourceConfig` 增加可选 `proxy_url`，支持 `http://` 与 `https://`，可用 `\${ENV_NAME}` 从环境变量注入；未配置代理时行为不变。
- 代理应用于该来源的 feed 获取、条目正文获取和结构化 API 请求；不把代理地址、凭据写入 Document、日志或错误详情。
- 继续对目标 URL 做 SSRF 地址校验；代理模式下由代理解析目标，无法再固定目标 IP，必须在设计/文档中明确该取舍，且只对显式配置代理的来源生效。
- 新增 Reddit r/stocks RSS 来源作为真实代理验收；缺少代理环境变量时该来源明确失败，不影响其他来源。
- 更新数据契约、快速启动、示例配置与 PA-4。

## 验收标准

- [x] 配置单测覆盖：合法/非法代理 URL、环境变量缺失、凭据不出现在错误中、无代理来源保持原行为。远程 `tests/test_collection.py` 62 passed。
- [x] 采集单测证明 RSS feed 与 HTTP API 请求收到来源代理参数；条目正文复用同一 `proxy_url` 变量。远程 `tests/test_collection.py` 通过。
- [x] 远程定向回归通过；真实 Reddit RSS 经代理经标准入口保存 3 个 Document（`partial`），原件 S3 字节与哈希核对一致；文章 HTML 返回 403，未伪装为完整正文。
- [x] 代理不可用/未配置时仅该来源失败（`SOURCE_PROXY_UNAVAILABLE` / `SOURCE_PROXY_INVALID`），其他来源保持直连；测试覆盖脱敏。
- [x] 测试部署重建 api/worker 后代理仍生效；文档、示例配置与 PA-4 同步；真实代理地址仅存在于测试部署 `.env`，未提交。

## 非目标

不改 MCP/Tavily 传输；不实现代理池、自动切换、PAC、认证管理界面；不绕过目标站点访问控制；不把代理当作 SSRF 防护替代。
