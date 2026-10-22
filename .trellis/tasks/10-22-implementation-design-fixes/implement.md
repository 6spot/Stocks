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

- [ ] 五个叶子均有 AC 证据和真实状态。
- [ ] 集成场景通过，报告与原始证据可追溯。
- [ ] 领域契约与实现一致，未引入新迁移或旧版兼容。
- [ ] 记录延期验收与限制，按用户授权提交/归档；不把 planning 材料作为实施完成证明。
