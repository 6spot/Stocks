# 技术设计

## 现状与目标边界

现有 Tool 客户端（`backend/src/stocks/tools/client.py`）已具备 Streamable HTTP 传输、SSE/JSON 双响应解析、无会话容忍（`MCP-Session-Id` 可缺省）、有限重试与全套预算/安全边界。真实探测确认 Tavily 远程 MCP 的三个差异点：

1. 凭据只能（在 initialize 层面）走 URL 查询参数 `?tavilyApiKey=<key>`（Bearer 在 initialize 返回 401；tools/call 层面双轨均可）。
2. 协议协商回显客户端 offer（`2025-11-25` 被接受），现有严格版本断言无需放宽。
3. 响应一律 SSE（`text/event-stream`）；`_rpc` 已统一处理，无需新代码路径。

因此本任务的实现核心只有**一个配置契约扩展**：凭据注入 URL 查询参数。客户端协议层零改动。

## 配置契约：`query_credential`

`ToolConfig` 新增可选块 `query_credential`（与 `bearer_token_env` 互斥）：

```yaml
query_credential:
  env: TAVILY_API_KEY        # 环境变量名，复用 ENVIRONMENT_NAME_PATTERN 校验
  param: tavilyApiKey        # 查询参数名，可见 ASCII，1..64
```

校验规则：

- `env`/`param` 均必填非空；与 `bearer_token_env` 同时出现即配置错误（`load_tool_configuration` 拒绝）。
- endpoint 仍禁止自带 query/fragment（注入前的基线校验不变）。
- 值校验沿用 Bearer 的边界：非空、≤8,192 字符、可见 ASCII（`ord 0x21..0x7E`）。API key 类凭据天然满足；含空格/控制字符的值在 URL 中非法，提前拒绝。
- 注入方式：请求 URL = `endpoint + ("?" if "?" not in endpoint else "&") + param + "=" + quote(value, safe="")`。endpoint 已验证无 query，实际恒为 `?` 分支；保留 `&` 判断只是防御重复注入。
- **注入后的 URL 不做二次 `validate_https_url`**（它拒绝 query，会自我矛盾）；注入前 endpoint 已通过完整校验（HTTPS、公网解析、无 query/fragment），注入仅追加凭据参数。`_validate_destination` 只校验 host，与 URL query 无关，保持不变。

## 脱敏契约（核心安全属性）

- 配置文件只含变量名与参数名，永不含值；值仅从 `environment`（进程环境）读取。
- `tool_snapshot_entry` / `tool_authorization_snapshot` 保持只记 `credential_required` 类布尔/名称字段，不出现注入后 URL 或值。快照新增 `credential_injection: "query"` 标记（仅形态，无值）。
- HTTP 层：`httpx` 日志降级 WARNING 的既有约定不变；错误 `details` 只含 status/limit 等结构化字段。注入后 URL 只存在于 `build_request` 调用点，不进入异常、日志或 ModelCall 留痕。
- `_PinnedNetworkBackend`/`_validate_destination` 用 endpoint host 解析，天然不含 query。

## 客户端改动点（最小）

`client.py` 只改两处：

1. `_EndpointClient.__init__` 预计算 `self._request_url`：有 `query_credential` 时在构造时读取环境变量并校验值形状（缺失/形状非法立即 `TOOL_CREDENTIAL_MISSING`/`TOOL_CREDENTIAL_INVALID`，与 Bearer 同码），注入生成完整 URL；否则为 `tool.endpoint` 原样。凭据读取从每请求一次（Bearer 现状）变为每客户端实例一次——`McpClient` 每工具调用新建 `_EndpointClient`，语义等价且更早失败。
2. `_post` 的 `build_request` 改用 `self._request_url`。

SSE 解析、会话处理、预算、重试、`initialize` 版本断言全部不动。Tavily stateless + SSE 已被现有代码覆盖（探测实测确认）。

## tools.yaml 接入

```yaml
tools:
  - id: tavily_search
    enabled: true
    endpoint: https://mcp.tavily.com/mcp/
    remote_name: tavily_search
    source_id: tavily_web
    query_credential: {env: TAVILY_API_KEY, param: tavilyApiKey}
    timeout_seconds: 60
    max_sse_events: 200
    input_schema:
      type: object
      additionalProperties: false
      required: [query]
      properties:
        query: {type: string, minLength: 1, maxLength: 500}
        max_results: {type: integer, minimum: 1, maximum: 5, default: 3}
    result_mapping:
      fields:
        title: [results, title]
        url: [results, url]
        content: [results, content]
    budgets: {max_calls: 2, max_tokens: 16000, max_request_bytes: 100000, max_result_bytes: 1000000}
```

`source_id: tavily_web`：`config/sources.yaml` 同步新增对应 Source（trust_tier 按外部网络检索定位、`kind: web_search` 或现有最接近分类），Tool 检索到的原文登记在该 Source 下。授权：`config/agents.yaml` 仅 `research_writer` 的 `tools` 列表加 `tavily_search`，deny-by-default 不变。

Tavily 返回结构（探测实测）：顶层 `{"query", "results": [{"title","url","content",...}]}` JSON 文本，`result_mapping` 按数组路径 `[results, title]` 逐条映射（沿用现有数组映射语义，实现时按 `ResultMappingConfig` 实际能力核对；若现有映射只支持单对象路径，则收敛为 `[results]` 列表字段，报告引用取每条 url）。

## 测试与文档

- `backend/tests/test_tools_config.py`（现有）：`query_credential` 合法/缺失字段/与 bearer 互斥/param 字符集/endpoint 仍禁自带 query；快照无值泄漏。
- `backend/tests/test_tools_client.py`（现有）：MockTransport 断言请求 URL 含注入参数、无 Authorization 头；凭据缺失/形状非法错误码；注入后 URL 不出现在任何错误 details；SSE initialize（SSE 帧返回 result）全流程。
- `docs/execution.md`：Tool 凭据契约补「查询参数注入」形态与脱敏要求。
- 真实验收：测试服务器经隧道用 `/opt/stocks/.env` 密钥完成一次受控 Task 下的真实 `tavily_search`，记录调用身份/耗时/预算消耗/返回 URL，`external_success` 如实标记。

## 回滚

单文件契约扩展 + 配置接入，无数据库/迁移改动。回滚 = 移除 `query_credential` 配置块与新增测试；`tavily_search` 从 agents 授权移除即恢复 deny-by-default。
