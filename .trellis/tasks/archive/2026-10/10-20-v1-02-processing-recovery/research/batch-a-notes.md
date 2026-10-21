# V1-02 批次 A 实施记录（持久状态 + 阶段领取 + 解析/索引事务接入）

范围：PRD R1/R2 的持久层与阶段服务；未接 Worker 槽位、未改 CLI（批次 B）。
Owner 已批准当前 PRD/design/implement。

## 新增/修改文件

| 文件 | 变化 |
| --- | --- |
| `backend/src/stocks/models.py` | `PROCESSING_STAGES`/`PROCESSING_STAGE_STATUSES` 词表；`DocumentProcessingState`（`(document_id, stage)` 主键，status/attempts/max_attempts/auto_retry/reprocess_requested/next_attempt_at/owner_token/started_at/finished_at/error/wait_reason/skip_reason/processor_version/config_fingerprint/input_batch_id/applicability）；约束：running 必有 token、非 running 无 token、failed 必有 error、skipped 必有 skip_reason、attempts>=0 且 max_attempts>=1；`ix_document_processing_states_ready` |
| `backend/alembic/versions/0001_initial.py` | 同一初始化结构新增该表、约束与索引；downgrade 同步 |
| `backend/src/stocks/processing/stages.py` | 新增阶段状态机：`create_stage_states`、`claim_stage`（依赖 EXISTS + `FOR UPDATE SKIP LOCKED` + 复核）、`apply_stage_outcome`（不提交）、`finish_stage`（提交）、`retry_stage`/`request_reprocess`/`recover_stage`、`list_stage_states`、`StageClaim`/`StageOutcome`/`StageResult`/`StageView`；阶段依赖 parse→{classify,index}、classify→facts |
| `backend/src/stocks/processing/runner.py` | `StageRegistry`/`StageProcessor` 普通 callable 契约、`execute_claimed_stage`、`run_next_stage`（领取与执行分 Session）；缺处理器→`pending`+`PROCESSOR_NOT_AVAILABLE` 且不消耗尝试；其它失败只记该阶段 |
| `backend/src/stocks/processing/processors.py` | `ParseStageProcessor`/`IndexStageProcessor` 适配既有 `process_document`/`index_document`，不复制算法；显式 applicability 键 `requires_embedding`/`requires_classification` |
| `backend/src/stocks/repository.py` | `ingest_document` 同事务创建四阶段待办；`persist_parse_result`/`persist_embedding_result` 新增可选 `stage_claim`，在结果同一事务内写阶段完成/失败（状态取自持久结果，不由调用方指定） |
| `backend/src/stocks/processing/service.py` | 普通解析只复用 `completed` 批次（失败批次真实重试）；`stage_claim` 透传；`persist_parse_reuse` 用已存批次完成阶段 |
| `backend/src/stocks/indexing/service.py` | `stage_claim`/`stage_processor_version`/`stage_config_fingerprint` 透传；仅最后一批覆盖核对与阶段完成同事务提交；复用已完成向量时完成阶段；无解析批次时记失败 |
| `backend/tests/test_processing_retry.py` | 新增 20 个用例（见下） |

## 关键实现决定

- **状态来源唯一**：阶段 status 由刚持久化的 ParseBatch.status 或 embedding coverage 推导，处理器/调用方不能声称 completed。这是实现中修掉的一个真实缺陷（原先把 `status="completed"` 直接传给阶段写入）。
- **结果与阶段同事务**：`persist_parse_result`、`persist_embedding_result` 的 `stage_claim` 在同一事务内 `apply_stage_outcome`；索引只有最后一批带 claim。中断时已提交向量保留，阶段不会假完成。
- **失败批次不再被复用**：`process_document` 仅在最近批次 `completed` 时复用，失败/unsupported 会真实重新读取原件并产生新 attempt_number。
- **能力缺失 ≠ skipped**：无处理器时 `pending` + `wait_reason=PROCESSOR_NOT_AVAILABLE`，attempts 不变；该 waiting 行仍可被领取（注册处理器后立即可跑），但 Worker 只会对已注册阶段调用 `claim_stage`，因此不会轮询空转。
- **跳过需显式依据**：`skipped` 必须带 `skip_reason`；parse 的 `applicability`（`requires_embedding`/`requires_classification`）为 false 时才把下游标 skipped。未用 MIME/空 Chunk/缺配置推断。
- **中断不自动接管**：`running` 只能 `recover_stage(confirm_stopped=True)`；有持久结果→completed，否则→pending 且保留尝试计数。旧 token 写结果被 `STAGE_NOT_OWNED` 拒绝。
- **重分类不重开 Fact**：只有 parse 新成功批次会重开下游；`request_reprocess` 只动目标阶段。
- 未新增分类/Fact 算法、无插件框架、无历史表、无迁移/兼容。

## 远程验证（测试服务器 100.120.14.84，本任务隔离目录）

目录 `/tmp/stocks-v1-02-processing-recovery-a1`，editable 指向本目录（`stocks.__file__` 已核对）。
容器：`stocks-test-v1-02-processing-recovery-postgres`（127.0.0.1:55442，日常用例）、
`stocks-test-v1-02-processing-recovery-freshdb`（127.0.0.1:55443，新库结构校验）。

| 命令 | 结果 |
| --- | --- |
| `alembic upgrade head`（全新库 55443） | 通过，`0001` |
| `alembic check`（全新库） | `No new upgrade operations detected` |
| `pytest -q tests/test_processing_retry.py` | 20 passed |
| `pytest -q tests/test_processing.py tests/test_repository.py tests/test_embeddings.py` | 51 passed |
| `pytest -q tests/test_processing_retry.py test_processing.py test_repository.py test_embeddings.py test_scheduler.py` | 107 passed |

未运行：全量回归、API/Compose、真实来源、Worker 进程 smoke（批次 B 负责）、分类/Fact 真实模型。

## 批次 B 需要沿用的资源

- 远程目录 `/tmp/stocks-v1-02-processing-recovery-a1`（可继续用；如需改名保持任务前缀）。
- 容器 `stocks-test-v1-02-processing-recovery-postgres`、`stocks-test-v1-02-processing-recovery-freshdb`。
- 持久环境 `/opt/stocks-test-runtime/tool-venvs/stock-tools`，editable 已指向上述目录。

## 剩余问题 / 交给批次 B

1. Worker 独立文档处理槽位、线程池容量、`--once` 处理摘要、设置项（`worker_max_concurrent_documents`）。
2. `stocks.processing` 统一 status/retry/reprocess CLI，旧 parse/index CLI 改为调用方。
3. 索引阶段 `requires_embedding=false` 的数值跳过只做了协议与单元验证；真实 Source Mapper 属 V1-04。
4. 文档 `docs/data.md`/`docs/execution.md`/`docs/quickstart.md` 的行为契约更新与 AC 记录。
