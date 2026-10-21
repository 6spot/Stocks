# V1-01 开工复核（2026-10-21）

## 顺序与前置状态

- 下一交付叶子为现有 `10-20-v1-01-worker-scheduling`，不需新建任务。
- V0-07 已归档到 `.trellis/tasks/archive/2026-10/10-20-v0-07-evidence-api-acceptance/`。报告读取 API 已交付，真实模型端到端闭环仍未验收；Owner 已决定最后统一验收，见路线图 `pending-acceptance.md` 的 PA-1。不得把 V0 父任务 8/8 子任务归档写成阶段验收通过。
- `.trellis/workspace/me/journal-1.md` 的 Session 10 明确下一项 V1-01，开工需确认 V1-01/V1-02 的采集步骤边界。
- Pi/Trellis/Magpie 的已有未提交改动与本业务任务分开处理；其 `acceptance.md` 仍记录运行验证受限。

## 当前代码证据

- `backend/src/stocks/execution/config.py`：Task 只允许 `type: research`、`trigger: manual`，仅 `select_evidence` / `research_agent` 步骤；每个 Task 要求末尾 Research Agent。
- `backend/src/stocks/execution/service.py`：`create_or_get_run` 使用 PostgreSQL advisory transaction lock；已有 queued/running 活跃 Run 时直接返回；`claim_run` 可原子领取 queued Run。当前 CLI 通过 `create_claim_and_execute` 同进程创建、领取、执行。
- `backend/src/stocks/models.py`：已有每 Task 活跃 Run 条件唯一索引和运行所有权约束；TaskDefinition 仍限制 manual trigger。
- `backend/src/stocks/execution/service.py`：每个步骤独立持久化，已有步骤会复用，但失败步骤会终止当前 Run；这不等于已提供 Owner 重试或崩溃恢复入口。
- `backend/src/stocks/collection/__main__.py`：独立加载 Source，直接调用 `collect_source`；采集尚未纳入 Task/Run。
- `backend/tests/test_execution.py`：已有并发触发、所有权、模型有限重试等覆盖，实施时复用并只新增调度、Worker、恢复和采集接入的关键行为验证。

## 提议边界（待本轮用户确认）

V1-01 包含：manual / schedule 统一触发、cron 时区与 every 间隔、独立 Worker、现有研究步骤执行、现有采集服务的最小 Task 步骤适配、Run 查询与 Owner 确认旧进程已停止后的异常结束/重试。采集按 Task 独立配置，不在 Source 增设 schedule。

V1-02 保留：解析/分类/Fact/索引统一后台处理状态与自动续处理、按文档和阶段的积压重试。后续分类与 Fact 算法继续由其所属任务实现。V1-01 不提前搭完整处理链路。

理由：只对研究增加调度无法满足 V1-01 PRD 的“采集和研究按各自配置独立调度”与 AC-2；薄适配可复用现有采集幂等机制，不需增加通用 Connector DAG。

## 待细化与验证

用户确认边界后完善调度持久状态、停机期间漏触发策略、重试的快照和步骤复用语义、CLI 及数据库初始化方案，再提交最终实施规划。不得把建议当作已经授权的实施行为。

本轮只做仓库文件阅读与规划记录；没有运行 task start、SSH、测试、构建或业务实现。所有验证仍只允许在 `100.120.14.84` 的任务隔离目录执行，由 Implement 承担；此前 Pi 配置任务的 SSH 认证失败只是历史限制，不代表本任务已探测。
