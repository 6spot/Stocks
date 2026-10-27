# V2-01 真实远程 MCP 调试：Tavily Search

## 目标

用 Owner 提供的真实 Tavily 远程 MCP 端点完成 V2-01 中「真实远程 MCP 服务」的验收：把端点接入 Stocks 的 Tool 配置，在隔离环境完成一次真实搜索调用，记录协议差异与最终验收边界。

## 背景与已确认事实（2026-10-26 有界探测）

- **端点形态**：`https://mcp.tavily.com/mcp/?tavilyApiKey=<key>`。initialize 返回 `protocolVersion: 2025-03-26`、`serverInfo: tavily-mcp/4.1.4`。
- **认证双轨**：远程 MCP 同时接受 `?tavilyApiKey=` 查询参数和 `Authorization: Bearer <key>`（服务端错误信息明示两种方式）。首次探测时 Bearer 返回 401 的结论仅对 initialize 步骤成立，后续服务端信息显示 tools/call 层面 Bearer 同样被接受；待恢复密钥后实测确认。
- **无会话（stateless）**：initialize 200 但**不返回 `MCP-Session-Id`**；后续 `tools/list`、`tools/call` 无需会话头。`notifications/initialized` 返回 202、空 body，符合现有客户端「只保证 2xx」的契约。
- **响应格式**：`text/event-stream`（SSE）。现有 `_read_sse()` 已支持无会话 SSE 完成条件，但 initialize 结果解析需要适配 SSE 帧（当前探测用临时脚本解析 data: 行取得 result）。
- **工具清单**：`tavily_search` / `tavily_extract` / `tavily_crawl` / `tavily_map` / `tavily_research`（注意是下划线命名；本轮只需要 `tavily_search`）。
- **网络路径**：测试服务器无公网 HTTPS 出网（SEC/GitHub/Tavily 握手全被切）。已按既有模式建立 SSH 反向转发：`/etc/hosts` 加 `127.0.0.1 mcp.tavily.com`，`ssh -R 127.0.0.1:443:mcp.tavily.com:443`，控制套接字 `/tmp/stocks-tavily-mcp.sock`；远端仍以真实 URL + TLS SNI/证书校验访问，密钥不出现在 URL 以外位置。
- **密钥注入**：密钥仅经内存/stdin 传输，不入库不入日志。首次注入成功（服务端错误信息回显确认过非空 key），后续注入读到空值，**尚未完成一次真实搜索调用**。恢复时重新粘贴密钥即可继续。

## 需求

1. **配置契约扩展**：`ToolConfig` 需要支持「凭据注入 URL 查询参数」的形态（键名可配置，如 `tavilyApiKey`），值来自环境变量。凭据仍不写入配置文件、日志或快照；URL 校验需为「注入前校验 endpoint、注入后不重复校验/不记录完整 URL」留出明确契约。日志与错误信息继续脱敏。
2. **协议适配**：initialize 结果允许 SSE 帧格式；无会话模式下不发送/不要求 `MCP-Session-Id`。客户端现有 SSE 读取预算与完成条件沿用，不为 Tavily 放宽限额。
3. **工具接入**：`config/tools.yaml` 新增 `tavily_search`：Streamable HTTP、schema 收敛到 `query`（可选 `max_results`，默认收敛到小值）、`result_mapping` 取 Tavily 返回的 title/url/content，预算按现有 `max_calls: 2` 量级起步。仅授权给一个受控 Task/Agent 配置，deny-by-default 不变。
4. **真实调用验收**：在测试服务器隔离目录，经现有 Tool 客户端完成至少一次真实 `tavily_search`（如半导体出口管制新闻），记录：调用身份、耗时、usage/预算消耗、返回 URL 与内容头部、脱敏后的错误路径；同一 Run 的外部成功标记如实记录。
5. **同步文档与测试**：受控测试覆盖 URL 查询参数凭据注入与 SSE initialize 解析；`docs/execution.md` 的 Tool 凭据契约补「查询参数注入」形态及脱敏要求。不默认全量测试。

## 验收标准

- [x] 配置：`tavilyApiKey` 以环境变量注入 URL 查询参数；配置校验通过，日志/错误无凭据泄漏。
- [x] 客户端：SSE initialize 解析通过；无会话模式完成 initialize → tools/list → tools/call 全流程。
- [x] 真实调用：测试服务器至少一次真实 `tavily_search` 成功返回搜索结果，预算与留痕字段完整（`verification/live-tavily-call.json`）。
- [x] 文档与测试同步；不把受控测试写成真实验收。

## 实际验收

受控测试：`tests/test_tools.py` 81 passed；`test_execution.py`+`test_research_supplement.py` 52 passed / 33 skipped；配置与授权快照实加载通过且无凭据泄漏。真实验收：生产 `McpClient` 经 SSH 隧道完成 initialize → tools/list → `tavily_search`，4.184 秒返回真实 CSIS 半导体出口管制分析（1,367 字符），`is_error: false`，`external_success: true`；SSRF 校验全程开启（首次尝试因回环解析被正确拒绝，证明校验生效）。

未验证：真实 Tavily 结果跑完整补证 Run 的入库/引用链路（由既有受控测试覆盖）；Tavily `content` 是搜索结果摘要而非整页原文。详见 `verification/README.md`。

## 非目标

不接入 tavily_extract / crawl / map / research；不改动 Evidence Selection、Fact 或报告流程；不在本轮处理 SEC 全文（另按用户决策跟进）；不修改部署或公网暴露；不把 Tavily 凭据写入仓库、日志或任务文档。
