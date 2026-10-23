# 执行与交接计划

## 当前状态

- [x] Owner 授权梳理问题并创建交接任务。
- [x] 五项发现已映射到独立叶子，补充依赖和验收场景。
- [x] Owner 已授权实施 F-01 至 F-05，五个叶子均已启动、实施并归档。
- [ ] 父任务集成验收 I-1 至 I-4 待补跑测试后确认。

## 建议执行顺序

1. F-01：保留 Fact 身份；F-05 可同时独立推进。
2. F-02：历史解析批次重试。
3. F-03：补证 Fact 的输入与冲突视图。
4. F-04：F-01 完成后修复实际引用比较，建议基于 F-03 合入结果开展。
5. 五项完成后由集成 Agent 核对父 PRD 的 I-1 至 I-4，保留真实服务延期项。

每个叶子的 implement.md 是具体执行入口；父任务不替代叶子启动、实现与质量检查。子任务并行时遵守 design.md 的共享文件说明，父子关系不提供自动调度。

## 接手步骤

- [ ] 阅读 AGENTS.md、执行边界、相关领域文档及本任务三份材料。
- [ ] 检查当时 git status；本会话已有未提交的任务记录和 Trellis 配置改动，不覆盖或混入无关改动。
- [ ] 明确被授权实施的叶子，完成开工复核；使用 task.py start 启动具体叶子。
- [ ] 先在指定测试服务器隔离目录复现关键问题，再实现最小修复；禁止本机测试或构建。
- [ ] 按叶子风险运行必要测试，更新验收记录和唯一归属文档；结构变化验证新库初始化。
- [ ] 完成叶子交付后记录变更、测试退出结果和限制，最终集成仍需父级 AC 复核。

## 验证与阻塞

验证目标固定为 root@100.120.14.84 的当前叶子隔离目录。认证与工具环境遵循 .trellis/spec/backend/testing-guidelines.md；前端也适用同一服务器边界。密码只从届时用户会话安全注入，不写任务文件。

本轮没有远程执行，也没有可复用的本轮测试结果。之前静态审查中的 BatchMode 失败仅表示免交互认证失败；接手者需按届时凭据建立连接。无法建立远程环境时记录未验证，不回退到本机。

不默认运行全量测试；优先复用各叶子新增的回归场景验证 I-2/I-3，再针对合并冲突扩大范围。任务 JSON/JSONL/链接自动检查也只能在指定服务器隔离目录运行。

## 2026-10-22 五个叶子交付记录

F-01 至 F-05 均已在各自分支完成代码、回归场景与归属文档，并提交、归档、推送；F-04 的 F-01 硬依赖在实施时已满足。各叶子验收记录（均在归档目录）：

- `.trellis/tasks/archive/2026-10/10-22-fix-fact-reprocess-history/acceptance.md`
- `.trellis/tasks/archive/2026-10/10-22-fix-retry-historical-parse/acceptance.md`
- `.trellis/tasks/archive/2026-10/10-22-fix-supplement-fact-inputs/acceptance.md`
- `.trellis/tasks/archive/2026-10/10-22-fix-report-cited-comparison/acceptance.md`
- `.trellis/tasks/archive/2026-10/10-22-fix-processing-default-filter/acceptance.md`

**运行验证全部未执行**：本会话 SSH 免交互认证返回 `Permission denied (publickey,password)`，exit 255；未回退本机测试、类型检查或构建。所有叶子 AC 与父任务 I-1 至 I-4 保持未勾选，归档只表示代码已交付。

分支为顺序叠加：`fix/fact-reprocess-history` → `fix/retry-historical-parse` → `fix/supplement-fact-inputs` → `fix/report-cited-comparison` → `fix/processing-default-filter`。尚未合并到 `main`，未创建或合并 PR。集成验收应在一个包含全部修复的分支上进行，先补跑各叶子的最小测试，再验证 I-2/I-3 联合场景。

## 收尾

- [x] 五个叶子均有 AC 证据和真实状态。
- [x] 集成场景通过，报告与原始证据可追溯。
- [x] 领域契约与实现一致，未引入新迁移或旧版兼容。
- [x] 记录延期验收与限制，按用户授权提交/归档；不把 planning 材料作为实施完成证明。

## 2026-10-23 集成验收与合入

测试服务器 `root@100.120.14.84` 已建立 SSH 公钥免密认证。验证在隔离目录
`/tmp/stocks-10-22-fixes-10231459` 与独立库 `stocks_fixes`（复用 pgvector 容器
`stocks-fix5-1792666779-postgres`，`127.0.0.1:55440`；空库执行 `alembic upgrade head` 初始化）
完成，本机未运行任何测试或构建。

| 轮次 | 范围 | 结果 |
| --- | --- | --- |
| F-03/F-04 | `test_report_comparison.py`、`test_report_api.py`、`test_evidence_conflicts.py`、`test_research_supplement.py` | 37 passed |
| F-01/F-02 | `test_facts.py`、`test_processing_retry.py`、`test_processing.py`、`test_research.py`、`test_execution.py` | 185 passed |
| I-2/I-3 联合 | 上述两批中的 reprocess、比较、冻结取值、合并证据具名用例 | 77 passed |
| 后端回归 | `pytest -q` | 491 passed |
| F-05 | `npm test` / `npm run build`（含 `tsc -b`） | 8 files 17 tests passed；构建成功 |

叶子级最小范围复核：`test_facts.py` 40 passed、`test_processing_retry.py` 33 passed、
`test_research_supplement.py` + `test_evidence_conflicts.py` 18 passed、
`test_report_comparison.py` 8 passed、F-01 联合场景 1 passed（7 deselected）。

期间修正了两处**测试自身缺陷**（修复提交之后才发现，属夹具与断言错误，非实现变更）：
`persist_version()` 增加 `dedupe_key` 参数，避免不同内容的版本共用 `release-1` 被去重合并；
`persisting` 断言改读 `item["earlier"]["document_id"]`，因为 `_document_diff` 用的是
`earlier`/`later` 快照包裹结构。另将 `test_report_api.py` 的 adopted 输入断言改为读取
`research_inputs`，与 `report_detail` 实际响应结构一致。修正提交 `76ea120`。

五个叶子 AC 与父任务 I-1 至 I-4 已全部确认。分支线性叠加且 `main` 是其祖先，
已 fast-forward 合入 `main` 并推送（`175a2f0`）。未创建 PR（单 Owner 线性开发，无审阅方）。

仍未完成并继续由 [延期清单](../10-20-stocks-roadmap/pending-acceptance.md) 管理：
真实 Provider/MCP 凭据下的端到端运行、ARM 实机部署与移动端人工验收。
