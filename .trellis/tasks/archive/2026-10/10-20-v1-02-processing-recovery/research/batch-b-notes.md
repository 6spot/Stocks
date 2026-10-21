# V1-02 批次 B 实施记录（Worker 文档槽位 + 统一 CLI + 文档）

范围：PRD R2/R3 的执行侧与 Owner 入口；未接分类/Fact 算法（V1-03/V1-04），未做 Web、变化水位、部署验收。
批次 A 的持久状态、领取、事务接入与 107 项远程用例继续有效，本批次只在其上接入 Worker/CLI 并修正下游重开边界。

## 新增/修改文件

| 文件 | 变化 |
| --- | --- |
| `backend/src/stocks/settings.py` | `worker_max_concurrent_documents`（默认 1，1–16） |
| `backend/src/stocks/processing/stages.py` | `PARSE_DEPENDENT_RERUN_STAGES`（parse 只重开 classify/index，**不重开 facts**）；`ready_stage_ages`；`reopen_stage`（Owner 统一入口：running 先经 `recover_stage` 识别已提交结果，再 retry/reprocess）；`list_stage_states` 稳定排序（`collected_at DESC, document_id, stage`）；`claim_stage(document_ids=...)`；清理 `_apply_downstream` 未用参数 |
| `backend/src/stocks/processing/runner.py` | `StageRegistry.index_factory` + `available_stages()`（惰性 Embedding 路由）；`for_stage` 的路由错误降级为 `EMBEDDING_ROUTING_UNAVAILABLE` 且 `auto_retry=False`；`run_stage_now`、`run_stages_for_documents`、`describe_stages`、`run_next_document_stage`（按最老可领取 Document 选阶段，避免新 parse 饿死旧 index）；`StageExecution.reprocess_requested` |
| `backend/src/stocks/processing/processors.py` | `load_index_processor`（仅在真正执行 index 时读 `config/models.yaml`）、`build_stage_registry`（注册 parse + 惰性 index；classify/facts 保持未注册） |
| `backend/src/stocks/processing/__main__.py` | 重写为统一 CLI：`status` / `retry` / `reprocess` / `parse` / `index`，全部走同一 claim + token 路径 |
| `backend/src/stocks/indexing/__main__.py` | 改为统一阶段服务的调用方（`--reindex` → reprocess），不再有平行执行路径 |
| `backend/src/stocks/processing/service.py` | 删除已无调用方的 `process_documents`（被 CLI 阶段路径取代） |
| `backend/src/stocks/indexing/service.py` | 删除已无调用方的 `index_documents` 及无用 `select` 导入 |
| `backend/src/stocks/worker.py` | `max_concurrent_documents`、`stage_registry`、`processing_registry()`；`_claim_and_execute_document`；`_run_slot(..., claim=...)`；`run_once` 增加 `processing` 摘要；线程池 `run_slots + document_slots + 1`；`--max-concurrent-documents` 与 `DOCUMENT_CONCURRENCY_INVALID` |
| `backend/tests/test_worker_processing.py` | 新增 22 个用例（Worker 槽位、惰性路由、status/retry/reprocess/parse/index CLI、并发参数、饥饿） |
| `backend/tests/test_processing_retry.py` | 新增 4 个用例：重解析只重开 index/classify 且保留 completed facts；切换 Embedding 模型不自动重开；显式重索引复用 Chunk；重解析失败保留当前批次与 Chunk |
| `backend/tests/test_collection.py` | StubSession 忽略 `DocumentProcessingState`（ingest 现在同事务创建待办）并补 `add_all` |
| `backend/tests/test_processing.py` | 用阶段语义的用例替换已删除的 `process_documents` 边界用例 |
| `backend/tests/v1_02_processing_smoke.py` | 新增有界进程 smoke（非 pytest 收集） |
| `.env.example`、`docs/data.md`、`docs/execution.md`、`docs/quickstart.md`、`docs/validation.md`、`.trellis/spec/backend/worker-guidelines.md` | 设置、契约、运行说明与工程规范 |

## 关键实现决定

- **独立文档槽位**：Run 槽位与文档槽位各自常驻，共用 `_run_slot` 的有界等待/退避/停机单次领取语义；线程池显式覆盖两类槽位 + 调度轮，因此任一侧饱和都不会饿死另一侧或调度。
- **只领取已注册阶段**：`run_next_document_stage` 只遍历 `registry.available_stages()`，未接入的 classify/facts 不会被轮询；`describe_stages` 仍把它们显示为 `pending` + `PROCESSOR_NOT_AVAILABLE` + `processor_available=false`，能力缺口可见且不伪装成 skipped/completed。
- **按最老 Document 选择阶段**：固定 parse-first 优先级会让持续入库的新 Document 永久饿死旧 Document 的 index。`ready_stage_ages` 取各阶段最老可领取 Document 的 `collected_at`，最小的先执行；同龄时按 `DOCUMENT_STAGE_ORDER` 稳定排序。
- **Embedding 路由惰性**：`index_factory` 只在 index 阶段真正执行时调用 `load_embedding_client`。构建 registry、解析、采集/研究 Run 都不读 embedding 配置；损坏的路由只让该次 index 尝试失败（安全错误、`auto_retry=False`，不烧预算）。smoke 用故意损坏的 models.yaml 验证了解析照常完成。
- **下游重开修正（批次 A 遗留）**：`_apply_downstream` 原来对 parse 新成功批次重开全部依赖阶段，包含 facts。按 PRD R4，现在只重开 classify 与 index；completed facts 保持 completed（smoke 与单测各验证一次）。
- **Owner 恢复入口**：`reopen_stage` 对 `running` 先 `recover_stage`，若已提交结果则识别为 completed（不再重跑），否则回 pending 保留尝试预算；之后才按 retry/reprocess 语义重开。CLI 的 `--confirm-stopped` 是显式前提，无自动接管。
- **CLI 无平行路径**：`parse`/`index`/`retry --execute`/`reprocess --execute` 都经 `run_stage_now`（claim + token fence + 结果事务）。普通 `parse`/`index` 只处理 `pending|failed`，已完成阶段以 `completed_is_error=False` 报为 unchanged；`retry` 拒绝已完成阶段，`reprocess` 才重开。
- 删除 `process_documents` / `index_documents`，不留兼容壳。

## 远程验证（测试服务器 100.120.14.84，本任务隔离目录）

目录 `/tmp/stocks-v1-02-processing-recovery-b1`（editable 已指向该目录，`stocks.__file__` 已核对）。
复用容器：`stocks-test-v1-02-processing-recovery-postgres`（127.0.0.1:55442）、
`...-freshdb`（127.0.0.1:55443）；本批次新建 `...-s3`（127.0.0.1:18346，独立数据目录 `/tmp/stocks-v1-02-processing-recovery-s3-data`）。

| 命令 | 结果 |
| --- | --- |
| `alembic upgrade head`（全新库 55443） | 通过（`0001`，无结构变更） |
| `alembic check`（全新库 55443） | `No new upgrade operations detected` |
| `pytest -q tests/test_worker_processing.py tests/test_processing_retry.py` | 46 passed |
| `pytest -q tests/test_processing.py tests/test_embeddings.py tests/test_repository.py tests/test_scheduler.py tests/test_report_api.py` | 87+7 passed |
| `pytest -q tests/test_collection.py` | 38 passed（修 StubSession 后） |
| `pytest -q tests/`（全量，含本批次新增用例） | 258 passed, 1 warning（starlette 弃用告警） |
| `python tests/v1_02_processing_smoke.py`（真实 `worker --once`、`stocks.processing` 子进程、真实 PG+S3） | 34/34 PASS，summary OK |

smoke 覆盖：入库生成四阶段待办 → `status` 显示 classify/facts 未接入但 pending+原因 → `worker --once`
（models.yaml 故意损坏）完成 parse 且 index 只失败自己 → 重复 `--once` 不重跑已完成 parse → 注入固定
Embedding 后 index completed 且 Chunk 未变 → 无 `--confirm-stopped` 的 retry 被拒 → 确认后恢复识别已提交
结果并清 token → `retry` 拒绝已完成 parse、`reprocess` 重开 → 再次 `--once` 产生新成功批次且旧批次保留 →
reparse 后 completed facts 仍 completed 且 index 对新批次重开。

未运行：真实模型 Provider（PA-1 仍待联合验收）、真实 Source Mapper/Fact（V1-04）、Compose 构建与 ARM 部署、
Web 控制台、变化水位。

## 剩余问题 / 交给 Check

1. 分类与 Fact 处理器仍是未接入能力；`status` 与 smoke 已证明其可见性与不伪造完成。
2. 数值资料“无需向量”的 skip 协议只有固定处理器验证；真实 Source Mapper 属 V1-04。
3. PA-1/PA-2、Compose/ARM 部署、Web 控制台、V1-05 变化水位不在本任务范围。
4. 未做提交/推送/归档；验收勾选与最终提交由主会话在 Check 后执行。
