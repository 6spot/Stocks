# 实现交接

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `backend/src/stocks/tools/config.py` | 新增 `QueryCredential` 模型（`env` 复用 `ENVIRONMENT_NAME_PATTERN`；`param` 可见 ASCII、1..64；`extra="forbid"`）；`ToolConfig.query_credential` 可选字段；`validate_tool` 中与 `bearer_token_env` 互斥；`tool_snapshot_entry` 的 `credential_required` 覆盖两种形态并新增 `credential_injection: "query"|"header"|null`；`__all__` 导出 `QueryCredential` |
| `backend/src/stocks/tools/client.py` | `_EndpointClient` 新增 `_request_target()` / `_build_request_url()`：惰性读取环境变量、复用 Bearer 的非空/≤8192/可见 ASCII 校验、`quote(value, safe="")` 注入、缓存结果；`_post` 改用 `self._request_target()`。SSE、会话、预算、重试、`initialize` 版本断言未改 |
| `backend/tests/test_tools.py` | 新增 8 个测试：注入到 URL 且无 Authorization、百分号编码与快照无泄漏、缺失/非法凭据在请求前失败、错误不回显凭据、无会话 SSE 全流程（Tavily 形态）、与 bearer 互斥、字段形状参数化 |
| `config/tools.yaml` | 新增 `tavily_search`（endpoint、remote_name、`source_id: tavily_web`、`query_credential`、timeout 60、max_sse_events 200、schema、`output_path: [structuredContent, results, 0]`、预算 max_calls 2）；文件头补 `query_credential` 字段说明 |
| `config/sources.yaml` | 新增 `tavily_web`（`http_api`、`enabled: false`、`trust_tier: T3`、max_redirects 0） |
| `config/tasks.yaml` | `research_writer.tools: [tavily_search]`（原 `[]`） |
| `docs/execution.md` | Tool 凭据契约补查询参数注入形态、互斥、注入后 URL 不落日志/快照/错误、快照只记形态 |

## 关键决定

1. **凭据惰性解析**：首版在 `__init__` 立即解析，导致 `McpClient(...)` 构造即抛错，与 Bearer 的"请求时失败"语义不一致，测试也据此失败。改为首次请求时解析并缓存，错误码与 Bearer 完全一致。
2. **`result_mapping` 用 `output_path` 而非 `fields`**：`research/supplement.py:_normalized_content` 只消费**单个** `{title, url, content, ...}` 对象；Tavily 返回 `structuredContent.results` 数组，因此取 `[structuredContent, results, 0]`（首条）。`fields` 无法表达数组元素。
3. **`content` 语义**：Tavily 的 `content` 是搜索结果摘要/提取文本（实测 1,367 字符），不是整页原文。引用指向该结果与摘要，不宣称全文。
4. **`tavily_web` 为 `enabled: false`**：Tool 取回的原文需要 Source 身份做 provenance，但该 Source 不参与 RSS/HTTP 采集循环。

## 真实调用结构（实测，供后续参考）

`tools/call` 的 CallToolResult：
- `content[0].text` = JSON 字符串（含 `results`）
- `structuredContent` = 已解析对象：`{query, follow_up_questions, answer, images, results[], response_time, request_id, auth_mode}`
- `results[i]` = `{url, title, content, score, raw_content, id}`

`ToolCallResult.result` 是原始 CallToolResult；**映射结果在 `.value`**。

## 未做 / 交给主会话

- 远程测试、真实验收、文档最终措辞、提交与清理由主会话完成（已执行，见 `verification/README.md`）。
- 未改 Evidence/Fact/报告流程；未动 MP-01/MP-02。
- `tavily_web` 的 trust tier 取 T3 是保守选择，若 Owner 认为搜索结果应更低/更高可调整。
