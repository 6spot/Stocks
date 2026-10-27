# 验证记录

环境：`root@100.120.14.84:/tmp/stocks-v0-v2-real-mcp-tavily`，Python 3.12 公共工具环境，editable 指向本任务 `backend/src`。所有运行验证只在测试服务器执行，本机未跑任何测试。

## 受控测试

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_tools.py` | **81 passed**，exit 0 |
| `.venv/bin/python -m pytest -q tests/test_execution.py tests/test_research_supplement.py` | **52 passed, 33 skipped**，exit 0（跳过项为需要数据库/外部依赖的既有用例） |
| `load_tool_configuration("config/tools.yaml")` | 通过：`['tavily_search']`，mapping `('structuredContent','results',0)` |
| `load_task_configuration("config/tasks.yaml")` | 通过：`research_writer.tools == ['tavily_search']`，8 个 Task |
| `tool_authorization_snapshot` | 只含 `credential_injection: "query"`；断言 `has_secret: False`（快照无凭据、无参数名、无注入 URL） |

新增测试覆盖：查询参数凭据注入到请求 URL、无 `Authorization` 头、百分号编码、凭据缺失/非法在请求前失败且错误不泄漏、快照无泄漏、与 `bearer_token_env` 互斥、字段形状（含未知字段/非法 param/超长）、无会话 SSE 全流程（Tavily 形态）。

## 真实验收

`verification/live-tavily-call.json`，经**生产 `McpClient`**（非探测脚本）：

- 工具 `tavily_search`，remote_name 同名，source_id `tavily_web`，凭据形态 `query`。
- 完整序列 initialize → tools/list → tools/call，`discovered: ["tavily_search"]`。
- 结果：`is_error: false`，`content_type: text/event-stream`，耗时 **4.184 秒**。
- 返回真实结果：CSIS《Collateral Damage: The Domestic Impact of U.S. Semiconductor Export Controls》，`content_len: 1367`。
- 预算：`max_calls: 2`、`max_tokens: 16000`、`max_request_bytes: 100000`、`max_result_bytes: 1000000`（本次 1 次调用）。

### 真实调用的网络与安全边界（必须如实记录）

测试服务器无公网 HTTPS 出网，经 SSH 反向转发访问：远端 `/etc/hosts` 加 `127.0.0.1 mcp.tavily.com`，`ssh -R 127.0.0.1:443:mcp.tavily.com:443`，TLS 证书按真实域名校验。因 hosts 覆盖使系统解析返回回环地址，验收脚本向 `McpClient` 注入返回 **Tavily 真实公网 IP**（`44.215.241.178` / `107.21.183.12` / `3.215.6.4`）的 resolver；**SSRF 校验保持开启**（非公网地址仍会拒绝，本轮首次尝试即被正确拒绝，证明校验生效）。生产部署不需要该注入。

## 未验证 / 边界

- 未做完整研究 Run 的端到端取材：Tool 结果如何经 `research/supplement.py` 入库为 Document 并进入报告引用，仅由既有受控测试（`test_research_supplement.py` 52 passed）覆盖，未用真实 Tavily 结果跑一次完整补证 Run。
- Tavily 返回的是搜索结果页摘要文本（`content`），不是抓取的完整原文；引用指向该结果及其摘要。
- 免费档限流低：本轮多次触发 `429`，真实调用控制在 1–2 次。
- `tavily_web` Source 为 `enabled: false`（搜索来源不参与 RSS/HTTP 采集循环），仅作为 Tool 取回原文的 provenance 身份。

## 失败与修正（如实保留）

1. 密钥注入通道三次失败（两次空值、一次非 key 值）；改由 Owner 直接写入 `/opt/stocks/.env` 后成功。
2. 首次 `tools/call` 因累计探测触发 `429`，等待后成功。
3. `result_mapping` 初版写 `[results, 0]`，实际 `results` 位于 `structuredContent` 下，修正为 `[structuredContent, results, 0]`。
4. 一度误读 `ToolCallResult.result`（原始结果）而非 `.value`（映射结果），已修正验收脚本。

## 资源清理

- 远端 `/etc/hosts` 的 `127.0.0.1 mcp.tavily.com` 覆盖已从备份恢复并删除备份；`grep -c tavily /etc/hosts` 为 0。
- SSH 反向转发已关闭（`-O exit`），控制套接字删除，远端 443 无监听。
- `/tmp/stocks-v0-v2-real-mcp-tavily` 与传输包已删除，`ls /tmp | grep -c v2-real-mcp-tavily` 为 0。
- `/opt/stocks/.env.bak-tavily` 已删除；`TAVILY_API_KEY` 值保留在 `/opt/stocks/.env`（属 Owner 的部署配置，不是测试残留）。
- 本任务未创建数据库或对象存储容器。
