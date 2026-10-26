# 错误处理规范

## 失败分类

- 无法继续的配置错误应在使用前明确失败。`backend/src/stocks/providers.py` 的 `resolve_env()` 和 `load_models()` 对缺少环境变量或不支持的协议抛 `ValueError`；`backend/src/stocks/s3.py` 对缺少必要 endpoint 抛 `RuntimeError`。
- 外部请求只对调用方定义为可重试的失败重试或回退。`ModelClient.chat()` 对指定 HTTP 状态、连接错误和超时尝试下一个配置目标；其他 HTTP 错误通过 `raise_for_status()` 传播。`backend/tests/test_providers.py` 分别覆盖回退和不回退的边界。
- 捕获异常时保留原始原因。所有候选目标失败时，`ModelClient.chat()` 使用 `raise RuntimeError(...) from last_error`；新增封装也应让诊断路径可追踪。

在 API 边界把需要返回给调用方的失败转换为明确 HTTP 状态和安全的错误内容。不要用广泛的 `except Exception` 把配置错误、权限错误或程序缺陷伪装成可重试故障；也不要在错误响应或日志中泄露凭据。

## 模型调用超时与路由预算

**路由级超时预算必须落到最终 HTTP 客户端，不能只写在配置里。** `stocks.providers.research_chat()` 曾用 provider 目标自身的 `target.timeout_seconds`（60s）建 `httpx.AsyncClient`，导致 `config/models.yaml` 路由级 300s 预算从未生效：研究 Agent 在 60s 处 MODEL_TIMEOUT，配置看起来正确却永远通不过。修复后的契约：候选目标循环内 `request_timeout = max(target.timeout_seconds, routing.timeout_seconds)`，即路由预算封顶、不小于目标默认；普通 `chat()` 与 embedding 路径仍以各自 `target.timeout_seconds` 为准，不受路由影响。回归测试见 `tests/test_providers.py::test_research_request_uses_the_routing_timeout_budget`。

两个 `httpx` 测试细节：超时以普通 dict 写入 `request.extensions["timeout"]`（值是 int，如 `{"connect": 300, ...}`），断言用 `timeout.get("connect")` 而不是下标或切片；MockTransport handler 内抛出的 `TypeError` 会被 `research_chat` 的 `except (ValueError, KeyError, TypeError)` 捕获并当作 `invalid_response` 重试，因此 mock 内不能让被测断言以异常形式逃逸——需要在 handler 里记录状态、返回合法响应。

## 原生决策协议接入

### Scope / Trigger

新增模型协议或拆分模型职责时，先确认接口能表达的输出类型。产品字段与配置契约归 [任务执行与模型接入](../../../docs/execution.md) 和 [数据设计](../../../docs/data.md)；本节只规定适配边界。

### Signatures

`async ModelClient.typesafe_decision(routing: ChatRouting, *, state: dict, questions: dict, validate_content: Callable[[str], object]) -> ChatResponse` 调用原生 `/systemone`；`classification/jev.py` 构造与校验决策；`decision.classify_document(...) -> ClassificationDecision` 合并结果。不把原生决策塞入 Chat Completions 的任意 JSON Schema 路径。

### Contracts

凭据经环境变量准备，只在实际使用路由时要求可选供应商配置完整；未启用的独立分类服务不得阻止研究或向量路由加载。分类与开放实体抽取是两个显式角色，保存各次调用身份、使用量与依据；不能把第二角色作为第一角色失败后的隐式回退。

### Validation & Error Matrix

| 条件 | 处理 |
| --- | --- |
| 缺失/多余答案、类型或候选不匹配、非法或非有限概率 | 拒绝输出，处理阶段失败，不保存空成功 |
| 概率为 `0.01`、`0.99` 等数值 | 按配置阈值比较，拒绝 bool 冒充数字，不用 `bool(probability)` |
| 开放实体不在输入原文证据中 | 拒绝结果，不从候选名单或系统知识补全 |
| 可重试传输失败 | 遵守现有有限次数、超时与脱敏 attempt 记录 |
| 失败响应含服务商正文或密钥 | 只暴露固定错误，不能写入状态、日志或快照 |

### Good / Base / Bad Cases

Good：受控协议测试同时检查请求与严格响应，并用真实持久化验证阶段失败可见。Base：空实体列表有明确有效输出且不伪造公司。Bad：直接将原生概率当布尔值，或固定候选选择失败后无记录地调用生成模型。

### Tests Required

参考 `tests/test_classification_jev.py`：官方请求字段/认证、问题与候选限制、答案身份/数值、实体证据、两个角色的来源与用量、失败阶段可见性，以及未配置可选供应商时其他路由仍可用。真实供应商调用必须另记；Mock 不能等同外部验收。

### Wrong vs Correct

Wrong：`should_extract = bool(answer["noul"])`。
Correct：先验证有限的 0..1 数字，再与已验证的配置阈值比较；实体生成作为明确的独立步骤执行，任一步失败均保留失败语义。

## 外部采集

`stocks.collection.errors.CollectionError` 是采集边界的稳定错误载体，包含 `code`、固定 `message`、可选脱敏 `url` 和结构化 `details`。配置、Feed 和 HTTP 失败使用不同错误码；外部异常可保留为 Python `__cause__` 供诊断，但 CLI/API 输出只调用 `to_dict()`，不得返回异常原文、完整响应或请求头。

配置在发出网络请求前完成全部校验。单个 Feed 条目或文章失败不得中断同批其他条目；文章正文失败或为空时，可以用实际取得的 Feed 标题与摘要生成 `completeness = partial`，同时保存脱敏 `fetch_error`，不能把 Feed 响应当成文章正文。

## 外部 Tool 与 MCP

`stocks.tools.errors.ToolError` 是 Tool 边界的稳定错误载体（`code` + 固定 `message` + 结构化 `details`），子类按配置、授权、协议、传输和预算分类；`stocks.tools.client` 在请求前后强制校验
HTTPS 公网解析、禁重定向、响应字节/JSON 深度/SSE 事件数、凭据格式与预算，任何失败都
转成上述错误码，不把远端原文放进异常、日志或快照。凭据只在准备请求头时从环境变量读取，
缺失即失败（`TOOL_CREDENTIAL_MISSING`），不写入配置、Run 快照或 ModelCall。

**SSE 响应完成条件（`_EndpointClient._read_sse()`）：** 请求完成不取决于连接 EOF。客户端按
字节收取经过 `_iter_body()` 同一字节预算与总截止时间检查的块，用 LF/CR/LF+CR（含跨块拆分的
CRLF）切行，只对完整事件解码 UTF-8；事件内 `data:` 行以换行拼接、注释和 `event:`/`id:`/`retry:`
跳过、空事件也计入 `max_sse_events`。收到与当前请求 ID 匹配的 JSON-RPC 结果并通过
`_response_result()` 后立即返回已接收字节与结果，外层 `finally` 关闭响应；通知先于结果时跳过。
错误响应、错配 ID、非法 JSON/UTF-8、事件/字节/截止时间超限、或 EOF 仍无结果均明确失败，
不得用后台无上限读取线程、重试或放宽限额换取“读完”。普通 `application/json` 响应仍由
`_read_body()` 完整读取；不要为了 SSE 复用把整响应缓冲写回普通 JSON 路径。

回归断言应落在真实 `httpx` 流上（读取次数、是否继续读、响应是否关闭、失败码），而不是只
断言 mock 被调用。受控流测试不构成真实外部 MCP 服务或授权验收；真实 endpoint 行为需另外
验收并记录来源、授权范围和当天版本。

两个 `httpx` 细节容易踩坑：0.28 的 `Client.send()` 不接受 `timeout=` 参数，每请求超时
必须通过 `build_request(..., timeout=...)` 或 `stream()`/`send(stream=True)` 的请求扩展
传递；`httpx` logger 默认在 INFO 记录每个请求 URL，工具客户端在构造时必须把
`logging.getLogger("httpx")` 降到 WARNING，否则 endpoint 会绕过脱敏约定进入日志。

远端的 `notifications/initialized` 只保证 2xx，不保证 202 或空响应体；只有非 2xx 或显式
rejected 状态才算失败。
