# 执行计划

前置事实见 research/probe-live.md（真实探测）与 design.md。所有运行验证在 `100.120.14.84` 本任务隔离目录 `/tmp/stocks-v0-v2-real-mcp-tavily`。

- [x] 1. `ToolConfig` 增加 `query_credential`（env+param，与 bearer_token_env 互斥）；endpoint 基线校验不变；快照只加形态标记。
- [x] 2. `client.py`：`_EndpointClient` 首次请求时读环境变量、校验值形状、生成注入后 `self._request_url`；`_post` 改用它。错误码沿用 `TOOL_CREDENTIAL_MISSING`/`TOOL_CREDENTIAL_INVALID`。
- [x] 3. `config/tools.yaml` 加 `tavily_search`（schema 收敛 query+max_results≤5，budgets max_calls=2）；`config/sources.yaml` 加 `tavily_web` Source；`config/tasks.yaml` 仅 `research_writer` 授权。
- [x] 4. 测试：config 层 + client 层共 8 个新用例；远程 `tests/test_tools.py` **81 passed**，`test_execution.py`+`test_research_supplement.py` **52 passed, 33 skipped**。
- [x] 5. 真实验收：经生产 `McpClient` 完成 initialize → tools/list → `tavily_search`，4.184 秒返回真实 CSIS 结果（1,367 字符），`is_error: false`；证据 `verification/live-tavily-call.json`。SSRF 校验保持开启。
- [x] 6. `docs/execution.md` 凭据契约已补；`verification/README.md` 记录受控/真实边界、网络路径与失败修正。
- [ ] 7. 展示提交分组（Phase 3.4 一次确认）；清理：远端 `/etc/hosts` 条目、隧道套接字、隔离目录（`.env` 的 key 保留——它是部署配置，属 Owner 数据）。`.env.bak-tavily` 在确认无回滚需要后删除。

## 验证命令（远程）

```bash
cd /tmp/stocks-v0-v2-real-mcp-tavily/source/backend
.venv/bin/python -m pytest -q tests/test_tools_config.py tests/test_tools_client.py
# 真实验收（经隧道 + /opt/stocks/.env）按 verification README 记录的入口执行
```

## 边界

- Tavily 免费档限流低（实测 429）：真实验收控制在 1–2 次调用，失败重试需间隔 ≥90s 且有新证据。
- MP-01/MP-02 等既有缺陷不并入；不动 Evidence/Fact/报告流程。
- 密钥只在 `/opt/stocks/.env`（Owner 维护），仓库/日志/任务文档零密钥。
