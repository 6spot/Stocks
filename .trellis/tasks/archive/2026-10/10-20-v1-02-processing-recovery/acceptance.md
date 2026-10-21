# V1-02 验收记录

任务：统一后台处理状态与积压重试（parse/classify/facts/index）。
唯一领域契约：`docs/data.md`、`docs/execution.md`、`docs/validation.md`。
实施记录：[batch-a-notes.md](research/batch-a-notes.md)、[batch-b-notes.md](research/batch-b-notes.md)、[review.md](research/review.md)。

## 已实现（当前代码）

1. **统一阶段状态**：`document_processing_states`，`(document_id, stage)` 主键，四阶段各一条。Document 新内容版本在入库同一事务内创建待办；相同字节复用不重开已完成阶段。状态 `pending | running | completed | failed | skipped` 中，`completed`/`failed` 由刚持久化的派生结果（解析批次状态、向量覆盖）推导，不由调用方声明；`pending`/`running` 表示排队、等待原因或当前领取者；`skipped` 只由处理器显式适用性及其依赖传播产生。
2. **领取与执行分离**：`claim_stage` 用依赖 `EXISTS` + `FOR UPDATE SKIP LOCKED` + 复核，claim 自身提交；处理器在事务外运行。
3. **结果与阶段同事务**：`persist_parse_result` / `persist_embedding_result` 接收 `stage_claim`，在同一事务内写阶段结局；索引只有最后覆盖批写完成，中间批只写向量并校验所有权。所有权与当前批次均以**数据库锁定的最新行**为准（`populate_existing`），不使用 Session 缓存实体。
3b. **新当前批次作废旧输入的运行中阶段**：解析成功切换批次时，输入批次不同的 `running` index/classify 在同一事务内释放所有权、回 `pending`、`attempts=0` 并指向新批次；旧执行者的写入报 `STAGE_NOT_OWNED`，新批次下一次正常领取即被索引。
4. **有限重试**：指数退避 + `max_attempts`，耗尽后停止自动领取，只能 Owner 重开。
5. **能力缺失 ≠ 跳过**：未注册处理器保持 `pending` + `PROCESSOR_NOT_AVAILABLE`，不消耗尝试、不轮询。跳过需处理器显式 `applicability`；依赖已跳过阶段的下游记为 `DEPENDENCY_SKIPPED` 并在上游完成后重开。
6. **中断不自动接管**：`running` 只能 `recover_stage(confirm_stopped=True)`；已有持久结果识别为完成，显式重建请求优先回到 `pending`；旧 token 写入被拒。
7. **Worker 文档槽位**：`WORKER_MAX_CONCURRENT_DOCUMENTS`（默认 1，1–16），与 Run 槽位独立，各槽独立 owner token；线程池覆盖两类槽位 + 调度轮。
8. **惰性 Embedding 路由**：文档处理只在真正执行 index 阶段时读 `config/models.yaml`，损坏只让该次尝试失败且不自动重试。研究 Run 的 Evidence Selection 仍按既有规则读取并核对自身 Embedding 路由快照，不受此约束。
9. **统一 CLI**：`stocks.processing status|retry|reprocess|parse|index`，全部走同一 claim + token 路径；`stocks.indexing` 是其调用方，无平行执行路径。

## AC 结果

| AC | 结论 | 依据 |
| --- | --- | --- |
| AC-1 | 通过 | `test_classification_failure_does_not_block_indexing`、`test_applicability_skip_propagates_and_is_not_revoked_by_completion`、`test_missing_processor_waits_without_consuming_an_attempt`、`test_missing_classifier_stays_visible_as_pending_in_status`；smoke 的 `status` 显示 classify/facts 未接入但 pending+原因 |
| AC-2 | 通过 | `test_new_document_registers_four_pending_stages_in_the_ingest_transaction`、`test_failed_stage_is_retried_with_backoff_and_stops_when_exhausted`、`test_recovery_keeps_durable_result_of_an_interrupted_stage`、smoke 重复 `--once` 不重跑已完成 parse |
| AC-3 | 通过 | `test_switching_embedding_model_does_not_reopen_a_completed_index`、`test_owner_reprocess_index_reuses_existing_chunks`、`test_reprocessing_a_classification_does_not_reopen_facts`、`test_reparse_failure_keeps_current_chunks_and_report_references`；smoke 断言的 `retry`/`reprocess` 语义 |
| AC-4 | 通过 | `test_claim_is_atomic_across_two_sessions`、`test_running_stage_requires_owner_confirmation_to_recover`、`test_intermediate_vector_batch_rejects_a_released_owner`、`test_stale_index_owner_cannot_write_after_owner_recovery`（旧 Session 持有已领取实体、第二 Session 恢复+重领）、`test_new_current_batch_supersedes_a_running_index_attempt`（`embed()` 中另一 Session 提交新批次，旧批次不得完成、新批次被索引）、`test_document_slot_waits_instead_of_polling_an_empty_backlog`、`test_document_slot_keeps_running_while_a_run_slot_is_busy`、`test_document_stage_choice_does_not_starve_older_index_work` |

## 实际验证（测试服务器 `100.120.14.84`，任务隔离目录）

目录 `/tmp/stocks-v1-02-processing-recovery-c1`（editable 已指向该目录，`stocks.__file__` 已核对）；
容器 `stocks-test-v1-02-processing-recovery-postgres`（127.0.0.1:55442）、`...-freshdb`（55443）、
`...-s3`（18346）。密码从容器环境注入，未写入记录或命令行参数。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 负责范围回归 | `pytest -q tests/test_processing_retry.py tests/test_worker_processing.py tests/test_processing.py tests/test_embeddings.py tests/test_repository.py tests/test_collection.py tests/test_scheduler.py tests/test_report_api.py` | **185 passed, 1 warning** |
| 全量（批次 B 已记录，未重复） | `pytest -q tests/` | 258 passed（此前批次 B） |
| 全新库结构 | 清空 schema 后 `alembic upgrade head` + `alembic check` | `0001`；`No new upgrade operations detected` |
| 真实子进程 smoke | `python tests/v1_02_processing_smoke.py`（真实 `worker --once`、`stocks.processing` 子进程、真实 PG + S3） | **34/34 PASS**，`summary: OK` |
| 并发修复定向（修复前源码） | `PYTHONPATH=<修复前 src> pytest -q tests/test_processing_retry.py -k "stale_index_owner or supersedes_a_running_index"` | 退出码 1，**2 failed**（确认新用例真能复现缺陷） |
| 并发修复定向（当前源码） | 同一 `-k` 选择 | 退出码 0，**2 passed** |
| 并发修复受影响范围 | `pytest -q tests/test_processing_retry.py tests/test_worker_processing.py tests/test_embeddings.py` | 退出码 0，**66 passed** |
| 阶段/仓储回归 | `pytest -q tests/test_processing.py tests/test_repository.py` | 退出码 0，**40 passed** |
| 最终源码全受影响集（上述 5 文件） | `pytest -q tests/test_processing_retry.py tests/test_worker_processing.py tests/test_embeddings.py tests/test_processing.py tests/test_repository.py` | 退出码 0，**106 passed** |
| 最终源码 smoke | `python tests/v1_02_processing_smoke.py` | 退出码 0，**34/34 PASS**，`summary: OK` |

smoke 覆盖：入库即生成四阶段待办 → `status` 显示 classify/facts 未接入但 pending+原因 → `worker --once`
（`models.yaml` 故意损坏）完成 parse 且 index 只失败自己并让退出码为 1 → 重复 `--once` 不重跑已完成 parse →
注入固定 Embedding 后 index completed 且 Chunk 未变 → 无 `--confirm-stopped` 的 retry 被拒 → 确认后恢复识别
已提交结果并清 token → `retry` 拒绝已完成 parse、`reprocess` 重开 → 再次 `--once` 产生新成功批次且旧批次保留 →
reparse 后 completed facts 仍 completed 且 index 对新批次重开。

## 未完成 / 受限（不得视为已通过）

- 分类算法与 Decision Provider（V1-03）、Fact 表与抽取/Source Mapper（V1-04）未实现；`classify`/`facts` 在各处
  保持 `pending` + `PROCESSOR_NOT_AVAILABLE`。
- 数值资料“无需向量”的跳过协议只由固定处理器验证，真实 Source Mapper 属 V1-04。
- PA-1 真实模型闭环、PA-2 既有 HTML 缺陷、Compose 构建与 ARM 部署验收、受保护 Web 控制台、V1-05 变化水位
  不在本任务，未验证也未关闭。
- 本任务未提交、未推送、未归档。

## 远程清理（2026-10-21 10:49:41 UTC）

仅执行授权的资源清理，无新增测试、源码修改或依赖安装。通过密码文件描述符连接
`root@100.120.14.84`；未输出凭据、容器环境或文件内容。

- 删除前以 `docker ps -a`、`docker inspect` 的名称/镜像/挂载信息核对归属；以目录清单及 tar 成员名确认归档为本任务的 Stocks 源码快照。
- `docker rm -f -v stocks-test-v1-02-processing-recovery-postgres stocks-test-v1-02-processing-recovery-freshdb stocks-test-v1-02-processing-recovery-s3`：退出码 **0**；对应端口 55442、55443、18346。
- 同时删除两个 PostgreSQL 专用匿名卷：`37c5c576f9940fb974dfc9370de86f68db09bbd7b9741156ee468892e2fa80bb`、`eee3dc3fda460d093b14a366b64bf14355e73faf8f3b13cea6110d29e22a6379`。
- 逐路径删除目录：`/tmp/stocks-v1-02-processing-recovery-a1`、`/tmp/stocks-v1-02-processing-recovery-b1`、`/tmp/stocks-v1-02-processing-recovery-c1`、`/tmp/stocks-v1-02-processing-recovery-s3-data`。
- 逐路径删除 **17** 份归档：`/tmp/stocks-v102-a.tgz`、`/tmp/stocks-test-v1-02-b.tgz`、`/tmp/stocks-v1-02-check-c1.tgz` 至 `/tmp/stocks-v1-02-check-c15.tgz`（整数 1–15 全部，未使用通配符删除）。这些归档包含此前修复迭代的源码快照。
- 检查 `/proc/*/cwd` 与进程参数，仅输出 PID/工作目录/程序名及 Worker/smoke 布尔标记；未发现本任务残留进程，无需终止进程。任务目录内 smoke 脚本/配置/缓存随目录删除；未发现独立的本任务修复前快照目录、日志或临时凭据文件。另检查 `/tmp`、`/root` 顶层名称与 `/var/tmp`、`/dev/shm`、`/tmp/pytest-of-root` 的有界候选名称，归属不明文件保留。
- 删除后核对全部指定路径不存在、三个容器及两个匿名卷均不存在，`/tmp` 顶层无 `v1-02`/`v102`/`v1_02` 候选；远程清理脚本退出码 **0**。
- 保留 `/opt/stocks-test-runtime` 及工具环境，未重新安装 editable 包；删除 c1 后其 editable 指针可能悬空，这是预期状态。
- 原有其他 **10** 个容器全部保留：`stocks-v0-03-accept-postgres`、`stocks-test-v0-03-final-s3`、`stocks-test-v0-03-fresh-postgres`、`stocks-test-v0-03-postgres`、`stocks-test-v0-02-final-postgres`、`stocks-test-v0-02-fresh-postgres`、`stocks-test-v0-02-s3`、`stocks-test-v0-02-postgres`、`monos-boundary-probe-server-1`、`monos-boundary-probe-database-1`。其他任务目录、归档、凭据文件及归属不明资源未删除。
