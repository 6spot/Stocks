# V1-01 远程验证记录

日期：2026-10-21
测试服务器：`100.120.14.84`（唯一验证入口）
首轮范围：远程环境建立、全新空库初始化、首批相关测试、第二批回归 / Compose / CLI smoke / AC-3 进程中断演练。
收尾复跑（第 9 节）：AC-3 演练脚本最终版（`reset()` 收窄后）的 `drive` 复跑，`result=passed` 18/18。
收尾清理（第 10 节）：本任务容器/镜像/远程目录/口令文件/演练产物已全部删除。
**未执行**：全量测试、真实外网采集（SEC）、真实模型 Provider 闭环（PA-1 保持延后）、`tests/test_embeddings.py`。

## 0. 认证与环境状态

- SSH 认证方式：密码认证（`PreferredAuthentications=password`、`PubkeyAuthentication=no`）。
  密码经文件描述符传入 `sshpass -d`，未写入仓库、日志或本文档。
- 上轮记录的 `Permission denied (publickey,password)` 阻塞**已解除**。
- 探测结果：`Python 3.12.15`、`uv 0.13.3`、Docker 守护进程正常、
  `/opt/stocks-test-runtime/tool-venvs/stock-tools` 可复用。
- 期间出现过两次**瞬时**密码认证失败（同一命令原样重试即成功），非凭据问题。

## 1. 隔离资源（均已于第 10 节清理）

| 项 | 名称 | 说明 |
| --- | --- | --- |
| 远程代码目录 | `/tmp/stocks-v1-01-worker-scheduling-20261021` | 本任务独立目录 |
| PostgreSQL 容器 | `stocks-test-v1-01-postgres` | `pgvector/pgvector:0.8.7-pg17-bookworm`，绑定 `127.0.0.1:55438` |
| 数据库口令 | `/root/.stocks-v1-01-dbpass`（600） | 随机生成，仅测试用；未记录明文 |
| 可编辑安装 | `.venv` → 持久 `stock-tools` | 已核对指向本任务目录 |

开工前核查：无 `v1-01` 遗留容器、无旧 `/tmp/stocks-v1-01*` 目录、端口 55438 空闲。
其他 v0 容器保持未动。

打包传输：`tar -czf /tmp/stocks-test-v1-01-worker-scheduling.tgz --exclude='.git'
--exclude='.venv' --exclude='.env' --exclude='__pycache__' --exclude='.pytest_cache'
--exclude='._*' --exclude='*.pyc' backend config docs compose.yaml .env.example`
（约 1.99 MB）；远程解包后删除 `._*`。

依赖：持久环境原先**缺 `croniter`**（`ModuleNotFoundError: No module named 'croniter'`），
已按 `pyproject.toml` 的 `croniter>=2,<3` 补装 `croniter==2.0.7`（含 `pytz`），随后
`uv pip install --no-deps --force-reinstall -e .` 把可编辑指针从上一个 v0-07 目录
改指本任务目录。

核对：
```text
.venv/bin/python -c "import stocks; print(stocks.__file__)"
→ /tmp/stocks-v1-01-worker-scheduling-20261021/backend/src/stocks/__init__.py
```

## 2. 全新空库初始化（结构变更验证）

```bash
cd /tmp/stocks-v1-01-worker-scheduling-20261021/backend
export DATABASE_URL="postgresql+psycopg://stocks:<test-only>@127.0.0.1:55438/stocks"
.venv/bin/alembic upgrade head   # 限时 120s（perl alarm，本机无 timeout）
.venv/bin/alembic check
```

| 命令 | 退出码 | 用时 | 结果 |
| --- | --- | --- | --- |
| `alembic upgrade head` | `0` | 1s | `Running upgrade -> 0001, Initial intelligence storage.` |
| `alembic check` | `0` | <1s | `No new upgrade operations detected.` |

结论：`models.py` 与 `0001_initial.py` 在**全新空库**上一致（含 `task_schedules`、
`task_runs.triggered_by` / `retry_of_run_id`、`ck_task_definitions_trigger`、
`ck_task_runs_closed_owner`、`ix_task_schedules_due`、`ix_task_runs_status_created`）。
本轮为初始化结构验证，未测试任何旧库升级路径。

## 3. 首批测试

```bash
export TEST_DATABASE_URL="postgresql+psycopg://stocks:<test-only>@127.0.0.1:55438/stocks"
export DATABASE_URL="$TEST_DATABASE_URL"
.venv/bin/python -m pytest -q tests/test_scheduler.py tests/test_execution.py  # 限时 180s
```

| 运行 | 退出码 | 计数 | 用时 |
| --- | --- | --- | --- |
| 合并运行 | `0` | `90 passed` | 3.84s（命令墙钟 5s） |
| `tests/test_scheduler.py` 单跑 | `0` | `36 passed` | 2.56s |
| `tests/test_execution.py` 单跑 | `0` | `54 passed` | 1.88s |
| 复跑 `alembic check` | `0` | `No new upgrade operations detected.` | <1s |

### 3.1 首轮失败与修复（两处，均已修复后通过）

1. **`backend/src/stocks/worker.py` 语法错误（收集阶段即失败）**
   ```text
   tests/test_scheduler.py:25: in <module>
       import stocks.worker as worker
   E   File ".../backend/src/stocks/worker.py", line 422
   E       elif any(item.run_id is not None for item in outcomes):
   E       ^^^^
   E   SyntaxError: invalid syntax
   ```
   原因：`run_forever` 的 `try/except` 之后直接写 `elif`，Python 不允许
   `try` 语句带 `elif`。修复：改为 `else:` 块内 `if any(...): wakeup.set()`，
   语义与意图一致（仅当调度轮未抛异常时判断是否唤醒空闲槽位）。
   `test_scheduler.py` 整文件此前从未被导入过，所以该错误在本轮首次暴露。

2. **`backend/tests/test_scheduler.py::test_retry_reuses_previous_steps_but_reruns_the_agent`**
   ```text
   E   sqlalchemy.orm.exc.DetachedInstanceError: Instance <EvidencePackage ...>
       is not bound to a Session; attribute refresh operation cannot proceed
   ```
   原因：测试在 `with Session(engine)` 退出后才访问 `package.id`。该 fixture 的
   `engine` 使用 SQLAlchemy 默认 `expire_on_commit=True`，`commit()` 使属性过期，
   会话关闭后无法刷新。**这是测试缺陷，不是 `retry_run` 的行为缺陷**（同一用例中
   `new_package.id != ...` 的比较对象取错才是真正问题来源）。修复：在会话内
   `session.flush()` 后保存 `package_id = package.id`，后续断言改用 `package_id`。

修复后两文件全部通过；未改动被测业务语义（除上述语法错误的等价改写）。

## 4. 第二批：相关回归（执行生命周期 / API run 字段 / collection 适配）

```bash
cd /tmp/stocks-v1-01-worker-scheduling-20261021/backend
export TEST_DATABASE_URL="postgresql+psycopg://stocks:<test-only>@127.0.0.1:55438/stocks"
export DATABASE_URL="$TEST_DATABASE_URL"
timeout 120 .venv/bin/python -m pytest -q tests/test_research.py tests/test_report_api.py tests/test_collection.py
```

| 命令 | 退出码 | 计数 | 用时 |
| --- | --- | --- | --- |
| `pytest -q tests/test_research.py tests/test_report_api.py tests/test_collection.py` | `0` | `52 passed, 1 warning` | 2.02s |
| `pytest -q tests/test_research.py` | `0` | `7 passed` | 0.97s |
| `pytest -q tests/test_report_api.py` | `0` | `7 passed, 1 warning` | 1.53s |
| `pytest -q tests/test_collection.py` | `0` | `38 passed` | 0.60s |

唯一 warning 是 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`，
属既有依赖提示，非本任务代码问题。

累计已通过：`test_scheduler.py` + `test_execution.py` 90 项（首批）与上述 52 项。
未重跑首批 90 项：本批未修改任何业务源码（仅新增一个不被 pytest 收集的演练脚本）。

## 5. 第二批：Compose 配置与构建

Compose 验证只使用本任务隔离资源，未创建生产 `pgdata`，未启动长期 API/Worker 服务，
未触碰其他 v0/v1 容器。测试专用文件均在远程任务目录内生成，**不属于仓库**：
`compose.test-override.yaml`（worker 用 host 网络访问 `127.0.0.1:55438` 的隔离 PG）、
`compose.test-build.yaml`（仅替换 build dockerfile）、`backend/Dockerfile.ac3test`
（在 `RUN pip install` 前加 pip 镜像 `ENV PIP_INDEX_URL`）。

`.env` 由 `.env.example` 生成（`umask 077`、`chmod 600`），仅含测试值：随机 DB 口令、
`MODEL_API_KEY=test-only-not-a-real-key`、`MODEL_NAME=test-chat-model`、
`EMBEDDING_MODEL_NAME=test-embedding-model`。**未在日志或本文档输出任何口令。**

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `docker compose config --quiet`（仓库 `compose.yaml`） | `0` | 通过 |
| `docker compose -f compose.yaml -f compose.test-override.yaml config --quiet` | `0` | 通过；services = postgres, api, worker |
| `docker compose ... build worker`（仓库原 `Dockerfile`，180s 限时） | `1` | **超时预算内失败**：`pip install --no-cache-dir .` 在 180s 内未完成（见下） |
| `docker compose ... build worker`（`Dockerfile.ac3test`，180s 限时） | `0` | 构建成功，`Image ...-worker Built`，`Successfully installed` ×1 |

**首次构建失败的根因（非代码缺陷）**：宿主到 pypi 的下载速度约 100 KB/s，构建阶段
`Installing build dependencies` 用了 70s，之后逐个下载 wheel 超出限时。构建日志显示
docker registry 解析、context 传输、缓存层全部正常。按指令未无限重试，改为在**测试专用**
Dockerfile 副本里加 pip 镜像后一次成功；仓库 `backend/Dockerfile` 未被修改。

## 6. 第二批：Worker / CLI 实际 smoke（真实进程）

全部通过 `docker compose run --rm --no-deps -T worker ...` 在容器内执行，即真实镜像 +
真实 PostgreSQL，不启动常驻服务。模型通过本机 `127.0.0.1:18080` 的**进程内** mock
OpenAI-compatible 端点（`ac3_drill.py mock-provider`）提供，无外部 Provider、无外网请求。

| 阶段 | 命令 | 退出码 | 观察结果 |
| --- | --- | --- | --- |
| `worker_once` | `python -m stocks.worker --once` | `0` | `scheduler: []`、`executed: null`（空队列不忙轮询） |
| `trigger_queued` | `python -m stocks.execution trigger --task empty_evidence_skip` | `0` | `status: queued`、`triggered_by: manual` |
| `show_queued` | `... show --run-id <id>` | `0` | `status: queued`、`steps: []` |
| `end_queued` | `... end --run-id <id> --confirm-stopped` | `0` | `status: cancelled`、`error.code = RUN_ENDED_BY_OWNER` |
| `retry_queued` | `... retry --run-id <id>` | `0` | 新 `run_id`、`status: queued`、`triggered_by: retry`、`retry_of_run_id` 指向被结束 Run |
| `worker_executes_retry` | `python -m stocks.worker --once` | `0` | 领取该 retry Run → `status: succeeded` |
| `show_executed` | `... show --run-id <retry id>` | `0` | `evidence` 步骤 `succeeded`、`research` 步骤 `skipped`（`condition_not_met`），与 `config/tasks.yaml` 的 `empty_evidence_skip` 示例语义一致 |
| `runs_list` | `... runs --limit 5` | `0` | retry Run `succeeded`、原 Run `cancelled` 且保留 `RUN_ENDED_BY_OWNER` |
| `worker_rounds` | `python -m stocks.worker --rounds 1 --poll-seconds 0.5 --max-concurrent-runs 1` | `0` | `{"rounds": 1}`，连续模式可正常启动并按时退出 |

mock 端点收到的请求：`[{"path": "/v1/embeddings"}, {"path": "/v1/embeddings"}]`（两次 Evidence Selection
各自的查询向量），随后 retry 走的是一个已含空证据包的 Run，符合预期。
运行 `end` 前也验证了 `retry` 对 `queued` Run 返回 `RUN_NOT_ENDED`（首批单元用例已覆盖，
本轮在容器内通过 CLI 再次确认）。

## 7. 第二批：AC-3 真实进程中断 + Owner 恢复演练

新增脚本 `backend/tests/ac3_drill.py`（**不匹配 `test_*.py`，不会被 pytest 收集**，只在
远程任务目录手工执行）。脚本不手工插入步骤结果：它用真实 `python -m stocks.execution trigger`
入队、用真实子进程 `python -m stocks.worker`（`run_once`）领取并执行，模型传输用
`httpx.MockTransport` 在进程内注入，无真实 Provider、无外网。Repository 种子数据通过
生产入口 `ingest_document` / `persist_parse_result` / `persist_embedding_result` 建立。

命令（凭据仅从远程文件和环境变量取得，未输出）：

```bash
cd /tmp/stocks-v1-01-worker-scheduling-20261021/backend
export DATABASE_URL="postgresql+psycopg://stocks:<test-only>@127.0.0.1:55438/stocks"
export MODEL_API_BASE_URL=http://127.0.0.1:18080/v1 MODEL_API_KEY=test-only-not-a-real-key \
       MODEL_NAME=test-chat-model EMBEDDING_MODEL_NAME=test-embedding-model
timeout 240 .venv/bin/python tests/ac3_drill.py drive --workdir /tmp/ac3-drill
```

| 项 | 结果 |
| --- | --- |
| 退出码 | `0` |
| `result` | `passed`（18/18 检查项通过） |
| 原 Run | `f1ec003b-dc50-4958-990c-637d7cf3f48e` |
| retry Run | `882dc6b9-48c7-4758-9672-f66a707859ec` |
| Worker 子进程退出码 | `-9`（`SIGKILL`） |
| retry Worker 退出码 | `0` |
| retry Worker 实际模型请求 | `['/v1/chat/completions']`（**无** `/embeddings`） |

逐项检查（全部 PASS）：

| 检查 | 证据 |
| --- | --- |
| `agent_step_blocked` | 第 1 步 `select_evidence` 已提交、第 2 步 `research_agent` 停在模型请求中 |
| `worker_sigkilled` | 子进程被 `SIGKILL`（`-9`） |
| `state_after_kill_kept_progress` | Run 仍 `running`、`process_token` 存在、`error = null`、仅 1 个 `evidence` 步骤且 `succeeded` |
| `show_after_kill` | CLI 可读：`status=running`、`evidence/succeeded`、`evidence_refs=2`、`evidence_package_id` 指向已存包 |
| `retry_refused_while_running` | `retry` 退出码 2、`RUN_NOT_ENDED` |
| `end_requires_confirmation` | 不带 `--confirm-stopped` 的 `end` 退出码 2、`OWNER_CONFIRMATION_REQUIRED` |
| `ended_by_owner` | `end --confirm-stopped` 退出码 0 → `cancelled` + `RUN_ENDED_BY_OWNER`；`process_token` 已清空 |
| `retry_queued` | 新 `run_id` ≠ 原 Run，`status=queued`，`triggered_by=retry`，`retry_of_run_id` 正确 |
| `retry_step_snapshot_copied_before_worker_run` | retry Run 在 Worker 运行前已含复制的 `evidence/succeeded` 步骤与新 Evidence Package |
| `retry_worker_claims_run` / `retry_run_succeeded` / `retry_run_has_report` | retry Run `succeeded`，Analysis 与 Report 均生成 |
| `evidence_selection_not_rerun` | retry Worker 只发 1 次 `chat/completions`，无 `embeddings` |
| `retry_agent_step_ran_once` | `chat/completions` 恰好 1 次 |
| `retry_package_is_new_run_owned` | 新包 id `f8a7ad83-…` ≠ 原包 `5aa96d6f-…` |
| `retry_package_reused_evidence_snapshot` | 新包 `chunk_ids`/`parse_batch_ids` 与原包完全一致 |
| `original_history_preserved` | 原 Run 仍 `cancelled` + `RUN_ENDED_BY_OWNER`，仅 1 个 `succeeded` 步骤，无 Analysis/Report |
| `repository_not_duplicated` | documents 1→1、chunks 2→2、embeddings 2→2、runs 0→2 |

状态快照（脱敏，仅 ID/状态/时间）：

```text
state_while_blocked  running  token=true  steps=[evidence:succeeded]          pkg=5aa96d6f…  analysis=false report=false
state_after_kill     running  token=true  steps=[evidence:succeeded]          pkg=5aa96d6f…  analysis=false report=false
state_after_end      cancelled token=false steps=[evidence:succeeded]         pkg=5aa96d6f…  analysis=false report=false
retry_before_worker  queued   token=false steps=[evidence:succeeded]          pkg=f8a7ad83…  analysis=false report=false
retry_after_worker   succeeded token=false steps=[evidence:succeeded, research:succeeded] pkg=f8a7ad83… analysis=true report=true
original_after_retry cancelled token=false steps=[evidence:succeeded]          pkg=5aa96d6f…  analysis=false report=false
```

“已持久成功步骤”不是手工插入的：`evidence` 步骤由真实 Worker 在 `_persist_step` 内提交，
`evidence_package` 由 `select_evidence` 在步骤内自行提交；`SIGKILL` 后无法回滚。
这也验证了文档所述 fence 边界：严格 fence 只保证被结束的 Run 不再写入步骤结果/最终状态，
已由步骤自身提交的资料按统一入库规则保留。

演练脚本自身的两处修正（均已在远程重跑验证，不涉及产品代码）：

1. `persist_embedding_result(...)` 漏传必填 `error=None` → `TypeError`，直接报错退出，未产生任何 Run。
2. 种子函数原先“文档已存在就整体跳过”，导致首次失败后留下**已解析但未索引**的 Document；
   无向量则 Evidence Selection 选出 0 条，Agent 步骤立即 `EVIDENCE_UNAVAILABLE` 失败而不进入阻塞，
   脚本报 `agent_step_never_started`。改为幂等补齐（仅缺 Embedding 时补建），重跑后才真正走到阻塞分支。
   该现象本身是预期行为：无索引就不能研究，而不是演练失败。

以上均为脚本缺陷，产品代码未因此修改。

## 8. 资源保留（已于第 10 节全部清理）

按当轮指令，Check 最终审查前曾经**暂留**以下本任务独占资源（现已删除，实际清理记录见第 10 节）：

- 容器 `stocks-test-v1-01-postgres`（`127.0.0.1:55438`，未对外暴露）。
- 远程目录 `/tmp/stocks-v1-01-worker-scheduling-20261021`（含 `.venv` 链接、可编辑安装、
  测试专用 compose override / Dockerfile 副本 / `.env`）。
- 镜像 `<dir>-worker:latest`（`349d22302f3e`，355MB，仅测试用）。
- `/root/.stocks-v1-01-dbpass`（600，仅测试口令）。
- `/tmp/ac3-drill*`、`/tmp/ac3-mock*`、`/tmp/ac3-cli-smoke.jsonl`、`/tmp/ac3-build*.log`
  等本任务日志/演练产物。

已确认：无遗留 `stocks-v1-01` Worker 容器（`docker ps -a` 仅 postgres）；mock provider 端口
18080 已释放；未改动其他 v0/v1 任务容器与目录。

清理计划（已在第 10 节执行完毕，留下供核对）：
```bash
docker rm -f stocks-test-v1-01-postgres
docker image rm stocks-v1-01-worker-scheduling-20261021-worker:latest
rm -rf /tmp/stocks-v1-01-worker-scheduling-20261021 /tmp/stocks-test-v1-01-worker-scheduling.tgz
rm -f /root/.stocks-v1-01-dbpass
# /tmp/ac3-* 改为逐条明确路径删除，不用通配
```

## 9. AC-3 演练脚本最终版复跑（`reset()` 收窄后）

日期：2026-10-21（收尾轮）。目的：确认 Check 收尾对 `backend/tests/ac3_drill.py` `reset()` 的收窄
（只删 `TASK_ID = saved_evidence_brief` 的 Run 及关联行；该 Task 有任何 `queued`/`running` Run 时直接
`SystemExit` 拒绝）不影响 `drive` 路径。

### 9.1 复跑前只读核对

| 检查 | 结果 |
| --- | --- |
| 本任务容器 | 仅 `stocks-test-v1-01-postgres`（`Up`），`pg_isready` = `accepting connections` |
| 本任务 Worker/演练进程 | 无（`ps` 中无 `stocks.worker` / `ac3_drill` / `mock-provider`） |
| mock Provider 端口 18080 | 空闲（复跑使用进程内 `httpx.MockTransport`，无需 mock Provider） |
| 任意 Task 的 `queued`/`running` Run | **0 行** |
| `saved_evidence_brief` 的 Run | **0 行** |
| Repository 计数 | documents 0 / chunks 0 / embeddings 0 / packages 0 / runs 0（上轮演练库已被清） |

同步：将本机当前 `backend/tests/ac3_drill.py` scp 覆盖到远程任务目录的同一路径（远程旧版 29636 字节，
新版 31011 字节，差异仅为 `reset()` 收窄与 CLI 帮助文本）。

### 9.2 实际命令与结果

```bash
cd /tmp/stocks-v1-01-worker-scheduling-20261021/backend
export DATABASE_URL="postgresql+psycopg://stocks:<test-only>@127.0.0.1:55438/stocks"
export MODEL_API_BASE_URL=http://127.0.0.1:18080/v1 MODEL_API_KEY=<test-only> \
       MODEL_NAME=test-chat-model EMBEDDING_MODEL_NAME=test-embedding-model
perl -e 'alarm 85; exec @ARGV' ./.venv/bin/python tests/ac3_drill.py drive --workdir /tmp/ac3-drill
```

| 项 | 结果 |
| --- | --- |
| 退出码 | `0` |
| 用时 | 9s（`alarm 85` 未触发） |
| `result` | `passed` |
| 检查项 | `checks_total=18`、`passed=18` |
| `reset` 返回 | `task_id=saved_evidence_brief`、`runs_removed=0`、各表 `deleted=0` |
| 原 Run / retry Run | `3371f17a-…` / `8e4dfc03-…`（均为本次新建） |
| 状态 | `queued` → 结束后 `cancelled` → retry `succeeded` |
| Worker 子进程退出码 | `-9`（`SIGKILL`）；retry Worker `0` |
| 阻塞期间模型请求 | `['/v1/embeddings', '/v1/embeddings', '/v1/chat/completions']` |
| retry Worker 模型请求 | `['/v1/chat/completions']`（**无** `/embeddings`） |
| Repository 计数 | documents 1→1、chunks 2→2、embeddings 2→2、packages 0→2、runs 0→2 |
| 非预期退出码的 CLI 命令 | 无 |
| stderr | 空 |

逐项检查（全部 PASS，与上轮同名）：
`agent_step_blocked`、`retry_refused_while_running`、`end_requires_confirmation`、`worker_sigkilled`、
`ended_by_owner`、`retry_queued`、`retry_worker_claims_run`、`retry_run_succeeded`、`retry_run_has_report`、
`retry_reused_succeeded_step`、`retry_step_snapshot_copied_before_worker_run`、`evidence_selection_not_rerun`、
`retry_agent_step_ran_once`、`retry_package_is_new_run_owned`、`retry_package_reused_evidence_snapshot`、
`original_history_preserved`、`repository_not_duplicated`、`state_after_kill_kept_progress`。

结论：收窄后的 `reset()` 在“无活跃 Run 且无本 Task 历史”的库上正常执行且不删任何数据，`drive` 全链
仍为 18/18；PRD 的 AC-3 据此勾选。本轮**未**改动任何产品源码，按指令未重跑已绿的 142 项 pytest、
Compose 与 CLI smoke。

## 10. 本任务资源清理（已完成）

时间：2026-10-21。只删除本任务独占资源；其他 v0 任务容器/目录、共享 Python/uv 工具环境均未触碰。

| 对象 | 命令 | 退出码 | 清理后核对 |
| --- | --- | --- | --- |
| 容器 `stocks-test-v1-01-postgres` | `docker rm -f` | `0` | `docker ps -a` 无 `v1-01` 容器 |
| 测试镜像 `stocks-v1-01-worker-scheduling-20261021-worker:latest`（`349d22302f3e`） | `docker image rm`（删前 `docker ps -a --filter ancestor=…` 返回空，无引用） | `0` | `Untagged` + `Deleted`，`docker images` 无 `v1-01` |
| 远程任务目录 `/tmp/stocks-v1-01-worker-scheduling-20261021` | `rm -rf` | `0` | `ls -d` → `dir_absent` |
| 传输归档 `/tmp/stocks-test-v1-01-worker-scheduling.tgz` | `rm -f` | `0` | `ls -l` → `tgz_absent` |
| `/root/.stocks-v1-01-dbpass`（600） | `rm -f` | `0` | `ls -l` → `dbpass_absent` |
| `/tmp/ac3-drill`（目录）与 20 个 `/tmp/ac3-*` / `/tmp/ac3_*.sh` 文件 | 逐条明确路径 `rm -rf` / `rm -f`（**未使用** `/tmp/ac3-*` 通配） | `0` | `ls -d /tmp/ac3*` → `ac3_absent` |

删除前按明确清单逐项 `stat` 核对归属与时间（均为本任务 2026-10-21 14:37–14:43 产物），并扫描是否存在
清单外的 `/tmp/ac3*`（结果：无）。

清理后核对：

| 检查 | 结果 |
| --- | --- |
| `docker ps -a` 中含 `v1-01` 的容器 | 无 |
| `docker images` 中含 `v1-01` 的镜像 | 无 |
| `stocks.worker` / `ac3_drill` / `mock-provider` / `stocks.execution` 进程 | 无 |
| 端口 55438 / 18080 | 均空闲 |
| 共享工具环境 `/opt/stocks-test-runtime/tool-venvs/stock-tools`、`/opt/stocks-test-runtime/bin/{python3.12,uv}` | **保留** |
| 其他任务容器 | 未变（`stocks-test-v0-02-*`、`stocks-test-v0-03-*`、`stocks-v0-03-accept-postgres`、`monos-boundary-probe-*`） |

持久工具环境里的可编辑安装指针原本指向已删除的本任务目录，按规范属于预期状态；后续任务用
`uv pip install -e .` 重新指向自己的目录即可，不需要重装依赖。本机侧的临时 SSH 口令文件与远程脚本副本
已在本次收尾一并删除，未写入仓库、日志或本文档。

## 11. 本轮未覆盖（保持未验证）

- 全量 pytest；`tests/test_embeddings.py`（本任务未改索引/向量代码）。
- 真实外网采集（SEC Feed，`config/sources.yaml` 仍是 `owner@example.com` 占位）与真实模型
  Provider 闭环 → 仍是 PA-1，状态未变。
- 长期常驻 API/Worker 服务、Nginx、ARM 目标机部署（按指令不启动常驻服务）。
- 多 Worker 进程同时竞争的真实跨进程并发（仅用现有单元用例 + 容器内单进程验证）。
- Compose 在**无 pip 镜像**条件下的构建时长（本次 180s 内未完成，未做无镜像复测）。

