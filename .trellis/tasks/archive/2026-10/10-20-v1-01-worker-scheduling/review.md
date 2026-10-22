# V1-01 Check 审查结论（2026-10-21 最终）

范围：本任务全部 diff —— `execution/config.py`、`execution/service.py`、`execution/scheduler.py`、
`worker.py`、`execution/__init__.py`、`execution/__main__.py`、`models.py`、`0001_initial.py`、
`settings.py`、`api.py`、`pyproject.toml`、`compose.yaml`、`config/tasks.yaml`、`.env.example`、
`docs/execution.md`、`docs/data.md`、`docs/validation.md`、`docs/quickstart.md`、
`tests/test_scheduler.py`、`tests/test_execution.py`、`tests/ac3_drill.py`。
既有 Pi/Trellis 平台改动（`.pi/`、`.agents/`、`.trellis/tasks/10-21-pi-trellis-magpie/`、
`.trellis/model-routing.md`、`check.jsonl`/`implement.jsonl`/`task.json`）不属于本任务，未审查、未改动。

**执行方式：人工逐行审查 + 读取 Implement 在测试服务器 `100.120.14.84` 隔离目录的实际运行结果**
（见 [`research/remote-verification.md`](research/remote-verification.md)：新库 `upgrade head`/`check` 通过、
`test_scheduler.py`+`test_execution.py` 90 passed、`test_research.py`+`test_report_api.py`+`test_collection.py`
52 passed、Compose `config` 通过、容器内 9 阶段 CLI smoke、AC-3 真实子进程演练 18/18）。
本轮 Check 自身未在任何机器运行 pytest/编译/类型/格式/Alembic/Docker；只有第四节列出的脚本改动
需要在远程重跑一次。

> 事实纠正（相对首轮）：首轮在 SSH 认证失败的前提下写作"全部未运行"，且把本机 AST/静态扫描讨论为
> 验收口径。认证在第二批前已由 Owner 提供密码解除，**远程结果已取代那些结论**；Implement 早期确实做过
> 本机 AST 扫描，但那不属于仓库唯一验证入口，不作为验收证据，现也不再引用。

## 1. 已修复的具体问题

### 1.1 `worker.run_forever` 空闲/混合负载下的错误等待（高）

原实现每轮 `await asyncio.wait(in_flight, timeout=poll_seconds)`（默认 `return_when=ALL_COMPLETED`），
而槽位领不到 Run 就立刻返回。后果：

- **空队列 = 忙轮询**：每个 `poll_seconds` 边界所有槽位同时返回 `None`，`while len(in_flight) < slots`
  立即重建同样数量的新任务，于是每轮都重新读 `config/tasks.yaml` 并跑一次完整调度 + 每个槽位一次
  `FOR UPDATE SKIP LOCKED` 领取，负载随轮数线性增长且无退避。
- **混合负载**：`ALL_COMPLETED` 使一个空槽位也要等本轮最慢的 Run（或整个 `poll_seconds`）才被补齐，
  短采集 Run 不能马上接手队列里的下一份 Run。
- **新建空槽位热循环**：每轮 `create_task` 而不是复用槽位。

修复（`backend/src/stocks/worker.py`）：调度循环与执行槽位解耦（主循环只做"一次调度 + 等待
`poll_seconds`"，途中长 Run 不推迟到期判定）；固定数量**常驻槽位**（`_run_slot`）：`executed is not None`
立即再领取，领不到才 `_idle_wait(stop, wakeup, poll_seconds)`，可被"本轮已入队 Run"提前唤醒；
连续意外失败按 `min(poll_seconds * 2**n, poll_seconds + MAX_BACKOFF_EXTRA_SECONDS)` 退避，成功领取即复位；
`stop` 置位后每槽最多再领取一次，保证 `--rounds N` 最后一轮入队的 Run 被执行；`finally` 仍 `gather` 全部
槽位并 `executor.shutdown(wait=True)`。`poll_seconds` 上下限提为 `MIN/MAX_POLL_SECONDS`，`run_forever` 与 CLI 共用。
新增 4 个行为用例（`test_run_forever_*`，stub 掉调度与领取，不依赖数据库）。

同轮把阻塞步骤从 `asyncio.to_thread` 改为 Worker 自有 `ThreadPoolExecutor(max_workers=slots + 1)`
（`_call_in_executor`）。**风险准确表述**：`asyncio.to_thread` 使用事件循环默认执行器，它是
`ThreadPoolExecutor`，Python 3.12 默认大小约 `min(32, cpu_count() + 4)`（并非"单核只有 1 线程"）；
问题在于 `WORKER_MAX_CONCURRENT_RUNS` 可配到 16，**配置并发可能大于默认池大小**（例如 4 核机器默认 8），
此时满负荷的长 Run 会占满线程并饿死调度轮。用自有池使池大小与槽位数显式对齐。

顺带修正 `_fail_run`：原 `except Exception` 分支在调用前构造 `error` 字典，若 `exc.to_dict()` 自身抛错会
丢掉失败落库；现由 `_fail_run` 统一构造、写入并返回同一份安全错误。

### 1.2 DST 测试期望算错（高，测试正确性）

原 `test_cron_skips_dst_gap_and_fires_repeated_time_once` 把 America/New_York **2026-11-01 的 02:30** 当成
重复时刻。实际当天 02:30 **只出现一次**：EDT 在本地 02:00 结束（01:59:59 EDT → 01:00:00 EST），所以当天不存在
"02:30 EDT"，只有 02:30 EST = **07:30 UTC**；重复的墙钟时间是 **01:00–01:59**（01:30 EDT = 05:30 UTC、
01:30 EST = 06:30 UTC）。因此原来的 `06:30` / 次日 `07:30` 断言并没有验证"重叠时刻只触发一次"。
（首轮本文曾把当天 02:30 写成 EDT/EST 两个瞬间，同样是错的，此处一并纠正。）

修复：拆成三个各自成立的用例，期望值由 tzdata 规则推导、并已在远程 90 passed 中实测确认：

- `test_cron_skips_spring_forward_gap`：`30 2 * * *`，起点 2026-03-08 05:00 UTC；02:30 不存在，
  直接得到 2026-03-09 06:30 UTC（次日 02:30 EDT）。
- `test_cron_fires_repeated_wall_clock_time_only_once`：`30 1 * * *`，起点 2026-11-01 04:00 UTC →
  **05:30 UTC**（第一次出现）；再推进 → 2026-11-02 06:30 UTC（01:30 EST）；从 `fold=1` 的 01:30
  （= 06:30 UTC，用原始 UTC 瞬间断言，不比较 naive 时间）起算同样得到 2026-11-02 06:30 UTC，
  即重叠小时不会跑两遍。
- `test_cron_overlap_day_fires_each_distinct_instant_once`：`0,30 1 * * *`，朴素实现会得到四个瞬间
  （05:00/05:30/06:00/06:30 UTC）；断言只得到 05:00、05:30 与次日 06:00、06:30。

### 1.3 调度到期未先判活跃 Run（中，契约与凭据暴露）

`_run_task_schedule` 原先在持有 Task advisory lock 的事务里**直接**调 `_enqueue_run`，
`_enqueue_run` → `_model_snapshot` → `load_chat_routing(...)` 会解析研究模型路由与环境凭据；已有活跃 Run 时
靠条件唯一索引 `on_conflict_do_nothing` 返回 `None` 才判为 `skipped_active_run`。这与 `docs/execution.md`
对手动触发的要求（"不读取 Task 配置、不查询模型凭据"）不一致，并且当 `models.yaml` 缺失/损坏时会先进入
`except` → `_record_failure`，把"任务繁忙"记成 `action=failed` + `last_error`，掩盖真实原因。

修复：到期后先 `get_active_run(session, task.id)`（同一 advisory lock、同一事务）；命中则写回
`next_due_at = due`、`last_attempt_at`、清空 `last_error` 并提交，返回 `skipped_active_run`，完全不触碰模型配置；
`_enqueue_run` 的索引冲突分支保留为最终兜底。新增
`test_due_task_with_active_run_skips_without_loading_model_routes`：把
`stocks.execution.service.load_chat_routing` patch 成失败，断言 `brief` 为 `skipped_active_run`、无 error、
无 run_id、due 已推进、`last_attempt_at` 已记录、`last_fired_at is None`。该用例用 `pytest.fail` 作为注入的失败，
其意图（调度路径真的没去解析路由）已由远程通过结果证实；首轮本文把它解释为"因为不是 `Exception` 子类所以
一定不被吞"，这层实现细节不该当作结论依据，已删除。

### 1.4 结束 Run 后的派生写入承诺过强（中，文档契约）

原 `docs/execution.md` 与 acceptance 写"Owner 结束后旧执行不能再产生任何派生资料"。这不成立：
每一步前的 `_ensure_run_still_owned` 是无锁读，只能阻止**新**步骤/新 Source 开始；已经开始的
`select_evidence`（自身 `session.commit()`）与 `collect_source`（逐批提交 Document）已提交的数据无法回滚，
按数据采集底线也必须保留为真实证据。

修复为两层清晰语义（`docs/execution.md`、`docs/quickstart.md`、design.md、acceptance.md 与 `service.py` 注释同步）：

- **严格 fence**：`_assert_run_ownership`（写 StepResult/Analysis/Report 前 `FOR UPDATE`）+ `_finish_run`
  （条件 `UPDATE ... RETURNING`，不匹配则 `RUN_NOT_OWNED` 回滚）—— Owner 结束后旧执行不能写成功的步骤结果、
  不能改写 Run 最终状态。
- **事前检查**：只阻止新步骤/新 Source 启动；已提交派生资料保留，但该 Run 不会因此获得成功步骤或报告。
- **Owner 确认旧进程已停止是前提**（CLI 强制 `--confirm-stopped`，无自动接管/租约）。

AC-3 真实演练正好验证了这层边界：`SIGKILL` 前由步骤自身提交的 Evidence Package 仍在，被结束的 Run 没有成功步骤。

### 1.5 `ac3_drill.py` 的 `reset()` 清理范围过宽（本轮收尾修复）

演练脚本随仓库保留，其 `reset()` 原先对 `Report / Analysis / EvidencePackage / ModelCall / TaskStepResult /
TaskRun` **整表删除**。在该任务专用测试库上没有实际危害，但脚本一旦被复用到别的库就会抹掉其他 Task 的历史
Run 与失败证据，违反"不删除历史证据"的底线。

最小修复（不改产品代码、不建任何平台）：只删除 `task_id == TASK_ID`（`saved_evidence_brief`）的 Run 及其关联行；
若该 Task 仍存在 `queued`/`running` Run，直接 `SystemExit` 并提示先用 `stocks.execution end --confirm-stopped`，
避免误删活跃数据；`reset` 的 CLI 帮助文本写明所属 Task。返回结构增加 `task_id`/`runs_removed` 便于核对。
**该脚本改动已在远程重跑确认**：`drive` 退出码 `0`、`result=passed`、18/18（见第 4 节与
remote-verification.md 第 9 节）。

### 1.6 一致性与最小化清理

- `_retire_stale_schedules` 内联的 Agent 解析式与 `_task_agents` 语义不一致（会跳过未知 Agent，而调度路径会抛错）；
  统一改用 `_task_agents`，失败仍被 per-Task try/except 隔离。
- design.md 写"Worker 并发默认 1"，实际 `settings.py`/`.env.example`/`docs/execution.md` 都是默认 2；以代码为准修正 design.md。
- `run_once(run_scheduler=...)` 无任何调用方，删除该开关（不留兼容分支）。
- acceptance 的 AC-3 测试点补上 `test_retry_reuses_previous_steps_but_reruns_the_agent`。

## 2. 复查通过、未改动的部分

- **retry 快照与完成步骤复用**：`retry_run` 只接受 `failed|cancelled`；复制**被结束 Run 自己的**
  `config_snapshot`/`model_snapshot`/`prompt_snapshot`（`config_path=None`、`upsert_definition=False`），不用新任务
  定义替换；`_copy_completed_work` 只复制**连续成功**且非 `research_agent` 的步骤，把 `evidence_package_id` 改指
  新包（`uq_evidence_packages_run` 要求每 Run 一包；`_run_agent` 会校验 `package.run_id == run.id`，因此这步是
  必需的而不是装饰）；`skipped` 步骤不复制而重判；原 Run 步骤/ModelCall/error 原位保留。与 `docs/execution.md` 一致，
  并由 AC-3 演练证实（retry 只发 1 次 `chat/completions`、无 `embeddings`）。
- **模型初始化隔离**：采集 Run 的 `agents` 为空 → `_model_snapshot` 返回空快照，`_run_collection` 只读
  `config/sources.yaml`；研究配置损坏不影响采集入队与执行（AC-2 的结构性保证）。
- **初始化一致性**：`models.py` ↔ `0001_initial.py` 人工比对结论已被远程全新空库 `alembic upgrade head` +
  `alembic check`（退出码 0/0，`No new upgrade operations detected.`）确认，包括 `task_schedules`、
  `ck_task_definitions_trigger`、`triggered_by`/`retry_of_run_id`、`ck_task_runs_closed_owner`、`ix_task_schedules_due`、
  `ix_task_runs_status_created`；具名 `UniqueConstraint`/`CheckConstraint`/`Index` 未退化成列级 `unique=True`。
- **CLI/文档同步**：`stocks.execution` 子命令与 `docs/quickstart.md` 逐条对应，并已在容器内真实执行
  （`trigger/show/end/retry/runs` + `worker --once/--rounds 1`）；`end` 无 `--confirm-stopped` 返回
  `OWNER_CONFIRMATION_REQUIRED`/exit 2；`runs --limit` 边界 1–200。旧 `create_claim_and_execute`、
  `refresh_active_run` 与单命令入口已删除，`__init__.py` 导出同步，仓库内无残留引用（历史
  `research/kickoff-review.md` 记录旧基线，属事实记录，不改）。
- `api.py` 只读输出新增 `triggered_by`/`retry_of_run_id`；报告证据路径的 `run_not_succeeded` 语义不受 `cancelled`
  影响（非 `succeeded` 一律记为缺口），无需改动。

## 3. 未修复但明确延后（不属于本任务）

- 后台解析/分类/Fact/向量的统一处理状态与积压重试归 V1-02；可靠变化水位归 V1-05；受保护 Web 控制台未实现。
- `WORKER_MAX_CONCURRENT_RUNS` 除 env/CLI 外无额外运行时调参入口，够用即止，不提前抽象。
- PA-1（真实模型端到端闭环 + 真实外网采集）与 PA-2（V0-03 解析器换行缺陷）状态不变，本任务未推进。
- 仓库 `backend/Dockerfile` 在无 pip 镜像时 180s 内构建未完成，属测试机网络条件，**未改仓库 Dockerfile**；
  构建时长未做无镜像复测。
- 工程知识已按第 4 节写入 spec，不再另立 TODO。

## 4. 本轮 Check 的产出与重跑结果

已写文件：

- 新增 [`.trellis/spec/backend/worker-guidelines.md`](../../../../spec/backend/worker-guidelines.md)（Worker/调度的
  Session、领取、调度轮边界、两层所有权 fence、空闲等待与退避、自有线程池、重试复制规则、错误矩阵、
  Good/Base/Bad、Tests Required、Wrong/Correct），产品 cron/漏触发语义只链接 `docs/execution.md`，不复制第二套设计；
  已加入 `.trellis/spec/backend/index.md` 导航表。
- `.trellis/spec/backend/testing-guidelines.md` 补充：密码认证只能经会话临时凭据（绝不入库）、
  `BatchMode=yes` 的 `Permission denied` 表示"未提供可用认证方式"而非"服务器不可达"、缺依赖须第一时间反馈，
  并在错误矩阵中拆分认证失败与网络不可达两行。
- `backend/tests/ac3_drill.py` 的 `reset()` 收窄（1.5）。
- `acceptance.md` 重写为当前唯一结论；`implement.md` 收尾项状态同步。

**需 Implement 在测试服务器重跑（最小范围，不在本机执行）：**

```bash
cd /tmp/stocks-v1-01-worker-scheduling-20261021/backend
export DATABASE_URL="postgresql+psycopg://stocks:<test-only>@127.0.0.1:55438/stocks"
export MODEL_API_BASE_URL=http://127.0.0.1:18080/v1 MODEL_API_KEY=<test-only> \
       MODEL_NAME=<test-only> EMBEDDING_MODEL_NAME=<test-only>
.venv/bin/python tests/ac3_drill.py drive --workdir /tmp/ac3-drill   # 预期 result=passed, 18/18
```

**重跑结果（Implement 已执行）：通过。** 退出码 `0`、`result=passed`、`checks_total=18 passed=18`、9s；
`SIGKILL` 子进程退出码 `-9`、retry Worker `0`、retry Worker 模型请求仅 `['/v1/chat/completions']`（无
`/embeddings`）；`reset` 返回 `runs_removed=0`（复跑前只读确认该库无任何活跃 Run）。明细见
[`research/remote-verification.md`](research/remote-verification.md) 第 9 节。

只剩这一条曾待补；本轮代码/文档/spec 改动未触碰任何产品源码，142 项 pytest、Compose、CLI smoke 均已在
同一目录通过，按指令未重跑。资源清理已完成（同文档第 10 节），本任务远程资源已全部删除。
