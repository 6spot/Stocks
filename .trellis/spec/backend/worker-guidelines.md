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
- 改动 `backend/src/stocks/processing/` 的阶段服务、`indexing/` 或 Repository 的阶段结果事务时同样适用；
  文档待办与 TaskRun 各用自己的状态和 token，不以进程内标记代替持久所有权。
- API 与 Worker 是共享同一后端代码的**两个进程**，只通过 PostgreSQL 协调（`compose.yaml` 的 `worker` 服务）。
  任何"另一个进程可能在跑"的假设都必须用数据库条件写操作表达，不能用内存状态。

## Signatures

- `worker.WorkerContext.from_settings(**overrides)`：唯一构造入口，持有 `session_factory`、`settings`
  与三个配置路径（tasks/models/sources），以及可选 `model_transport`（测试注入 `httpx.MockTransport`）。
- `worker.run_once(context, *, process_token=None, now=None) -> dict`：一个调度轮 + 至多一份 Run + 至多一个文档阶段，返回
  `{"process_token", "scheduler", "executed", "processing"}`；空项为 `null`，`--once` 与测试都用它。
- `worker.run_forever(context, *, poll_seconds, max_rounds=None)`：常驻轮询；`--rounds N` 用于有界执行。
- `worker.execute_next_document_stage(context, *, process_token, now=None)`：至多一个 Document 阶段；`run_once` 返回 `{"process_token", "scheduler", "executed", "processing"}`。
- `runner.run_next_document_stage` / `runner.run_stages_for_documents` / `runner.describe_stages`：文档阶段的领取、显式重试/重处理与积压查询。
- `worker.claim_next_run(session, *, process_token, now=None)`：`FOR UPDATE SKIP LOCKED` 领取最旧 `queued` Run。
- `scheduler.run_scheduler_round(session, tasks, agents_by_id, *, now, model_config_path) -> list[ScheduleOutcome]`。
- `service.create_or_get_run` / `end_run` / `retry_run` / `claim_run` / `execute_claimed_run` /
  `fail_claimed_run`：manual、schedule、retry 共用同一入队路径（内部 `_enqueue_run`）。
- CLI：`python -m stocks.execution {trigger,runs,show,end,retry}`、`python -m stocks.worker [--once|--rounds N]`。
  文档入口：`python -m stocks.processing status|retry|reprocess|parse|index`；retry/reprocess 指定
  `--document-id <uuid> --stage <stage>`，默认入队、`--execute` 才当次执行，解除 running 必须 `--confirm-stopped`。
  已删除的旧入口（`create_claimed_and_execute` 一类"同进程创建+领取+执行"）不得复活。
- `document_processing_states`：主键 `(document_id, stage)`，用 `owner_token`、`attempts/max_attempts`、
  `next_attempt_at`、`reprocess_requested`、处理器/配置身份和 `input_batch_id` 表达当前执行；
  running 必有 token，其他状态不得带 token，failed 必有 error，skipped 必有 skip_reason。
- `WORKER_MAX_CONCURRENT_RUNS` 默认 2、`WORKER_MAX_CONCURRENT_DOCUMENTS` 默认 1，两者均为 1–16；
  CLI 分别用 `--max-concurrent-runs` / `--max-concurrent-documents` 覆盖。

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
   `ThreadPoolExecutor(max_workers=run_slots + document_slots + 1)`。`asyncio.to_thread` 走事件循环默认执行器
   （`ThreadPoolExecutor`，Python 3.12 默认大小约 `min(32, cpu_count() + 4)`），当配置并发大于该默认值时长 Run
   会占满线程并饿死调度轮，因此不使用。文档槽位必须与 Run 槽位分别占线程：共用池会让解析积压占满线程，
   反过来饿死采集/研究，也让调度轮没有线程可用。
7a. **文档槽位独立于 Run 槽位。** `_run_slot(..., claim=...)` 用同一个常驻循环，Run 槽位传
   `_claim_and_execute`，文档槽位传 `_claim_and_execute_document`。两者共享同一有界等待、退避与
   “停止后每槽最多再领取一次”语义；`max_concurrent_documents` 默认 1、可配 1–16。只对
   `registry.available_stages()` 中的阶段调用 `claim_stage`，因此未接入的 classify/facts 不会被轮询。
   每个文档槽位用**自己的** owner token（`{process_token}-doc-{uuid4}`）：并发槽位共用 token 会让
   所有权 fence 无法区分写入者，Owner 恢复时可能接受错的执行者。
7b. **Embedding 路由惰性解析。** `StageRegistry.index_factory` 只在真正执行一个已领取的 index 阶段时调用
   `load_embedding_client`；构建 registry、解析 Document 和执行采集 Run 不读文档处理的 embedding 路由，
   路由损坏只让该次 index 尝试失败（`EMBEDDING_ROUTING_UNAVAILABLE`，不自动重试）。研究 Run 的取材仍
   读取并核对自己的 Embedding 路由快照，不能把文档槽位的惰性初始化误写成研究不依赖该配置。
   缺处理器用 `pending` + `PROCESSOR_NOT_AVAILABLE`（不消耗尝试），能力/配置错误才写 `failed`。
7c. **分批结果每批都校验所有权，且以数据库最新行为准。** `persist_embedding_result` 在 Document 行锁下
   先 `assert_stage_owned`，再重读权威的 `document_parse_states.current_batch_id`（`populate_existing=True`）；
   旧批次或旧 token 都报 `STAGE_NOT_OWNED`，既不写向量也不写阶段结果。中间批（`stage_final_batch=False`）
   只写向量，只有最后覆盖批把“覆盖核对 + 阶段完成”放在同一事务。
   **`expire_on_commit=False` 下缓存实体不是授权。** `_lock_owned_state`、`claim_stage`、`retry_stage`、
   `request_reprocess`、`recover_stage` 的 `select(...).with_for_update()` 必须带 `execution_options(populate_existing=True)`，
   否则 Session 会校验旧状态/旧 token 而不是锁住的行；读 `document_parse_states` / `document_embedding_states`
   做复用或结果判断时同样要重读，否则另一 Session 已提交的新当前批次会被旧缓存掩盖。
7d. **新当前批次使旧输入的运行中阶段作废并可重新领取。** 解析成功切换当前批次时，
   `_apply_downstream` 对仍在 `running` 且 `input_batch_id` 不是新批次的 index/classify 阶段，在同一事务内
   释放所有权并回到 `pending`、`attempts=0`、指向新批次；旧执行者的写入被 7c 的 token 校验拒绝。
   不能只 `continue` 跳过运行中的行：那会让旧执行者完成阶段，新批次永远无人索引。
7e. **`--once` 的退出码要暴露阶段失败。** 文档阶段失败会被记录在对应 Document 上且不会终止槽位，
   但 `--once` 仍需以退出码 1 反映出来（`processing.status == "failed"`），否则部署/验收脚本无法
   区分“空积压”和“处理真的失败”。
7f. **`execute_next_document_stage` 不向调用方抛异常。** 它自身隔离阶段缺陷并返回安全失败摘要，
   因为 `--once` 在槽位守卫之外直接调用它；槽位路径本来就是每项自己捕获并继续。
7g. **多阶段就绪时的排序是“最老 Document 优先 + 声明顺序 tie-break”。** `run_next_document_stage`
   用 `ready_stage_ages` 比较各阶段最老就绪 Document；同一年龄**必须**按 `DOCUMENT_STAGE_ORDER`
   （parse → index → classify → facts）决定，不能用阶段名（`sorted(..., item)`）。后者会让
   `classify` 排在产生其输入的 `parse` 之前：一旦 classify 处理器注册，同年龄的新 Document
   会被反复领取分类而不是先解析。测试见 `test_stage_choice_breaks_an_age_tie_by_declared_priority`。
7h. **新增阶段处理器时，所有权 fence 要在写入派生结果之前显式校验一次。** 除 `apply_stage_outcome`
   在行锁内的 token 校验外，处理器还应先调用 `assert_stage_owned(session, claim)` 再改
   Document/派生行，否则一个已被新解析批次作废的尝试可能先把结果写进 Session，直到提交时才因
   token 不匹配整体回滚——错误码会变成 `STAGE_NOT_OWNED`，但语义上它本不该走到写结果这一步。
   同时：处理器的路由/词表必须用 `*_factory` 惰性解析（参照 `index_factory`、`classify_factory`），
   否则构建 registry 或执行采集 Run 时会读模型凭据，损坏的配置会变成全局故障而不是单次尝试失败。
7i. **硬依赖与适用性关系要分开表达。** `STAGE_DEPENDENCIES` 表示"没有它就没有输入"；
   `DIRECT_SKIP_APPLICABILITY` 表示"它决定该阶段是否需要"。二者不能混用：`facts` 的输入是
   已存内容本身，`classify` 只决定 `should_extract`，所以把 `classify` 写成 `facts` 的硬依赖会
   让"无需文本分类"的结构化响应永远卡在等待，反之把结构化资料硬绑到 `parse` 会让它在
   `unsupported` 后无法映射。阶段自读判定时需要自己的等待原因（如 `CLASSIFICATION_PENDING`）
   且**不消耗尝试次数**，并在上游完成时用 `clear_wait_reason` 清掉，否则会看起来永久阻塞。
   跳过传播要按传递闭包迭代，并区分"输入被跳过"与"适用性来源被跳过"两种情况。
7j. **派生写入前的所有权校验必须用本 Session 内的权威实体。** 处理器在提交派生结果前
   先 `assert_stage_owned`，并且要用 `select(...).with_for_update().execution_options(
   populate_existing=True)` 在**本 Session** 内重读 Document/阶段行；直接对调用方传入的、
   来自另一 Session 的 ORM 实例做 `session.refresh()` 会抛 `InvalidRequestError`，而依赖
   缓存实例会绕过行锁校验。
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
| `poll_seconds`/并发越界 | CLI exit 2 + `POLL_SECONDS_INVALID`/`CONCURRENCY_INVALID`/`DOCUMENT_CONCURRENCY_INVALID`/`ROUNDS_INVALID`，不静默夹取 |
| 未注册文档处理器 | status 显示 `pending` + `PROCESSOR_NOT_AVAILABLE`、`processor_available=false`；不自动领取、不消耗尝试 |
| 多个阶段同时有就绪工作 | 最老就绪 Document 优先；同年龄按 `DOCUMENT_STAGE_ORDER`，`parse` 先于依赖它的阶段 |
| 阶段路由/词表缺失或损坏 | 该次尝试 `failed` + 稳定错误码且不自动重试；解析、索引、采集与研究不受影响 |
| 模型返回结果超出受控词表 | 按无效响应有限重试后 `failed`；不得写成 `should_extract=false`，也不得覆盖上次成功结果 |
| 文档旧 token 或已被新解析批次作废的输入 | `STAGE_NOT_OWNED`，结果事务回滚，不能覆盖新所有者/新输入 |
| 文档 running 未确认旧进程停止 | `OWNER_CONFIRMATION_REQUIRED`；不解除所有权 |
| retry 已完成文档阶段 | `STAGE_ALREADY_COMPLETED`；只有 reprocess 可以显式重开 |
| 文档阶段实际失败且未耗尽预算 | 按持久 `next_attempt_at` 退避；预算耗尽后不自动领取 |
| 显式重建失败或中断 | 保留 `reprocess_requested`，不得把旧可用结果当作这次请求已完成 |

## Good / Base / Bad Cases

- Good：两个槽位（`WORKER_MAX_CONCURRENT_RUNS=2`），15 分钟采集与 8 分钟研究同时存在；采集槽位执行完立即领取下一份，
  调度轮仍每 `poll_seconds` 判定一次，研究长 Run 不推迟到期。
- Base：`worker --once` 在两侧均空时 → `scheduler: []`、`executed: null`、`processing: null`、退出码 0；
  未注册分类器不会被忙轮询。
- Bad：每轮重建 `create_task` 并 `asyncio.wait(..., timeout=poll_seconds)`（默认 `ALL_COMPLETED`）补齐槽位——空闲槽
  立刻返回会造成每间隔热循环，且一个空槽要等本轮最慢 Run；用 `asyncio.to_thread` 承担全部阻塞调用；
  为"防止结束后写资料"去回滚已入库的原文；用内存 `last_run` 做调度去重。

## Tests Required

- `tests/test_worker_processing.py`：文档槽位空闲不热轮询、长 Run 不饿死文档阶段、`--once` 至多一个阶段、
  未注册阶段不被领取且 status 显示等待原因、惰性 index factory、CLI status/retry/reprocess/parse/index 边界。
- `tests/test_processing_retry.py`：阶段状态机、领取原子性、重试预算、恢复 token fence、
  分批向量写入的每批所有权校验、失败的显式重处理保留请求、中断的显式重建不被误判为完成、
  适用性跳过的传播与重新打开、重处理不重开 Fact。
  **并发用例必须用两个独立 Session 真的切换所有权**：旧 Session 要强引用其已领取的阶段实体
  （以及解析指针），第二 Session 完成 Owner 恢复/重新领取或提交新的当前解析批次；只在全新 Session
  里伪造一个过期 claim 不能覆盖 `expire_on_commit=False` 下的缓存实体路径。参考
  `test_stale_index_owner_cannot_write_after_owner_recovery`、
  `test_new_current_batch_supersedes_a_running_index_attempt`（在 `embed()` 回调中让另一 Session 提交新批次）。
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
# Wrong：有行锁却仍用 Session identity map 中的旧 token 判断授权
state = session.scalar(select(DocumentProcessingState).where(...).with_for_update())

# Correct：在锁内刷新实体，再用数据库当前 token 判断；结果与状态同事务提交
state = session.scalar(
    select(DocumentProcessingState).where(...).with_for_update()
    .execution_options(populate_existing=True)
)
# validate status/owner_token, persist derived result and stage outcome, commit
```

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
