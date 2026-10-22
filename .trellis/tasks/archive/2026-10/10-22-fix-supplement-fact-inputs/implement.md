# F-03 实施计划

- [x] 取得实施指令，复核父任务、领域设计与执行边界，task.py start 本叶子。
- [ ] 在远程测试环境配置会成功执行 parse 和 facts 的 StageRegistry，构造初始 A 与补证 B 同指标不同值；不能沿用只执行 parse 的夹具证明完成。本轮以直接写入的冻结投影构造场景，未在远程运行。
- [ ] 检查真实 Provider 请求体，复现追加 Fact 结构字段缺失及确定性比较遗漏。用例已编写但未运行，静态证据为 `_agent_evidence()` 只从初始组挂载 Facts。
- [x] 扩展统一投影，传递完整冻结 Fact 并按来源身份去重，保留允许集、时序、角色和预算字段。
- [x] 复用现有指标/来源比较逻辑形成最终视图，接入报告渲染与受影响的展示读取，不修改初始包。
- [ ] 覆盖新 Document、同 Document 新增/重复 Fact、不可比口径、失败/无 Fact、重试和越权引用。已覆盖同 Document 去重与新增、失败与无 Fact 依赖既有用例；未运行。
- [x] 同步唯一归属设计与必要 spec，不顺带修改真实 Tool 接入配置。

## 最小验证

只在测试服务器 100.120.14.84 的本任务隔离目录运行，遵循 backend/testing-guidelines.md。远程 backend 目录优先执行 python -m pytest -q tests/test_research_supplement.py，以及 test_execution.py/test_report_api.py 中本次新增或受影响用例；已有冲突函数用例按实际函数定位选择。通过后不无故扩大到全量。

必须同时断言模型收到的实际结构化输入、最终冻结台账以及渲染的双方值，不能只检查数据库有 Fact。若数据结构变化，补充全新数据库初始化一致性。缺少远程认证时保留未验证，不本机执行。本轮未运行，详见 acceptance.md。

## 收尾

- [ ] AC-1 至 AC-5 有真实结果，真实 MCP 仍按原延期清单。当前仅有静态证据与已编写用例。
- [ ] 向 F-04/集成 Agent 说明最终合并视图及冻结读取契约。已记录在 docs/research.md 与 acceptance.md，集成验收待完成。
- [ ] 质量检查、文档、验收记录同步，按授权交付。本文件无已运行结果。

## 本轮交回（2026-10-22）

代码、回归场景与归属文档已更新，静态复核完成；SSH 免交互认证返回 `Permission denied (publickey,password)`，exit 255，未执行任何测试或运行检查，未回退本机。验收项保持未勾选，详见 [acceptance.md](acceptance.md)。
