# V1-01 技术方案

## 已批准范围与现状

用户于 2026-10-21 在范围说明后要求“开始实施”。本项交付独立调度/Worker、现有采集服务的最小 Task 接入和 Run 人工恢复；V1-02 保留解析、分类、Fact、索引的统一后台处理与积压恢复。现状证据见 `research/kickoff-review.md`。

## 模块与执行路径

- 扩展 `backend/src/stocks/execution/config.py`，支持 manual 和 schedule（cron + IANA timezone 或 every 固定正间隔，两者互斥），以及 collection Task 的最小采集步骤；保留严格配置校验。不保留被替代配置的兼容分支，同步示例与测试。
- 采集步骤直接复用 `stocks.collection.service.collect_source`、Source 配置与既有 Repository 幂等，不重写获取/持久化链路；采集 Run 不加载无关研究模型凭据。Source 无 schedule。
- `execution/service.py` 保留统一触发入口和 PostgreSQL 同 Task 活跃 Run 唯一约束，将触发入队与执行解耦。manual、schedule、retry 共用任务占用检查；重复手动触发返回现有 Run，schedule 占用时跳过。
- 新增最小调度模块和 `stocks.worker` CLI；Worker 与 API 分进程。数据库原子领取 queued Run，多个 Worker 不重复执行同一 Run；不同 Task 不因某个任务失败或中断而失去执行机会。每个执行上下文独立 Session，避免共享线程 Session。
- Worker 支持单轮执行以便测试及连续轮询以便部署；通过多 Worker/有界执行并发使研究长调用不阻塞采集。所有等待、外部调用和重试有界，停机不自动抢占 running Run。

## 调度与持久状态

- 调度状态保存在同一 PostgreSQL 的 TaskDefinition 或紧邻的最小记录中，至少可确定 next_due 与调度配置指纹。不得用纯内存 last_run 作为唯一去重机制。
- 并发 scheduler 对同一 Task 的到期判定、触发和 next_due 更新在同一个任务级串行事务中完成；已存在的任务 advisory lock 与活跃 Run 索引继续构成边界。
- cron 明确验证时区，采用维护良好的 cron 解析库而非自写完整 cron 算法；每次由带时区时刻计算下一次未来触发并存 UTC，测试夏令时跳时/重复时刻。every 使用固定间隔与持久 due 锚点，不按任务完成时间漂移。
- 停机或繁忙错过多个触发点时合并为至多一次到期处理，推进到下一未来触发点，不创建历史 Run 风暴；任务活跃时跳过本次到期，推进 due。首次注册/调度变更的锚点和行为写入唯一归属 `docs/execution.md` 并测试。
- disabled Task 不触发；配置移除/禁用不能因数据库残留定义继续调度。单任务配置/模型初始化失败输出安全错误，继续处理其他 Task，不终止整个调度轮次。

## 运行快照、恢复与证据

- Run 创建时固定任务、Agent、模型路由等实际输入快照，执行 queued Run 时不静默读取新任务定义替换其快照；研究 as_of 按实际开始执行时间取值，而非队列创建时间。可靠增量水位仍留给 V1-05/V1-06。
- Worker 可保存结构化失败信息与已完成步骤；硬中断只能留下最后持久状态，不伪称已检测到死进程。运行列表/详情展示状态、步骤、错误及时间，支持 Owner 诊断。
- Owner 必须显式确认旧进程已经停止，才能结束异常 running Run；结束清除运行占用并保留原有进度和错误。恢复只通过本地受控 CLI/服务，不新增未认证写 API。
- 手动 retry 复用已完成步骤及既有 Evidence Package/原件，不重复制造成功资料；保存此前失败、调用记录和恢复关联，不能删除失败证据来假装首次执行。实现以现有模型的最小扩展确定重试历史/关联形式；禁止无界自动重跑整个 Run。外部模型沿用当前有界重试，采集沿用现有有界请求机制。
- 所有结束/步骤落库操作应核对 Run 状态与所有权，避免旧执行在人工结束后误写结果。Owner 确认停机是恢复前提，不引入租约、自动接管或分布式调度平台。

## 实施细化（2026-10-21，Implement 落定）

以下为设计未唯一确定、由实现选定的最小方案，已同步到 `docs/execution.md` 与 `docs/quickstart.md`。

- **cron 库**：`croniter>=2,<3`。仅在任务时区墙上时间求下一触发点，再转 UTC；DST 跳时（如 2026-03-08 02:30 America/New_York）由 `_resolve_local_time` 返回 `None` 跳过； fall-back 重复时刻（如 2026-11-01 01:30）只取第一次出现的 UTC 瞬间（`fold=0`），后续推进不会命中同一墙钟时间的第二次出现。`every` 不依赖库，解析 `s`/`m`/`h`/`d` 为秒。
- **调度记录**：新增最小表 `task_schedules`（`task_id` 主键 + FK CASCADE），保存 `schedule_fingerprint`（`kind/cron/timezone/every_seconds` 的 SHA-256）、`schedule_snapshot`、`next_due_at`、`last_fired_at`、`last_attempt_at`、`last_error`。不新增调度历史表。
  - 注册/重锚定：无行或指纹不同 → 取 `now` 之后的下一个触发点，首次不触发；未到期 → `not_due`；到期 → 在**同一事务**内入队并推进 due。
  - 到期且已有活跃 Run → 在同一 advisory lock 事务内先检查活跃 Run，命中则仅推进 due 并记 `skipped_active_run`（不读取模型配置、不记录配置失败）。
  - disabled / 移除 / 改回 manual：每轮开头删除不在当前调度集合内的行（`_retire_stale_schedules`），并为这些 Task 保留当前定义快照以便读取历史 Run。
- **重试历史与关联**：不新建历史表。`task_runs` 增加 `triggered_by`（`manual|schedule|retry`）与自引用 `retry_of_run_id`；重试新建 Run 并复制结束时 Run 的配置/模型/提示词快照、**已成功**步骤与新复制的 Evidence Package（`select_evidence` 输出中的 `evidence_package_id` 改指向新包）。`skipped` 步骤重新判断，不复制。失败 Run 的步骤、ModelCall 与 error 原位保留。
- **结束状态**：复用 Run `status` 增加 `cancelled`，error 固定为 `RUN_ENDED_BY_OWNER`；不新增 `aborted` 列或标记字段。CLI 强制 `--confirm-stopped`，不提供未认证写 API。
- **所有权**：写入 StepResult/Analysis/Report 前 `SELECT ... FOR UPDATE` 校验 `status='running' AND process_token=:token`；Run 收尾用同条件的 `UPDATE ... RETURNING`，无匹配则 `RUN_NOT_OWNED` 并回滚，旧执行不会覆盖 Owner 结束后的状态。`select_evidence`/`collect_sources` 会在步骤内提交自己的派生数据，因此每一步及每一个 Source 开始前再做一次无锁事前检查；这层检查只能阻止新步骤/新 Source 开始，不能阻止已经开始的内部 commit（已入库原文仍作为证据保留），因此结束后的严格 fence 仅适用于步骤结果与最终状态。
- **Worker 并发**：`WORKER_MAX_CONCURRENT_RUNS`（1–16，默认 2）；`run_forever` 同时维护固定数量的**常驻执行槽位**（每个 Run 独立 Session），而不是每轮重建任务：刚执行完 Run 的槽位立即领取下一份，空队列的槽位按 `poll_seconds` 等待并可被“本轮已入队 Run”提前唤醒，连续意外失败按指数退避（上限 `poll_seconds` + 60s）；到期调度循环独立于槽位，因此途中的长 Run 不阻塞 scheduler。`stop` 置位后每个槽位最多再领取一次（拿到 Run 就执行，空则退出），保证 `--rounds N` 最后一轮入队的 Run 被执行同时不形成无限追赶。所有阻塞步骤（调度轮与领取/执行）跑在 Worker 自有的 `slots + 1` 线程池，不用事件循环默认执行器（其大小按 CPU 核数，小于并发上限时会被长 Run 占满而饿死调度）。`FOR UPDATE SKIP LOCKED` 保证不重复领取。
- **CLI 形态**：`python -m stocks.execution {trigger,runs,show,end,retry}`；`python -m stocks.worker [--once|--rounds N]`。旧的单命令创建+领取+执行入口与 `create_claim_and_execute`、`refresh_active_run` 直接删除。
- **采集 Run 范围**：`run.scope = {"source_ids": [...]}`；`objective` 固定为 Task name（Task 不提供时）。采集 Run 不加载任何模型路由。

## 数据库、部署与回退

直接修改 `models.py` 和现有初始化建表定义，不新增增量迁移。新空库 upgrade head + alembic check 必须一致。Compose 增加 Worker 服务并复用镜像、config 与环境；API 保持本机绑定。任务 CLI 触发入队、状态、结束、重试，具体命令同步 quickstart。

本地只编辑/打包，所有运行验证均由子 Agent 在 `100.120.14.84` 的本任务隔离目录执行；SSH 失败则记录未验证并继续静态实现/审查，不回退本机。回退代码不会删除既有证据；开发库按当前结构重建。真实模型闭环仍按 PA-1 留待统一验收。
