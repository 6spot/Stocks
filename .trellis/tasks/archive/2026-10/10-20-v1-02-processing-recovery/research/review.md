# V1-02 全范围 Check 记录（2026-10-21）

范围：本任务全部改动（批次 A 持久状态/阶段服务 + 批次 B Worker/CLI/文档），含未跟踪新增模块与测试。
审查依据：PRD R1–R5/AC-1–AC-4、`design.md`、`implement.md`、`check.jsonl` 所列 spec 与领域文档、
`research/batch-a-notes.md`、`research/batch-b-notes.md`。

未纳入：Decision Provider/分类算法（V1-03）、Fact 表与抽取/Source Mapper（V1-04）、真实模型 PA-1、PA-2、
变化水位（V1-05）、Web 控制台、ARM 部署验收。无关的 `.agents/`、`.pi/`、`workflow`、模型路由与 Pi 任务改动未触碰。

## 审查结论

批次 A/B 的事务边界、`(document_id, stage)` 状态推导、`FOR UPDATE SKIP LOCKED` 领取、惰性 Embedding 路由、
Owner 显式确认后的恢复与 CLI 无平行路径等主要设计均落实，未发现需要推翻的方案问题。首轮修复 7 项确认缺陷；
主会话复审又发现 4 项与 `expire_on_commit=False` 下缓存实体相关的并发/身份缺陷（见第二轮小节），均已修复并补真并发用例。
全部在本任务范围内。批次 B 已记录全量 258 passed 与 34/34 真实子进程 smoke，本次未重复全量套件。

## 确认并修复的缺陷

| # | 缺陷 | 影响 | 修复 |
| --- | --- | --- | --- |
| 1 | `apply_stage_outcome` 对**任何**结局都清除 `reprocess_requested` | 显式重处理失败后，Worker 的普通重试可能以原有可用向量/批次"完成"，Owner 请求的重建从未真正发生 | 只在 `completed`/`skipped` 清除；`failed` 保留。`retry_stage` 也不再清除该请求 |
| 2 | `recover_stage` 只看持久结果就判 completed | 被中断的显式重建在旧结果仍存在时被误报完成，Owner 的重建请求静默丢失 | 先看 `reprocess_requested`：为真则回 `pending` 保留请求；否则才用 `_has_durable_result` 判完成 |
| 3 | 索引分批写入只在最后一批带 `stage_claim` | 中间向量批次完全不做所有权校验；被恢复并重新领取后，旧执行者仍可继续写向量 | `persist_embedding_result` 增加 `stage_final_batch`；非最后批走新增的 `assert_stage_owned`，仍与向量同一事务 |
| 4 | 跳过只从 `(parse → index/classify)` 硬编码判断，且 `facts` 永远 `pending` | 依赖已跳过阶段的下游阶段永久 `pending` 且无等待原因，能力缺口不可见；已完成的可用结果有被覆盖风险 | 改为 `DIRECT_SKIP_APPLICABILITY` 表 + 传递式继承 `DEPENDENCY_SKIPPED`；仅改 `pending`/`failed` 行，`completed`/`running` 不动；新增 `_reopen_dependency_skips`，上游完成后重新打开 |
| 5 | Worker 所有文档槽位共用一个 token `{process_token}-doc` | 并发文档槽位无法被所有权 fence 区分，Owner 恢复时可能接受错的执行者 | 每槽独立 `{process_token}-doc-{uuid4().hex}` |
| 6 | `--once` 退出码忽略文档阶段失败 | 部署/验收脚本无法区分"空积压"与"处理真的失败" | `processing.status == "failed"` 计入退出码 1；smoke 期望同步更新 |
| 7 | 手动 `parse`/`index` 默认目标只按状态筛选 | 新入库但未解析的 Document 会占满 1–200 上限，全部报 `STAGE_NOT_CLAIMABLE`，真正的可执行积压被挤掉 | `list_stage_states(..., dependencies_completed=True)` 在查询内排除不可领取行，不事后过滤 |

## 顺带清理

- `execute_next_document_stage` 现在自含异常隔离：`--once` 在槽位守卫之外直接调用它，一个阶段缺陷不再逃逸成 Worker 失败。
- 删除未被任何调用方读取的 `StageOutcome.output`（处理器仍在 `ParseSummary`/`IndexSummary` 中带输出），以及无调用方的 `release_stage_waiting`。
- 解析失败的等待原因改为按持久化 `parse_state.status == "unsupported"` 推导，不再匹配已不存在的错误码字符串 `PARSE_UNSUPPORTED_CONTENT_TYPE`（真实解析器产出 `UNSUPPORTED_CONTENT_TYPE`/`INVALID_CONTENT_TYPE`，原判断永不命中）。
- `_apply_downstream` 中的 `facts` 分支收敛为 `PARSE_DEPENDENT_RERUN_STAGES` 单一事实来源，避免"parse 重开 facts"的旧路径复活。

## 新增验证用例（`backend/tests/test_processing_retry.py`）

| 用例 | 覆盖 |
| --- | --- |
| `test_failed_explicit_reprocess_keeps_the_request_for_a_real_retry` | 修复 1 |
| `test_recovery_does_not_report_completion_for_an_interrupted_reprocess` | 修复 2 |
| `test_intermediate_vector_batch_rejects_a_released_owner` | 修复 3（多 Chunk、`embedding_batch_size=1` 真分批） |
| `test_applicability_skip_propagates_and_is_not_revoked_by_completion` | 修复 4：传播、`NOT_APPLICABLE`/`DEPENDENCY_SKIPPED`、不消耗尝试、不撤销 completed |
| `test_skipped_dependency_reopens_once_its_dependency_completes` | 修复 4：上游真实完成后重新可执行 |
| `test_downstream_wait_reason_clears_when_parse_recovers` | 下游等待原因在上游真实成功后清除，并跟随新的 `input_batch_id` |
| `test_unsupported_parse_reason_distinguishes_from_a_repairable_failure` | 等待原因按持久化 `parse_state.status` 推导，`unsupported` 与可修复失败可区分 |

`backend/tests/test_worker_processing.py` 新增
`test_manual_index_without_ids_targets_only_actionable_documents` 覆盖修复 7（默认目标只选可领取行）。

`v1_02_processing_smoke.py` 的 `second --once exit 0` 改为按修复 6 断言 exit 1 + `processing.status == "failed"`。

## 第二轮 Check：并发/缓存实体缺陷（最终收敛）

主会话代码审查在首轮 Check 之后发现两条未被首轮用例覆盖的真实风险路径。根因是 `db.session_factory`
使用 `expire_on_commit=False`，而若干读取沿用 Session 缓存实体；首轮 `test_intermediate_vector_batch_rejects_a_released_owner`
只在全新 Session 里伪造过期 claim，没有真的切换所有权，因此未触及这两条路径。

| # | 缺陷 | 影响 | 修复 |
| --- | --- | --- | --- |
| 8 | `_lock_owned_state`（及 `claim_stage`/`retry_stage`/`request_reprocess`/`recover_stage`）的 `select(...).with_for_update()` 未带 `populate_existing` | Session 已有旧阶段实体时会校验旧 `status`/`owner_token`，而不是锁住的行；Owner 恢复后旧执行者仍可写入 | 全部加 `execution_options(populate_existing=True)`，以锁定行为唯一权威 |
| 9 | `persist_embedding_result` 在 Document 行锁内用 `session.get(DocumentParseState, ...)` 读可能已缓存的解析指针；复用捷径与 `index_document` 起始读取同样缓存 | 另一 Session 完成显式重解析后，旧 index 执行者仍看到旧 `current_batch_id`，把旧批次向量/阶段 `completed` 提交，**新当前批次永久无人索引** | 行锁内以 `populate_existing` 重读权威指针；批次不再是当前成功批次时对已领取阶段报 `STAGE_NOT_OWNED`（不写向量/状态），未领取调用仍报 `ValueError`；复用捷径与 index 起始读取均重读 |
| 10 | `_apply_downstream` 对 `running` 的下游阶段一律 `continue` | 新当前批次下旧输入的运行中 index 既不被作废也不可重新领取，旧执行者最终把阶段标成完成，新批次永不索引 | 新批次且 `input_batch_id` 不同时，在同一事务内释放所有权、回 `pending`、`attempts=0`、指向新批次；旧写入被 7c fence 拒绝 |
| 11 | `_index_stage_outcome` 未写处理器/配置身份；`IndexStageProcessor` 复用捷径的 `StageOutcome` 缺 `applicability` | 成功索引后阶段缺少 R1 要求的处理器/模型版本与配置指纹；复用路径不记录适用性 | 为 `persist_embedding_result` 增加 `stage_processor_version`/`stage_config_fingerprint` 并写入完成/失败结局；复用捷径补 `applicability` |

另修一处早前遗漏：`index_document` 在“无当前解析批次”分支直接返回失败摘要而未落阶段结局，导致已领取的 index 阶段永久 `running`；现改为同事务写入 `failed`。

### 新增真并发用例

| 用例 | 覆盖 |
| --- | --- |
| `test_stale_index_owner_cannot_write_after_owner_recovery` | 缺陷 8：旧 Session 强引用已领取阶段实体与解析指针，第二 Session 完成 `recover_stage(confirm_stopped=True)` + 新 token 领取；旧写入必须 `STAGE_NOT_OWNED`，且新 owner 的 `running` 状态、`attempts=0` 与无向量不被覆盖 |
| `test_new_current_batch_supersedes_a_running_index_attempt` | 缺陷 9/10：在 `embed()` 回调中由第二 Session 提交显式重解析的新当前批次；旧批次不得完成当前 index，阶段必须 `pending` + 指向新批次且 `attempts=0`，旧 Chunk 仍可读；下一次领取索引**新**批次并写入 `processor_version`/`config_fingerprint` |

两条用例已在**修复前**源码上验证会失败（`DID NOT RAISE ProcessingError`；`assert 'completed' != 'completed'`），修复后通过，因此确实覆盖被报告的路径。

## 文档/规范同步

- `docs/data.md`：跳过语义补 `DEPENDENCY_SKIPPED` 与"跳过不吃预算、不撤销可用结果"；中断小节补"包括分批中间向量批次"与"以数据库最新行为准"；新增"新当前批次使旧输入的运行中阶段作废"；重处理边界补"请求只在真正产出结果的尝试成功后清除"。
- `.trellis/spec/backend/worker-guidelines.md`：新增 7c（每批所有权校验 + `expire_on_commit=False` 下缓存实体不是授权）、7d（新当前批次作废旧输入的运行中阶段）、7e（`--once` 退出码暴露阶段失败）、7f（`execute_next_document_stage` 不抛异常），并在 7a 说明每槽独立 token；`Tests Required` 补本轮新增用例类别与"必须用两个独立 Session 真的切换所有权"的写法要求。
- 其余领域契约（`docs/execution.md`、`docs/validation.md`、`docs/quickstart.md`）已有描述与本轮实现一致，未改动。

## 实际执行的验证（全部在测试服务器 `100.120.14.84`）

任务隔离目录 `/tmp/stocks-v1-02-processing-recovery-c1`；editable 从批次 B 的 `-b1` 重新指向本目录并核对
`stocks.__file__`；复用容器 `stocks-test-v1-02-processing-recovery-postgres`（127.0.0.1:55442）、
`...-freshdb`（55443）、`...-s3`（18346）。密码从容器环境注入，未写入记录或命令行。

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `pytest -q tests/test_processing_retry.py` | 0 | **31 passed** |
| `pytest -q tests/test_worker_processing.py` | 0 | **23 passed** |
| `pytest -q` 受影响范围：retry + worker_processing + processing + embeddings + repository + collection + scheduler + report_api | 0 | **185 passed, 1 warning**（starlette 弃用告警） |
| `alembic upgrade head` + `alembic check`（55443 清空重建 schema 后的全新库） | 0 | `0001`，`No new upgrade operations detected` |
| `python tests/v1_02_processing_smoke.py`（真实 `worker --once`、`stocks.processing` 子进程、真实 PG+S3） | 0 | **34/34 PASS**，`summary: OK` |

结构验证从**全新空库**执行（`DROP SCHEMA public CASCADE` 后 `upgrade head` + `check`），未测试旧库升级。

### 第二轮（并发修复）实际执行的验证

同一隔离目录 `/tmp/stocks-v1-02-processing-recovery-c1`（重新解包当前工作区源码与测试）、同一组复用容器；
未重建数据库或 schema（本轮无结构变更）。

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `pytest -q tests/test_processing_retry.py -k "stale_index_owner or supersedes_a_running_index"`（修复前源码 + 新用例，`PYTHONPATH` 指向修复前 `src`） | 1 | **2 failed**（复现被报告的两条缺陷） |
| 同一 `-k` 选择（当前源码） | 0 | **2 passed** |
| `pytest -q tests/test_processing_retry.py tests/test_worker_processing.py tests/test_embeddings.py` | 0 | **66 passed** |
| `pytest -q tests/test_processing.py tests/test_repository.py` | 0 | **40 passed** |
| 最终源码全受影响集（上述 5 个文件） | 0 | **106 passed** |
| `python tests/v1_02_processing_smoke.py`（最终源码，真实 `worker --once` + CLI 子进程 + 真实 PG/S3） | 0 | **34/34 PASS**，`summary: OK` |

本轮无数据库结构变更，未重跑 `alembic`；未重跑全量套件（按指令不重复）。

## 第三轮 Check（最终 NARROW 复审，2026-10-21）

范围收敛：只复核第二轮并发修复涉及的 `stages.py`、`repository.py`、`indexing/service.py`、`processing/processors.py`、`processing/runner.py` 与两条新回归用例，不重复全任务审计与全量测试。依据主会话给出的四项验收焦点：`expire_on_commit=False` 下的新鲜度、真实所有权切换（非伪造 token）、新当前批次作废/重领、已完成 facts 不被重置；并单独核对锁顺序、阶段结局/复用路径、处理器/模型身份持久化与事务回滚。

### 逐项确认结论

| 焦点 | 结论 | 代码证据 |
| --- | --- | --- |
| 锁顺序 | 一致且无死锁 | `persist_embedding_result` 与 `persist_parse_result` 都先 `SELECT ... FOR UPDATE` 锁 `documents` 行，再读/写解析指针与阶段行；所有阶段写入（`_lock_owned_state`、`claim_stage`、`retry_stage`、`request_reprocess`、`recover_stage`）只锁 `document_processing_states` 单行，没有任何路径先锁阶段行再锁 Document 行，因此不存在反向获取 |
| 新鲜度（`expire_on_commit=False`） | 全部决策读已改为权威行读取 | 5 处 `with_for_update()` 均带 `populate_existing=True`；`persist_embedding_result` 行锁内重读 `document_parse_states`；`index_document` 起始与复用捷径、`IndexStageProcessor` 复用捷径、`runner._fresh_stage_state` 同样重读。本轮另补 `runner` 的 index 前置判断（见下） |
| 真实所有权切换 | 非伪造 token | `test_stale_index_owner_cannot_write_after_owner_recovery` 用两个独立 `Session`：第二 Session 真的执行 `recover_stage(confirm_stopped=True)` 并让第三个 Session 用新 token `claim_stage`，旧 Session 只是持有已领取实体；断言旧写入 `STAGE_NOT_OWNED`、新 owner 的 `running`/`attempts=0`/无向量不被覆盖 |
| 新当前批次作废与重领 | 成立，且不吃重试预算 | `_apply_downstream` 对 `running` 且 `input_batch_id` 不同的受 parse 影响阶段，在同一事务内回 `pending`、`owner_token=None`、`attempts=0`、指向新批次；`test_new_current_batch_supersedes_a_running_index_attempt` 在 `embed()` 回调里由第二 Session 提交显式重解析，断言旧批次不完成、`pending`+新批次+`attempts=0`、旧 Chunk 仍可读，随后新领取索引新批次并写入 `processor_version`/`config_fingerprint` |
| 已完成 facts 不重置 | 成立 | `PARSE_DEPENDENT_RERUN_STAGES` 只含 `classify`/`index`，`facts` 在 parse 成功路径被显式 `continue`；`test_reparsing_reopens_index_but_keeps_completed_facts` 与 smoke 的 “a reparse keeps completed Facts completed” 双重覆盖 |
| 阶段结局/复用路径 | 身份与适用性齐全 | `persist_embedding_result` 接收 `stage_processor_version`/`stage_config_fingerprint` 并写入完成/失败结局；`IndexStageProcessor` 复用捷径补 `applicability`，并在复用前重读 embedding/parse 状态 |
| 事务回滚 | 无“结果已提交、阶段未完成” | 解析批次、Chunk、解析指针与阶段结局同一 `session.commit()`；向量批与阶段结局同一事务，中间批只写向量（所有权已在事务开头 fence）；`apply_stage_outcome` 遇 `STAGE_NOT_OWNED` 由 `runner` 先 `rollback()` 再读持久行报告，不把失败写进他人尝试 |

### 本轮唯一修复（最小范围）

`backend/src/stocks/processing/runner.py`：`execute_claimed_stage` 里 index 阶段的“是否有当前解析批次”前置判断原用 `session.get(DocumentParseState, ...)`，属决策读却会命中 Session 缓存实体。按 7c 的同一原则改为 `select(...).execution_options(populate_existing=True)`。触发场景：同一 Session 先读到旧指针、另一 Session 提交新批次后该判断仍可能把已领取的 index 阶段写成 `pending` + `PARSE_UNAVAILABLE`，而该等待原因之后没有路径会重开它。这是与本轮修复同类的新鲜度缺口，非新设计问题。

### 文档准确性修正

指令指出的两处不准确表述已改：

1. **状态推导**（`docs/data.md` 统一处理状态段、`acceptance.md` 第 1 项）：原文把五个状态整体说成“由刚持久化的派生结果推导”。实际只有 `completed`/`failed` 来自派生结果（ParseBatch 状态、向量覆盖），`pending`/`running` 表示排队、等待原因与当前领取者，`skipped` 来自处理器显式适用性及依赖传播。已按此分述。
2. **研究 Run 的 Embedding 配置**（`docs/data.md` V1-02 后台执行段、`docs/quickstart.md`、“`acceptance.md` 第 8 项）：原文称 Embedding 配置损坏“不会阻止采集/研究 Run”。实际 `_model_snapshot` 在 Task 含 `select_evidence` 时对研究 Run 调用 `load_embedding_targets` 快照路由，`_embedding_targets_from_snapshot` 在执行时也会重读并核对；只有**采集** Run 不解析 chat/embedding 路由。已改为“文档处理的 Embedding 路由…不会阻止解析，也不会阻止采集 Run；研究 Run 的 Evidence Selection 仍按既有规则读取并核对自身路由快照”，并把 quickstart 的 “Run collection” 收窄为 “a collection Run”。

### 本轮实际执行的验证（测试服务器 `100.120.14.84`）

隔离目录 `/tmp/stocks-v1-02-processing-recovery-c1`（仅覆盖 `backend/src/stocks` 与 `backend/tests` 的当前工作区文件，`stocks.__file__` 指向该目录）；复用容器
`stocks-test-v1-02-processing-recovery-postgres`（55442）、`...-s3`（18346）。密码从容器环境注入，未写入记录或命令行。无结构变更，未重建数据库、未跑 `alembic`。

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `pytest -q tests/test_processing_retry.py tests/test_worker_processing.py` | 0 | **56 passed** |
| `pytest -q tests/test_embeddings.py tests/test_processing.py tests/test_repository.py` | 0 | **50 passed** |
| 上述 5 文件合计 | 0 | **106 passed**（与主会话给出的 c1 基线一致） |
| `python tests/v1_02_processing_smoke.py`（真实 `worker --once` + `stocks.processing` 子进程 + 真实 PG/S3） | 0 | **34/34 PASS**，`summary: OK` |

### 最终结论

**无遗留阻塞项。** 第二轮报告的四项并发/缓存实体缺陷均已真实修复并被真并发用例覆盖；本轮补的一处新鲜度缺口同属该原则、已修复并通过受影响用例与真实子进程 smoke。

### 需主会话处理（本轮未改，避免并发编辑）

- `.trellis/spec/backend/worker-guidelines.md` 7b 仍写“构建 registry、解析 Document 和执行采集/研究 Run 都不读 `config/models.yaml` 的 embedding 配置”。前半句（构建 registry、解析 Document、采集 Run）成立，但**研究 Run 会读**（`_model_snapshot` → `load_embedding_targets`，执行时 `_embedding_targets_from_snapshot` 再审）。主会话正在改该文件，请把 7b 的表述收窄为“不读文档处理的 embedding 路由；研究 Run 的 Evidence Selection 按自身路由快照读取并核对”。
- `docs/execution.md` 第 66 行“采集 Run … 不解析 chat/embedding 路由”本身正确，无需改动。
- `docs/validation.md` V1-02 边界段未出现被纠正的两处表述，无需改动。

### 未完成 / 受限（不变）

- 按指令不重跑批次 B 已记录的全量 258 项套件，也不重跑首轮的 8 文件范围（含 scheduler/report_api/collection，本轮未触及）；本轮只跑 5 个受影响文件（retry/worker_processing/embeddings/processing/repository）+ smoke。
- 分类/Fact 算法与真实 Decision Provider、真实 Source Mapper 仍未实现（V1-03/V1-04），`classify`/`facts` 继续显示 `pending` + `PROCESSOR_NOT_AVAILABLE`。
- 数值资料“无需向量”的跳过协议只由固定处理器验证，真实 Source Mapper 属 V1-04。
- PA-1 真实模型闭环、PA-2 既有 HTML 缺陷、Compose 构建/ARM 部署、Web 控制台、V1-05 变化水位不在本任务，未验证也未关闭。
- 未提交、未推送、未归档；第三轮 Check 结束时保留的远程任务资源已在下述授权清理中移除。
- 未触碰无关的 `.trellis/tasks/10-21-pi-trellis-inline-workflow`。

## 授权清理补记（2026-10-21 10:49:41 UTC）

清理前按容器名称、镜像、挂载路径与归档成员名确认归属。未发现本任务遗留 Worker/smoke 进程。
`docker rm -f -v` 精确删除 `stocks-test-v1-02-processing-recovery-postgres`（55442）、
`stocks-test-v1-02-processing-recovery-freshdb`（55443）、`stocks-test-v1-02-processing-recovery-s3`（18346），退出码 0；
两个数据库匿名卷 `37c5c576f9940fb974dfc9370de86f68db09bbd7b9741156ee468892e2fa80bb`、
`eee3dc3fda460d093b14a366b64bf14355e73faf8f3b13cea6110d29e22a6379` 同时删除。

逐路径删除 `/tmp/stocks-v1-02-processing-recovery-a1`、`-b1`、`-c1`、`-s3-data`，
以及 `/tmp/stocks-v102-a.tgz`、`/tmp/stocks-test-v1-02-b.tgz`、
`/tmp/stocks-v1-02-check-c1.tgz` 至 `/tmp/stocks-v1-02-check-c15.tgz`（整数 1–15，共 17 份归档）。
任务目录内的 smoke 脚本/临时配置/缓存随目录移除；未发现独立的本任务修复前快照目录、日志或临时凭据文件。
候选核查限于 `/tmp`、`/root` 顶层名称及 `/var/tmp`、`/dev/shm`、`/tmp/pytest-of-root` 有界名称检查，未知归属不删除。

删除后脚本确认全部指定路径、三个容器、两个匿名卷均不存在，`/tmp` 顶层无本任务候选；退出码 0。
原有其他 10 个容器全部保留（精确名称见 acceptance.md），其他任务及归属不明文件保留；
`/opt/stocks-test-runtime` 与公共工具环境保留，未修复删除 c1 后预期悬空的 editable 指针。
本次仅补清理证据与 implement.md 勾选，无新增测试、源码/spec/路由修改，无提交、推送或归档。
