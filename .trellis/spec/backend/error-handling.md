# 错误处理规范

## 失败分类

- 无法继续的配置错误应在使用前明确失败。`backend/src/stocks/providers.py` 的 `resolve_env()` 和 `load_models()` 对缺少环境变量或不支持的协议抛 `ValueError`；`backend/src/stocks/s3.py` 对缺少必要 endpoint 抛 `RuntimeError`。
- 外部请求只对调用方定义为可重试的失败重试或回退。`ModelClient.chat()` 对指定 HTTP 状态、连接错误和超时尝试下一个配置目标；其他 HTTP 错误通过 `raise_for_status()` 传播。`backend/tests/test_providers.py` 分别覆盖回退和不回退的边界。
- 捕获异常时保留原始原因。所有候选目标失败时，`ModelClient.chat()` 使用 `raise RuntimeError(...) from last_error`；新增封装也应让诊断路径可追踪。

在 API 边界把需要返回给调用方的失败转换为明确 HTTP 状态和安全的错误内容。不要用广泛的 `except Exception` 把配置错误、权限错误或程序缺陷伪装成可重试故障；也不要在错误响应或日志中泄露凭据。

## 外部采集

`stocks.collection.errors.CollectionError` 是采集边界的稳定错误载体，包含 `code`、固定 `message`、可选脱敏 `url` 和结构化 `details`。配置、Feed 和 HTTP 失败使用不同错误码；外部异常可保留为 Python `__cause__` 供诊断，但 CLI/API 输出只调用 `to_dict()`，不得返回异常原文、完整响应或请求头。

配置在发出网络请求前完成全部校验。单个 Feed 条目或文章失败不得中断同批其他条目；文章正文失败或为空时，可以用实际取得的 Feed 标题与摘要生成 `completeness = partial`，同时保存脱敏 `fetch_error`，不能把 Feed 响应当成文章正文。

## 外部 Tool 与 MCP

`stocks.tools.errors.ToolError` 是 Tool 边界的稳定错误载体（`code` + 固定 `message` + 结构化 `details`），子类按配置、授权、协议、传输和预算分类；`stocks.tools.client` 在请求前后强制校验
HTTPS 公网解析、禁重定向、响应字节/JSON 深度/SSE 事件数、凭据格式与预算，任何失败都
转成上述错误码，不把远端原文放进异常、日志或快照。凭据只在准备请求头时从环境变量读取，
缺失即失败（`TOOL_CREDENTIAL_MISSING`），不写入配置、Run 快照或 ModelCall。

两个 `httpx` 细节容易踩坑：0.28 的 `Client.send()` 不接受 `timeout=` 参数，每请求超时
必须通过 `build_request(..., timeout=...)` 或 `stream()`/`send(stream=True)` 的请求扩展
传递；`httpx` logger 默认在 INFO 记录每个请求 URL，工具客户端在构造时必须把
`logging.getLogger("httpx")` 降到 WARNING，否则 endpoint 会绕过脱敏约定进入日志。

远端的 `notifications/initialized` 只保证 2xx，不保证 202 或空响应体；只有非 2xx 或显式
rejected 状态才算失败。
