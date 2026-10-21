# Worker、调度与 Run 所有权

本文件只规定**工程做法**：Worker 进程如何组织事务、Session、槽位与等待，以及代码必须遵守的所有权与重试边界。
cron/时区语义、漏触发策略、增量水位等产品契约的唯一来源是 [docs/execution.md](../../../docs/execution.md)，不在此重述。

| 主题 | 唯一归属 |
| --- | --- |
| 触发种类、窗口与增量语义、DST 与合并策略 | `docs/execution.md` |
| 远程验证环境与清理 | `testing-guidelines.md` |
| Worker/调度代码组织、Session 与所有权实现 | 本文件 |

## Scope / Trigger

- 改动 `backend/src/stocks/worker.py`、`backend/src/stocks/execution/scheduler.py`、
  `backend/src/stocks/execution/service.py` 的触发、领取、步骤落库、结束或重试路径时适用。
- API 与 Worker 是共享同一后端代码的**两个进程**，只通过 PostgreSQL 协调（`compose.yaml` 的 `worker` 服务）。
  任何"另一个进程可能在跑"的假设都必须用数据库条件写操作表达，不能用内存状态。

## Signatures

- `worker.WorkerContext.from_settings(**overrides)`：唯一构造入口，持有 `session_factory`、`settings`
  与三个配置路径（tasks/models/sources），以及可选 `model_transport`（测试注入 `httpx.MockTransport`）。
- `worker.run_once(context, *, process_token=None, now=None) -> dict`：一个调度轮 + 至多一份 Run，返回
  `{"process_token", "scheduler", "executed"}`；`--once` 与测试都用它。
- `worker.run_forever(context, *, poll_seconds, max_rounds=None)`：常驻轮询；`--rounds N` 用于有界执行。
- `worker.claim_next_run(session, *, process_token, now=None)`：`FOR UPDATE SKIP LOCKED` 领取最旧 `queued` Run。
- `scheduler.run_scheduler_round(session, tasks, agents_by_id, *, now, model_config_path) -> list[ScheduleOutcome]`。
- `service.create_or_get_run` / `end_run` / `retry_run` / `claim_run` / `execute_claimed_run` /
  `fail_claimed_run`：manual、schedule、retry 共用同一入队路径（内部 `_enqueue_run`）。
- CLI：`python -m stocks.execution {trigger,runs,show,end,retry}`、`python -m stocks.worker [--once|--rounds N]`。
  已删除的旧入口（`create_claimed_and_execute` 一类"同进程创建+领取+执行"）不得复活。

## Contracts

1. **每个执行单元一个 Session。** `execute_next_run` 用 `context.session_factory()` 新开 Session；
   `run_forever` 为每个槽位各调用一次，绝不跨 `asyncio` 任务共享 ORM Session。
2. **领取与执行分离。** 领取在一个短事务里 `with_for_update(skip_locked=True)` + `claim_run`
   （条件 `status='queued'` 的 `UPDATE ... RETURNING`）；领不到就返回 `None`，不 sleep 在事务里。
3. **调度轮的边界。** `run_scheduler_round` 每 Task 一个事务，且始终在 `pg_advisory_xact_lock(hashtextextended(task_id,0))`
   内完成"注册/判定到期 → 入队 → 推进 `next_due_at`"；单 Task 异常经 `_record_failure` 记录安全错误后继续下一个 Task。
   **到期任务若已有活跃 Run，必须在同一事务内先 `get_active_run` 命中并返回 `skipped_active_run`，
   不得先解析模型路由**（避免"任务繁忙"被记成配置失败，也避免无谓读取凭据）。
4. **配置失败不阻塞执行。** `schedule_round` 捕获 `ExecutionConfigError` 只返回一个 `configuration_failed`
   outcome；采集 Run 的 `agents` 为空 → `model_snapshot` 为空 → 不读 `config/models.yaml`。
5. **所有权是两层，不能混写为"绝对阻止派生写入"。**
   - 严格 fence：写 StepResult/Analysis/Report 前 `_assert_run_ownership`（`FOR UPDATE` 校验
     `status='running' AND process_token=:token`），收尾 `_finish_run`（同条件 `UPDATE ... RETURNING`，
     无匹配即 `RUN_NOT_OWNED` 回滚）。Owner `end_run` 后旧执行**不能**写成功步骤或改写最终状态。
   - 事前检查：`_ensure_run_still_owned` 是无锁读，只在每一步与每个 Source **开始前**执行；
     `select_evidence`/`collect_source` 已在自身事务内提交的派生资料（Document、Evidence Package）
     无法回滚，必须按统一入库规则保留为真实证据。日志与文档都不得把它们写成"被回滚"。
6. **空闲等待有界，不忙轮询。** `run_forever` 维持 `slots = min(max_concurrent_runs, 16)` 个**常驻**槽位：
   执行过 Run 的槽位立即再领取；领不到才 `_idle_wait(stop, wakeup, poll_seconds)`，可被"本轮已入队 Run"提前唤醒；
   连续意外失败按 `min(poll_seconds * 2**n, poll_seconds + MAX_BACKOFF_EXTRA_SECONDS)` 退避。
   调度循环独立于槽位，长 Run 不得推迟到期判定。`stop` 置位后每槽最多再领取一次，`finally` 仍 `gather` 全部槽位并
   `executor.shutdown(wait=True)`：Worker 永不抛下自己已拥有的 Run。
7. **阻塞调用用 Worker 自有线程池。** `_call_in_executor(executor, fn, ...)` +
   `ThreadPoolExecutor(max_workers=slots + 1)`。`asyncio.to_thread` 走事件循环默认执行器
   （`ThreadPoolExecutor`，Python 3.12 默认大小约 `min(32, cpu_count() + 4)`），当配置并发大于该默认值时长 Run
   会占满线程并饿死调度轮，因此不使用。
8. **重试复制快照与已完成工作，不新建历史表。** `retry_run` 只接受 `failed|cancelled`；复制**被结束 Run 自己的**
   `config_snapshot`/`model_snapshot`/`prompt_snapshot`（`config_path=None`、`upsert_definition=False`），
   只复制**连续成功**且非 `research_agent` 的步骤；Evidence Package 为新 Run 重新建一份，并把
   `select_evidence` 输出里的 `evidence_package_id` 改指新包（`_run_agent` 会校验 `package.run_id == run.id`）；
   `skipped` 步骤不复制而重判条件；`retry_of_run_id` 记录关联；原 Run 的步骤、ModelCall 与 error 原位保留。
9. **Run 的关闭状态由表约束表达。** `ck_task_runs_closed_owner` 禁止 `queued|succeeded|failed|cancelled` 携带
   `process_token`，`ck_task_runs_running_owner` 要求 `running` 必有 token；不要在代码里另加标记字段。
   Owner 结束固定写 `status='cancelled'` + `error.code='RUN_ENDED_BY_OWNER'` 并清空 token。
10. **不引入租约、心跳或自动接管。** Worker 不检测死进程；`end` 的前提是 Owner 已确认旧进程停止，
    CLI 强制 `--confirm-stopped`（缺失即 exit 2 + `OWNER_CONFIRMATION_REQUIRED`），不提供未认证写 API。

## Validation & Error Matrix

| 条件 | 预期处理 |
| --- | --- |
| `config/tasks.yaml` 缺失或非法 | `schedule_round` 返回 `configuration_failed`，已 `queued` 的 Run 仍被执行 |
| 某 Task 引用未知 Agent | 只影响该 Task 自己的 outcome（`failed` + 安全错误），其他 Task 继续 |
| 到期且已有活跃 Run | `skipped_active_run`，仅推进 `next_due_at` 与 `last_attempt_at`，`last_error` 置空，不读模型配置 |
| Task disabled / 移除 / 改回 manual | `_retire_stale_schedules` 删除残留 `task_schedules` 行，仍登记当前定义以便读取历史 Run |
| 领取到 Run 但已被 Owner 结束 | `claim_run`/`execute_claimed_run` 返回 `RUN_NOT_CLAIMED`/`RUN_NOT_OWNED`，`execute_next_run` 汇总 `status="not_owned"`，不改写该 Run |
| 步骤抛出未分类异常 | 只写该 Run 的 `failed` StepResult 与 error，Worker 存活；意外逃逸异常由 `_fail_run` 落一条安全错误 |
| `--once` 中 Run 失败或调度 `failed` | CLI 退出码 `1`（可观测），空队列正常返回 `0` |
| `poll_seconds`/并发越界 | CLI exit 2 + `POLL_SECONDS_INVALID`/`CONCURRENCY_INVALID`/`ROUNDS_INVALID`，不静默夹取 |

## Good / Base / Bad Cases

- Good：两个槽位（`WORKER_MAX_CONCURRENT_RUNS=2`），15 分钟采集与 8 分钟研究同时存在；采集槽位执行完立即领取下一份，
  调度轮仍每 `poll_seconds` 判定一次，研究长 Run 不推迟到期。
- Base：`worker --once` 在空队列 → `scheduler: []`、`executed: null`、退出码 0；不产生任何数据库写入负载之外的循环。
- Bad：每轮重建 `create_task` 并 `asyncio.wait(..., timeout=poll_seconds)`（默认 `ALL_COMPLETED`）补齐槽位——空闲槽
  立刻返回会造成每间隔热循环，且一个空槽要等本轮最慢 Run；用 `asyncio.to_thread` 承担全部阻塞调用；
  为"防止结束后写资料"去回滚已入库的原文；用内存 `last_run` 做调度去重。

## Tests Required

- `tests/test_scheduler.py`：cron 时区、DST 跳时/重叠（**按真实 tzdata 推导：2026-11-01 纽约重复的是 01:30，
  01:30 EDT = 05:30 UTC、01:30 EST = 06:30 UTC；秋季当天 02:30 只存在一次**）、`every` 固定锚点与合并、
  首次注册不触发、指纹变更重锚定、disabled 退役、并发调度轮只入队一份、跨 Task 失败隔离。
- `run_forever` 用例**不依赖数据库**：stub `schedule_round`/`execute_next_run`，断言领取次数与耗时上界
  （拒绝热循环回归）、空闲槽被唤醒、在途 Run 不阻塞第二轮调度、连续失败之间存在退避间隔。
- 断言"调度不解析模型路由"用 `monkeypatch.setattr("stocks.execution.service.load_chat_routing", ...)`
  注入失败；注意 `pytest.fail` 抛的是 `Failed`（非 `Exception` 子类），若被测路径有宽泛 `except Exception` 隔离，
  需确认断言真的传播到测试，不要写成会被吞掉的形状。
- 所有权/恢复边界：`test_end_run_keeps_progress_and_blocks_stale_writes`、
  `test_retry_requires_ended_run_and_reuses_snapshot`、`test_retry_reuses_previous_steps_but_reruns_the_agent`、
  `test_claim_next_run_is_atomic_across_workers`。
- 真实进程中断验收只在测试服务器用 `backend/tests/ac3_drill.py`（不匹配 `test_*.py`，不被 pytest 收集）：
  真实 `stocks.execution trigger` 入队、真实子进程 `python -m stocks.worker` 领取、进程内 mock 卡住模型调用后
  `SIGKILL`，再走 `end --confirm-stopped` + `retry`。脚本 `reset()` 只清本演练 Task 自己的 Run 及关联行，
  且该 Task 仍有活跃 Run 时直接拒绝，不得改回整表删除。

## Wrong vs Correct

```python
# Wrong：空闲槽位立即返回 → 每轮重建任务并重新读配置、重新领取
while True:
    tasks = [asyncio.create_task(claim_and_execute()) for _ in range(slots)]
    await asyncio.wait(tasks, timeout=poll_seconds)   # ALL_COMPLETED

# Wrong：把所有权检查写成"能阻止一切派生写入"，据此回滚已入库原文
# Wrong：阻塞步骤走事件循环默认线程池
await asyncio.to_thread(execute_next_run, context, ...)

# Correct：常驻槽位 + 自有线程池 + 空闲有界等待、可被入队唤醒
executed = await _call_in_executor(executor, _claim_and_execute, context, token)
if executed is None:
    await _idle_wait(stop, wakeup, poll_seconds)
else:
    continue  # 立刻领取下一份
```

```python
# Wrong：为"跳过繁忙任务"先去解析模型路由，靠唯一索引冲突再判 skipped
run_id = _enqueue_run(session, task, agents, config_path=model_config_path, ...)

# Correct：同一 advisory lock 事务内先查活跃 Run，命中就只推进 due
if get_active_run(session, task.id) is not None:
    schedule.next_due_at = due
    schedule.last_attempt_at = now
    schedule.last_error = None
    session.commit()
    return ScheduleOutcome(task_id=task.id, action="skipped_active_run", next_due_at=due)
```
