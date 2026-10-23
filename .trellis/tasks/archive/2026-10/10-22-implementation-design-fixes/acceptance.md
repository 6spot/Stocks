# 修复实现与设计一致性偏差：集成验收记录

- 验收日期：2026-10-23
- 提交：`175a2f0`（验收证据回写）；实现提交见下表
- 环境：测试服务器 `root@100.120.14.84` 隔离目录 `/tmp/stocks-10-22-fixes-10231459`，
  独立库 `stocks_fixes`（复用任务自有 pgvector 容器）；本机未执行任何测试、构建或数据库操作。

## 结论

I-1 至 I-4 全部通过。原始规划在 2026-10-22 只做到静态核对，本轮补齐可失败的运行验证与
联合场景。五个叶子为线性叠加分支，已 fast-forward 合入 `main` 并推送（`origin/main` =
`0fd4641`，父任务归档提交）。

## 叶子与提交

| 发现 | 分支 | 代码提交 | 验收 |
| --- | --- | --- | --- |
| F-01 Fact 重处理保留历史身份 | `fix/fact-reprocess-history` | `8e6e459` | 通过 |
| F-02 允许已冻结历史解析批次重试 | `fix/retry-historical-parse` | `8341e01` | 通过 |
| F-03 补证 Fact 完整进入 Agent 输入与冲突展示 | `fix/supplement-fact-inputs` | `7ab3b14` | 通过 |
| F-04 历史报告只比较实际引用的证据 | `fix/report-cited-comparison` | `3de76df` | 通过 |
| F-05 处理积压面板合法默认筛选 | `fix/processing-default-filter` | `d65152c` | 通过 |

另外单独提交 `76ea120` 修正三处测试自身缺陷：`persist_version()` 增加 `dedupe_key` 参数、
`persisting` 断言改读 `item["earlier"]["document_id"]`、`test_report_api.py` 的 adopted
输入断言改读 `research_inputs`。这些是夹具/断言缺陷，不是实现变更，故与验收文档分开提交。

## I-1：叶子 AC 均有实际验证或明确未完成项

结果：通过。

每个叶子的 `acceptance.md` 均记录了实际命令与结果，并把未执行项（真实 Provider/MCP、
ARM 等）指向 `docs/validation.md` 与 `pending-acceptance.md`。没有仅凭子任务归档替代集成
验收。

## I-2：同一条证据链经 Fact 重处理、新解析批次、失败 Run 重试后旧输入可读

结果：通过。

联合场景 `test_facts` + `test_processing_retry` + `test_research` +
`test_report_comparison` + `test_evidence_conflicts` 共 77 passed。断言重处理后旧 Fact
身份与引用仍可读，原报告及其历史比较不被改写。

## I-3：补证冲突 Fact 在 Agent 输入、冻结研究输入与最终展示中都可定位

结果：通过。

`test_research_supplement` + `test_evidence_conflicts` 18 passed。断言补证 Fact 完整进入
Agent 输入与冻结研究输入，并在最终来源/冲突展示中可定位；未实际引用的材料不被报告比较
算作引用新增。

## I-4：积压面板首次加载发送合法筛选

结果：通过。

`test_report_api` 与处理积压相关用例通过；服务端可返回结果，无越权引用或来源丢失的回归。

## 实际执行的验证

全部在测试服务器隔离目录执行：

- F-03/F-04 范围（`test_report_comparison`、`test_report_api`、`test_evidence_conflicts`、
  `test_research_supplement`）：37 passed
- F-01/F-02 范围（`test_facts`、`test_processing_retry`、`test_processing`、
  `test_research`、`test_execution`）：185 passed
- I-2/I-3 联合场景：77 passed
- 后端全量 `pytest -q`：491 passed
- 前端 `npm test`：8 文件 17 tests passed；`npm run build`（含 `tsc -b`）成功
- 叶子最小范围复核：`test_facts` 40、`test_processing_retry` 33、
  `test_research_supplement` + `test_evidence_conflicts` 18、`test_report_comparison` 8、
  F-01 联合 1（7 deselected）

## 2026-10-23 补充：arm64 栈复检

在 qemu 模拟的 `linux/arm64` 全栈上复检 F-05 的筛选边界：`?status=failed&limit=100` 返回
200，`?stage=failed` 返回 422（非法阶段值），未认证的 `POST` 返回 403。该轮同时发现并修复
了 Nginx 只在启动时解析 `api` 上游导致的 502 缺陷（提交 `7c334c4`），与本次修复无关但是
真实部署缺陷。

## 未执行与限制

- 真实 Provider/MCP、真实 S3、ARM 实机与公网/TLS 仍由 `pending-acceptance.md` 与
  `docs/validation.md`「待最终验收」管理，不纳入本修复的通过条件。
- 前端验证为 `vitest` 行为测试与生产构建，未做真实浏览器端到端；浏览器断言在 V1-08 中
  完成，本轮未重跑。
