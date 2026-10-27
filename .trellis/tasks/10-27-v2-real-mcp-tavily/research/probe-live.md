# 真实 Tavily MCP 探测记录（2026-10-27）

## 环境

- 测试服务器 `100.120.14.84`，经 SSH 反向转发（远端 `/etc/hosts` 指向 `127.0.0.1` + `ssh -R 127.0.0.1:443:mcp.tavily.com:443`，控制套接字 `/tmp/stocks-tavily-mcp.sock`）。远端始终以真实 URL 访问，TLS SNI/证书校验未被绕过。
- 密钥由 Owner 填写在 `/opt/stocks/.env` 的 `TAVILY_API_KEY=`（第 30 行，改动前备份 `.env.bak-tavily`）。密钥值未进入本仓库、聊天记录或探测输出。

## 探测结论（与 PRD 背景一致，全部实测）

| 项 | 结果 |
| --- | --- |
| initialize（query 参数认证） | 200 / SSE / `tavily-mcp 4.1.4` / proto `2025-03-26` |
| `MCP-Session-Id` | 不返回（stateless） |
| `notifications/initialized` | 202 空体 |
| tools/list | `tavily_search` / `tavily_extract` / `tavily_crawl` / `tavily_map` / `tavily_research` |
| 认证双轨 | Bearer 在 initialize 返回 401；服务端错误信息明示 tools/call 层面 query 参数与 Bearer 均可 |
| 空值/错误值密钥 | 服务端明确报「key 为空」「key 不以 tvly- 开头」，认证失败路径清晰 |

## 第一次真实搜索（成功）

查询 `semiconductor export controls news`、`max_results=3`：

- HTTP 200，`isError: false`，text 5,037 字符。
- 返回 URL（前 3）：
  - `https://www.csis.org/analysis/collateral-damage-domestic-impact-us-semiconductor-export-controls`
  - `https://www.microchipusa.com/industry-news/us-semiconductor-export-controls-to-china-impact-and-future-outlook`
  - `https://en.wikipedia.org/wiki/United_States_New_Export_Controls_on_Advanced_Computing_and_Semiconductors_to_China`
- 结果为 JSON 文本（query/results 结构），`result_mapping` 可按 `results[].title/url/content` 映射。

## 过程中的失败与限流（如实记录）

1. 本机三轮密钥注入失败：两次空值、一次 53 字节非 key 值（以 `-dev-o` 开头）；按边界停止重试，改由 Owner 直接写服务器 `.env`。
2. 服务器上曾短暂存在注入通道给出的错误值文件（已删除）；`.env` 方案落实后未再使用临时文件。
3. 密钥生效后第一次 tools/call 触发 **429 限流**（多轮探测累计）；等待 90 秒后单发成功。Tavily 免费档速率限制很低，实现预算（`max_calls: 2`）与重试间隔需按此校准，真实验收控制在 1–2 次调用。

## 实现入口（据此进入 design.md）

- 配置：`ToolConfig` 增加查询参数凭据注入（env 变量名 + 参数名可配置）；endpoint 校验在注入前完成，注入后 URL 不入日志/快照。
- 客户端：initialize 结果解析适配 SSE 帧；stateless 模式不发送/不要求会话头；SSE 预算沿用现有 `_read_sse()` 契约。
- 工具：`tavily_search` 下划线命名；schema 收敛 `query`（可选 `max_results` 小值默认）；仅授权受控 Task/Agent。
