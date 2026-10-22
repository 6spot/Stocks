# V1-01 独立 Worker 定时调度与 Run 恢复

## 目标与用户价值

采集和研究按各自配置独立调度，失败可定位并由 Owner 恢复。

## 背景与依据

阶段：V1。归属契约：`docs/execution.md`, `docs/data.md`, `docs/validation.md`。代码现状见[基线](../../../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V0-07（已归档）](../10-20-v0-07-evidence-api-acceptance/prd.md)

依赖是实施顺序，不由 Trellis 父子关系自动执行。V0-07 阅读 API 已交付；真实模型闭环按 Owner 决定延后至统一验收，仍见[待验收清单 PA-1](../../../10-20-stocks-roadmap/pending-acceptance.md)，不视为通过。

## 当前事实与已确认边界

2026-10-21 开工复核：现有执行服务已具备活跃 Run 去重、原子领取与步骤结果持久化；配置只支持手动研究，采集仍由独立 CLI 调用，没有调度器、独立 Worker 或 Owner 恢复入口。证据见[开工复核](research/kickoff-review.md)。

用户于 2026-10-21 在范围说明后明确要求“开始实施”：本项增加现有采集服务的最小 Task 步骤适配，使采集与研究均可独立定时运行；解析、分类、Fact、索引的统一后台处理状态与积压恢复继续归 V1-02。

## 范围与要求

- API 与 Worker 分进程，PostgreSQL 保存待执行 Run 和原子占用；cron 指定时区，every 固定间隔。
- 手动、定时和重试共用同一触发入口，同 Task 最多一个待执行/运行 Run；不同任务独立执行。
- Source 不另设 schedule；处理一百篇文章不逐篇触发研究。
- 有界重试和步骤幂等；Worker 中断保留进度/错误，Owner 确认旧执行已停后可结束异常 Run 并重试；不自动抢占。

## 验收条件

- [x] AC-1: 同时手动/定时触发只产生一个活跃 Run，按时区/间隔触发符合配置。（远程 90 passed + 容器内真实 CLI/Worker smoke）
- [x] AC-2: 研究模型失败不阻止采集，采集故障不阻止研究读取存量。（远程对应用例通过 + `test_collection.py` 38 项回归）
- [x] AC-3: 模拟 Worker 中断可看到错误和已完成步骤，Owner 结束异常 Run 后能恢复且不重复制造成功资料。
  （真实子进程 `SIGKILL` + `end`/`retry` 演练 18/18；收尾收窄 `reset()` 后的最终脚本已远程重跑 `drive`
  得 `result=passed`，退出码 0）

## 不在范围内

自动故障接管、复杂 DAG、动态事件研究触发、按证券 Fan-out。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。实施授权已取得，按 design.md / implement.md 启动本任务；所有实际验证仅在指定测试服务器的隔离任务目录执行。
