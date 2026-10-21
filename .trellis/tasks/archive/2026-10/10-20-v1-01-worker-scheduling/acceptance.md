# V1-01 验收记录

日期：2026-10-21（最终）
实施范围：独立 Worker 定时调度、manual/schedule/retry 统一原子触发、最小 `collection` Task 接入、Run 查询与 Owner 确认停机后的异常结束/重试。

**本文件是任务当前唯一验收结论。** 早期"SSH 不可用、全部运行验证未执行"的记录已作废（认证问题在
第二批前解除），逐条命令、退出码、计数、失败与修复过程只保留在
[`research/remote-verification.md`](research/remote-verification.md)，历史失败细节不在这里重复。
所有运行验证都只在测试服务器 `100.120.14.84` 的本任务隔离目录执行；本机未运行任何测试、编译、
类型、格式、数据库或 Docker 检查。凭据（SSH 密码、数据库口令、模型密钥）未写入仓库任何文件。

## 1. 实际执行的验证与结果

| 项 | 结果 | 退出码 | 备注 |
| --- | --- | --- | --- |
| 全新空库 `alembic upgrade head` | 通过 | `0` | 1s，`Running upgrade -> 0001` |
| 全新空库 `alembic check` | 无差异 | `0` | `No new upgrade operations detected.`（含 `task_schedules`、`triggered_by`/`retry_of_run_id`、`ck_task_definitions_trigger`、`ck_task_runs_closed_owner`、新索引） |
| `pytest -q tests/test_scheduler.py tests/test_execution.py` | **90 passed** | `0` | 3.84s（36 + 54）；含本轮改写的 4 个 `run_forever` 用例与 3 个 DST 用例 |
| `pytest -q tests/test_research.py tests/test_report_api.py tests/test_collection.py` | **52 passed, 1 warning** | `0` | 2.02s；warning 为既有 Starlette/httpx 依赖提示 |
| `docker compose config --quiet`（仓库 `compose.yaml`） | 通过 | `0` | services = postgres, api, worker |
| `docker compose ... build worker`（仓库 `backend/Dockerfile`） | **未完成** | `1` | 180s 限时内 `pip install --no-cache-dir .` 未跑完；根因是宿主到 PyPI 下载约 100 KB/s，registry 解析、context 传输与缓存层均正常，非代码缺陷（未做无镜像长时限复测） |
| `docker compose ... build worker`（**测试专用** pip 镜像 Dockerfile 副本，不入库） | 构建成功 | `0` | `Image ...-worker Built` |
| 容器内真实 `python -m stocks.worker` / `stocks.execution` CLI smoke | **9 阶段全部符合预期** | `0` | `--once`、`--rounds 1`、`trigger/show/end/retry/runs`；模型走 `127.0.0.1` 进程内 mock，无外网 |
| AC-3 真实子进程 `SIGKILL` + Owner `end`/`retry` 演练（最终脚本复跑） | **passed（18/18 检查）** | `0` | 收尾收窄 `reset()` 后重跑 `backend/tests/ac3_drill.py drive`，9s；子进程退出码 `-9` |

累计：**142 项 pytest**（90 + 52）+ 2 项新库结构检查 + Compose `config` 与（测试镜像）构建 +
9 阶段真实 CLI smoke + **18 项真实进程检查（演练脚本最终版已复跑通过）**。全量 pytest 未执行。

## 2. AC 映射

### AC-1 同时手动/定时触发只产生一个活跃 Run，按时区/间隔触发符合配置 — 通过

实现：`create_or_get_run` 与调度路径共用 `pg_advisory_xact_lock(hashtextextended(task_id,0))` + 每 Task
活跃 Run 条件唯一索引 `uq_task_runs_one_active`；手动触发先读活跃 Run，命中即返回且不读 Task 配置/模型凭据；
`scheduler._run_task_schedule` 在同一事务内完成"注册/判到期 → 入队 → 推进 `next_due_at`"；
`TriggerConfig` 强制 cron 带 IANA 时区、`every` 为 60s–30d、两者互斥；`next_due_at` 用 `croniter`
在任务时区墙上时间计算并存 UTC。

实测证据：`test_concurrent_scheduler_rounds_queue_one_run`、`test_due_round_queues_one_run_and_advances_due`、
`test_busy_task_skips_due_and_still_advances`、`test_due_task_with_active_run_skips_without_loading_model_routes`、
`test_cron_uses_task_timezone`、`test_cron_skips_spring_forward_gap`、
`test_cron_fires_repeated_wall_clock_time_only_once`、`test_cron_overlap_day_fires_each_distinct_instant_once`、
`test_next_due_uses_fixed_interval_anchor`、`test_advance_due_collapses_missed_intervals`、
`test_config_rejects_invalid_triggers`（均在 90 passed 内），以及容器内 `--once` / `--rounds 1` 真实执行。

限制：多 Worker **跨进程**真实竞争仅由单元用例（同一 PostgreSQL 上两个 Session 的 `SKIP LOCKED` 领取）与
单进程容器 smoke 覆盖，未起两个常驻 Worker 进程。

### AC-2 研究模型失败不阻止采集，采集故障不阻止研究读取存量 — 通过

实现：`collection` Task 的 `agents` 为空 → `model_snapshot = {"agents": {}, "embedding": None}`，
`_enqueue_run` 仅在 `agents` 非空时加载 chat/embedding 路由，`_run_collection` 完全不解析 `config/models.yaml`；
`run_scheduler_round` 每 Task 独立事务，单 Task 失败走 `_record_failure` 记录安全错误并推进 due；
`execute_next_run` 对每份 Run 独立 try/except；研究失败只写在该 Run 的 `error`/StepResult，不删既有 Document/Report。

本轮修复：到期任务先在 advisory lock 事务内 `get_active_run`，命中即 `skipped_active_run` 并只推进
`next_due_at`/`last_attempt_at`，不再先解析研究模型凭据、不再把"任务繁忙"记成配置失败。

实测证据：`test_worker_run_once_executes_collection_without_model_credentials`（`models.yaml` 不存在时采集仍成功）、
`test_task_with_unavailable_model_config_does_not_block_other_tasks`、
`test_due_task_with_active_run_skips_without_loading_model_routes`，加 `tests/test_collection.py` 38 项回归。
`worker --once` 的 `scheduler` 为空数组，确认 `scheduled_collection`/`scheduled_market_brief` 两个 disabled 示例未被调度。

限制：未做真实外网采集（`config/sources.yaml` 仍是 `owner@example.com` 占位，属 PA-1），采集故障路径由固定响应/单元用例覆盖。

### AC-3 模拟 Worker 中断可看到错误和已完成步骤，Owner 结束异常 Run 后能恢复且不重复制造成功资料 — 通过

实现：每步骤独立提交 `task_step_results`；`end_run` 只接受 `queued|running`、要求 CLI `--confirm-stopped`，
写 `status=cancelled` + `error.code=RUN_ENDED_BY_OWNER` 并清空 `process_token`；`retry_run` 只接受
`failed|cancelled`，复制**被结束 Run 自己的**配置/模型/提示词快照与连续成功的非 `research_agent` 步骤，
为新 Run 重建 Evidence Package 并把 `select_evidence` 输出的 `evidence_package_id` 改指新包，
`skipped` 步骤重判条件，`retry_of_run_id` 记录关联；原 Run 的步骤、ModelCall、error 原位保留。
所有权两层：`_assert_run_ownership`（`FOR UPDATE`）与 `_finish_run`（条件 `UPDATE ... RETURNING`）是严格 fence，
只保证旧执行不再写成功步骤结果或改写 Run 最终状态；`_ensure_run_still_owned` 是无锁事前检查，
只能阻止新步骤/新 Source 开始，**不能**回滚 `select_evidence`/`collect_source` 已在自身事务内提交的派生资料
（原文与证据包按统一入库规则保留为真实证据）。Owner 确认旧进程停止始终是前提。

实测证据（`ac3_drill.py drive`，真实 `trigger` 入队 + 真实子进程 `python -m stocks.worker`，进程内
`httpx.MockTransport`，无真实 Provider、无外网；Repository 种子走生产入口
`ingest_document`/`persist_parse_result`/`persist_embedding_result`）：

- `SIGKILL` 后 Run 仍 `running`、`process_token` 存在、`error = null`，已持久 1 个 `succeeded` 的 `evidence` 步骤，`show` 可读（`evidence_refs=2`）。
- `retry` 对 `running` Run → `RUN_NOT_ENDED`（exit 2）；`end` 缺 `--confirm-stopped` → `OWNER_CONFIRMATION_REQUIRED`（exit 2）。
- `end --confirm-stopped` → `cancelled` + `RUN_ENDED_BY_OWNER`，token 清空，步骤/包/错误保留。
- `retry` → 新 Run（`triggered_by=retry`、`retry_of_run_id` 正确），Worker 运行前已复制 `evidence/succeeded` 与新包。
- 第二个真实 Worker 完成该 Run：`succeeded`，Analysis + Report 生成；模型请求仅 `['/v1/chat/completions']` 一次、**无** `/embeddings`，即 Evidence Selection 未重跑。
- 新包 id ≠ 原包 id，但 `chunk_ids`/`parse_batch_ids` 完全一致；原 Run 在重试后仍 `cancelled`、无 Analysis/Report。
- Repository 计数不变：documents 1→1、chunks 2→2、embeddings 2→2；runs 0→2。

**最终脚本复核：** 收尾把 `reset()` 从整表删除收窄为只清演练 Task 自己的 Run 及关联行
（并在该 Task 仍有 `queued`/`running` Run 时 `SystemExit` 拒绝，不做任何删除），该版本已用
`tests/ac3_drill.py drive --workdir /tmp/ac3-drill` 在测试服务器任务隔离目录重跑：退出码 `0`、
`result=passed`、`checks_total=18 passed=18`、用时 9s（`SIGKILL` 子进程 `-9`、retry Worker `0`）。
复跑前用只读查询确认该数据库无任何活跃 Run（`saved_evidence_brief` 无 Run 记录，Repository 计数为 0），
`reset` 返回 `runs_removed=0`，即脚本在不删除任何数据的干净库上也能正常执行。产品代码本轮未改动。

## 3. 数据结构与初始化

- `task_definitions.trigger` 约束改为 `IN ('manual','schedule')`（`ck_task_definitions_trigger`，替换旧 manual-only 约束）。
- 新增 `task_schedules`：`task_id` 主键 + FK `ON DELETE CASCADE`、`trigger_kind`、`schedule_fingerprint`、
  `schedule_snapshot`、`next_due_at`、`last_fired_at`、`last_attempt_at`、`last_error`、`updated_at`；索引 `ix_task_schedules_due`。
- `task_runs` 新增 `triggered_by`（`manual|schedule|retry`，默认 `manual`）与自引用 `retry_of_run_id`
  （`ck_task_runs_retry_distinct`）；`status` 约束增加 `cancelled`（`ck_task_runs_closed_owner` 同步）；新增 `ix_task_runs_status_created`。
- 直接改 `models.py` 与 `0001_initial.py`，未新增增量迁移、未保留旧字段或兼容分支；新库 `upgrade head` + `check` 均已通过。

## 4. 其余改动

- 依赖新增 `croniter>=2,<3`（持久环境补装 `croniter==2.0.7`）。
- `compose.yaml` 新增 `worker` 服务（同镜像、同 env、只读 `./config`、不发布端口）。
- `.env.example` 新增 `WORKER_MAX_CONCURRENT_RUNS=2`；`settings.py` 同步 `worker_max_concurrent_runs`（1–16）。
- `stocks.execution` CLI 改为 `trigger/runs/show/end/retry`；旧的"同进程创建+领取+执行"入口与
  `create_claim_and_execute`、`refresh_active_run` 已删除，仓库内无残留引用。
- `api.py` 的 Run 只读输出新增 `triggered_by`/`retry_of_run_id`。
- 文档同步：`docs/execution.md`（调度持久状态与漏触发策略、V1 Task 子集、两类检索边界）、`docs/quickstart.md`
  （Worker/CLI 命令）、`docs/validation.md`（V1-01 已实现与待补边界）、`docs/data.md`（采集 Run 不另建表）。
- 工程规范新增 [`.trellis/spec/backend/worker-guidelines.md`](../../spec/backend/worker-guidelines.md)
  （Session/所有权/空闲等待/重试复制）并链接进 index；`testing-guidelines.md` 补充密码认证、
  `BatchMode` 失败不等于服务器不可达、以及环境缺失须及时反馈的规则（不含任何凭据）。

## 5. 未完成 / 受限

- **PA-1**：真实模型 Provider 端到端闭环（缺 chat/embedding 凭据）与真实外网 SEC 采集（占位邮箱）仍未推进，状态未变。
- **PA-2**：V0-03 解析器"正文直接位于 `article`/`main`/`body` 时逐字符换行"缺陷仍未修，本任务未触碰解析器。
- **V1-02**：解析/分类/Fact/索引的统一后台处理状态与积压重试未实现。
- **V1-05**：可靠变化水位与增量扫描进度未实现（Run 仍无游标推进）。
- 受保护 Web 控制台、常驻 API/Worker 部署与 ARM 目标机验收未做（按指令不启动常驻服务）。
- 仓库 `Dockerfile` 在无 pip 镜像条件下的构建时长未复测（受控 180s 内未完成）。
- 全量 pytest 与 `tests/test_embeddings.py` 未运行（本任务未改索引/向量代码路径）。
- 远程资源已在本次收尾清理完毕（`stocks-test-v1-01-postgres`、测试镜像、远程任务目录、`.tgz`、
  `/root/.stocks-v1-01-dbpass`、`/tmp/ac3-*` 演练产物），未改动其他任务容器/目录，未删除共享 Python/uv 工具环境；
  脱敏清单与退出码见 [remote-verification.md](research/remote-verification.md) 第 10 节。
