# 验收记录

- 修复：`_read_sse()` 改为在同一字节/时间预算内增量读取，按字节级 LF/CRLF/CR 行边界组装事件，完整匹配响应通过既有 `_response_result()` 校验后立即返回；通知与注释跳过，错误/错配 ID/非法 JSON/非法 UTF-8/流结束无结果仍明确失败。
- 删除被替代的整流 SSE 解析路径；普通 JSON 响应仍完整读取。
- 测试：`backend/tests/test_tools.py` 新增受控流回归，覆盖 initialize/list/call 提前返回与响应关闭、跨块 UTF-8、跨块 CRLF、多行 data、通知、协议错误、事件数/字节/截止时间限额。
- 验证位置：`100.120.14.84:/tmp/stocks-review-fixes-20261023`，专用数据库 `stocks_review_fixes`，持久 Python 3.12 环境。
- 命令：`python /tmp/stocks-review-run.py test tests/test_tools.py`。
- 结果：**69 passed in 0.65s，退出码 0**（`sse-tests.log`）；两个修改过的 Python 文件 `py_compile` 与空白检查通过（`sse-static.log`）。
- 限制：受控 `httpx` 流回归，不是真实外部 MCP 服务/授权验收；未单独运行类型检查。既有真实 MCP 外部验收缺口保持不变。
