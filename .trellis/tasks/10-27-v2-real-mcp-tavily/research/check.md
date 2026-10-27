# 复核记录

独立 `trellis-check` Agent（`8988199d`）在读取任务上下文后空闲 4 分钟未产出，按执行边界规则取消，未留下结论。本文件为主会话在取消后完成的自查记录，**不冒充独立复核**。

## 自查项与结论

| 检查项 | 结论 | 证据 |
| --- | --- | --- |
| `QueryCredential` 字段校验 | 通过：`env` 复用 `ENVIRONMENT_NAME_PATTERN`，`param` 拒绝非可见 ASCII，`extra="forbid"`，长度 1..128 / 1..64 | `config.py:557-589` |
| 两种凭据形态互斥 | 通过：`validate_tool` 在 endpoint 校验前拒绝同时设置 | `config.py:690-693` |
| 凭据值边界与 Bearer 一致 | 通过：非空、≤8192、可见 ASCII，同一错误码 | `client.py:290-315` |
| 只注入请求 URL，无 Authorization | 通过：`_headers` 仅看 `bearer_token_env`；`_post` 使用 `_request_target()` | `client.py:590`、测试 `test_query_credential_is_injected_into_the_request_url_only` |
| 百分号编码 | 通过：`quote(value, safe="")`，测试用运行时拼接的期望值并断言 `quoted != secret` | `client.py:313`、测试 |
| 凭据不进错误 details/日志/快照 | 通过：错误 details 不含 URL；快照只记 `credential_injection`；测试断言 `tvly-`、`TAVILY_API_KEY`、`tavilyApiKey` 均不出现 | 测试 3 处断言 |
| 惰性解析未削弱校验 | 通过：`_request_target()` 首次请求时解析并缓存，`_validate_destination()` 仍基于 `tool.endpoint` 解析 host；真实调用首次因回环地址被拒，证明 SSRF 校验生效 | `client.py:287-315`、真实验收记录 |
| `tools.yaml` 配置 | 通过：schema `query` 必填、`max_results` 1..5；`output_path: [structuredContent, results, 0]`；`max_calls: 2` | 实际加载验证 |
| `sources.yaml` / `tasks.yaml` | 通过：`tavily_web` 字段完整、`enabled: false`；仅 `research_writer.tools == ["tavily_search"]` | 实际加载验证 |
| `docs/execution.md` | 与实现一致：查询参数形态、互斥、注入后 URL 不落日志/快照/错误、快照只记形态 | 文档 diff |
| 测试非重言式 | 通过：19 条断言均针对实际行为（请求头缺失、URL 编码、错误码、快照内容、SSE 全流程） | 测试 diff |

## 未复核 / 未验证

- 无独立第三方复核结论；上述为主会话自查。
- 真实 Tavily 结果跑完整补证 Run（入库 → Chunk → 引用）未执行，仅由既有受控测试覆盖。
- `tavily_web` 的 `trust_tier: T3` 是保守取值，未经产品评审。
