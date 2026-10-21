# Research: select_evidence、Evidence Package 与可续扫游标的执行接入

- Query: 复核 `select_evidence`、Evidence Package、retry、Run/Step 事务，以及增量变化扫描以最小改动接入的位置；给出与现有 Schema 一致的扫描进度形状和原子推进风险。
- Scope: internal
- Date: 2026-10-21

## Files Found

- `backend/src/stocks/changes.py`: committed counter, window, page, candidate grouping and cursor helpers.
- `backend/src/stocks/models.py`: `TaskRun` window fields, `EvidencePackage`, `ChangeCounter`, `ChangeRecord`, `ResearchCursor`.
- `backend/src/stocks/execution/service.py`: Run binding, step execution/commits, Analysis/Report persistence, retry and final state.
- `backend/src/stocks/research/service.py`: current Evidence Selection and package persistence.
- `backend/src/stocks/repository.py`: Document ingestion, parse/index persistence and change-recording call sites.
- `backend/src/stocks/classification/service.py` and `backend/src/stocks/extraction/service.py`: classification/Fact change-recording call sites.
- `backend/alembic/versions/0001_initial.py`: current initial schema for the change and cursor tables.
- `backend/tests/test_change_watermarks.py` and `backend/tests/test_research.py`: existing helper/integration coverage and its limits.

## Findings

### 1. 当前已存在的水位基础与真正的业务缺口

- 已实现一个共享计数行 `change_counters.watermark`，变化写入在同一事务内执行 `UPDATE ... RETURNING` 并一直持有行锁到提交；读端把可见计数作为已提交上界。该机制满足“不能使用时间字段、普通序列或事务开始时间”的核心设计，见 `backend/src/stocks/changes.py:77-136`、`backend/src/stocks/models.py:793-813`。
- 已实现 `change_records`（`watermark / kind / document_id / fact_id / recorded_at`）、`research_cursors(task_id, scope_fingerprint)`、`TaskRun.change_watermark_start/end`、`scope_fingerprint`、`change_scan_completed`，见 `backend/src/stocks/models.py:162-177`、`backend/src/stocks/models.py:816-876`，初始化结构在 `backend/alembic/versions/0001_initial.py:104-107`、`backend/alembic/versions/0001_initial.py:678-740`。
- 研究 Run 执行前会调用 `open_window` 固定 `(start,end]`，将窗口写入 Run，见 `backend/src/stocks/execution/service.py:632-635`、`backend/src/stocks/execution/service.py:835-862`。`open_window` 取当前已提交计数，存在游标时以游标为起点；首次运行从 0 开始，或按 `initial_lookback` 截取，见 `backend/src/stocks/changes.py:182-224`。
- 变化写入点已经覆盖入库、解析完成、分类完成、索引完成、Fact 映射和 Fact 抽取：`backend/src/stocks/repository.py:198-230`、`backend/src/stocks/repository.py:1000-1009`、`backend/src/stocks/repository.py:525-535`、`backend/src/stocks/classification/service.py:337-345`、`backend/src/stocks/extraction/service.py:618-642`、`backend/src/stocks/extraction/service.py:874-905`。这些记录与各自派生结果在同一事务提交。
- **未闭环的关键点**：`select_evidence` 仍只执行语义背景查询 `semantic_search`，没有读取 `ChangeRecord`/`changes_in_window`/`changed_candidates`，见 `backend/src/stocks/research/service.py:96-180`。仓库中也没有公开的 `repository.search_changes`；`changes.py` 提供的是内部 helper。因此当前 Run 可以固定窗口并推进游标，但该窗口的内容从未进入 Evidence Package。

### 2. 当前 select_evidence 与 Evidence Package 行为

- `select_evidence` 对每个 query 调 `semantic_search`，只选择 current parse batch 的 Chunk，并按 query 记录角色、返回数、截断和缺口；发现/筛选/选中数是每次查询集合内的统计，见 `backend/src/stocks/research/service.py:96-211`。
- Evidence Package 的持久字段是 objective、scope、as_of、规则版本、预算、query 记录、选中 Chunk 引用、覆盖统计和 created_at；没有 `changes_window` 或 scan page position，见 `backend/src/stocks/models.py:188-236`、`backend/src/stocks/research/service.py:212-232`。
- `select_evidence` 在内部 `flush` 后直接 `commit`，见 `backend/src/stocks/research/service.py:233-243`。因此执行器的 `_run_evidence_selection` 返回时，Evidence Package 已经独立提交；之后所有权 fence 失败也不会撤回它，见 `backend/src/stocks/execution/service.py:688-710` 和 `backend/src/stocks/execution/service.py:672-680` 的既有说明。
- Evidence Package 输出目前只有 package id、queries、items、coverage、truncated 等，没有窗口和续扫位置，见 `backend/src/stocks/execution/service.py:1278-1290`。`_complete_change_scan` 只读取 `final_output["change_scan"]["completed"]`；缺失或形状不对时应视为未完成，当前却默认 `completed=True`，见 `backend/src/stocks/execution/service.py:865-893`。

### 3. Run/Step 的真实提交边界

- `execute_claimed_run` 在开始执行前已通过 `claim_run` 提交 `running + process_token`，见 `backend/src/stocks/execution/service.py:539-568`。执行入口先 `rollback` 清理 Session 状态，再读取 Run，见 `backend/src/stocks/execution/service.py:571-591`。
- 每个 Step 成功后由 `_persist_step` 写 StepResult、flush，然后立即 commit；Analysis 和 Report 作为 `_analysis_and_report` 的附加对象也在这个 commit 中，见 `backend/src/stocks/execution/service.py:1059-1098`、`backend/src/stocks/execution/service.py:1727-1801`。所以当前“Analysis/Report + succeeded StepResult”是原子的，这一点成立。
- Run 的最终成功状态随后由 `_finish_run` 单独 commit，见 `backend/src/stocks/execution/service.py:802-817`、`backend/src/stocks/execution/service.py:963-1000`。`_complete_change_scan` 与 `_finish_run` 虽然在同一调用序列中，但前者只是修改 Session 中的 `run.change_scan_completed` 并调用 `advance_cursor`，真正 commit 发生在 `_finish_run`，见 `backend/src/stocks/execution/service.py:865-893`。因此 cursor 与 Run 最终状态可以一起提交，但**不与最终 Research Agent Step、Analysis、Report 同事务**；仅两步骤的研究 Task 也至少先提交一次 Step/Report，再单独提交 Run/cursor。
- 权限 fence `_assert_run_ownership` 在 `_persist_step` 写结果前持有 Run 行锁，见 `backend/src/stocks/execution/service.py:1003-1029`；但该锁随 Step commit 释放，之后才进行最终状态/游标提交。若 Owner 在这两个 commit 之间结束 Run，Report 已保存而 cursor 不推进；若进程在 Report commit 后死亡，Analysis/Report 存在但 Run 未成功。这两种状态可重试且不会少扫，但违反“结果与游标同事务”的严格语义，也会产生“报告已存在、Run 却失败/取消”的不一致。
- 因此终态应设计成一个事务：最终 StepResult + Analysis/Report + cursor 推进 + `TaskRun.status='succeeded'` 一起 flush/commit。该事务开始时应以 `SELECT ... FOR UPDATE` 验证 `status='running' AND process_token=:token`，任一写入失败或 Owner 已结束则整体回滚。现有 `_persist_step` 内的无条件 commit（`execution/service.py:1089-1092`）和 `_finish_run` 内的 commit（`:1000`）是必须调整的精确边界。
- 步骤异常时 `_persist_step(status="failed")` 自己提交失败 StepResult，随后 `_finish_run(status="failed")` 再提交 Run 状态，见 `backend/src/stocks/execution/service.py:747-826`。此路径不会推进 cursor。外层意外异常会 `rollback` 后调用 `_fail_claimed_run`，见 `backend/src/stocks/execution/service.py:827-830`、`backend/src/stocks/execution/service.py:929-960`。
- 配置要求研究 Task 最后一个 Step 必须是 `research_agent`，见 `backend/src/stocks/execution/config.py:517-522`；但该最终 Step 可以因 `when` 条件不满足而被持久化为 `skipped`，见 `backend/src/stocks/execution/service.py:653-671`。当前 `terminal_error is None` 后仍继续 `_complete_change_scan`，且 `_final_output` 不要求最终 Step succeeded，见 `backend/src/stocks/execution/service.py:802-809`、`backend/src/stocks/execution/service.py:1804-1836`。因此一个被跳过的研究报告步骤也可能使 Run 成功并推进 cursor。

### 4. 分页 helper 与当前计划的偏差

- `changes_in_window` 按 `(start,end]` 排序并用 `after_watermark` 续页，见 `backend/src/stocks/changes.py:227-267`。限制是 `1..1000`，且 `after_watermark` 与窗口起点取最大值。
- `scan_complete` 仅以“短页即完成”判断，见 `backend/src/stocks/changes.py:270-279`。这对记录级分页成立，但 `changed_candidates` 自己固定传入一个 `limit` 读取页面后再按 Document 聚合，见 `backend/src/stocks/changes.py:352-410`；如果直接把聚合后的候选作为页尺寸，则会被过滤来源、Document 缺失或同 Document 多记录误导，不能作为续扫终点。建议原始 ChangeRecord 页使用 strict primary-key continuation（完整键 `(watermark, id)`），候选层只展示本页结果，不改变原始页完成条件。
- `changes_in_window` 目前只按 watermark 排序和续页。`change_records.watermark` 全局唯一，因此 `after_watermark=last_watermark` 在现 Schema 可续页；但若增加 kind/source 过滤或改变排序，必须同时保存已扫描到的未过滤 floor。关键风险是“过滤后空页”不能与“窗口完成”混淆（当前 `scan_complete` 对空列表返回 true，但若过滤条件排除了剩余内容，需用更高的 resume floor 或记录已扫描到的未过滤水位来证明没有跳过）。

### 5. 推荐的持久扫描进度形状

**最小改法是在现有 `TaskRun` 上保存 continuation，并让 `ResearchCursor` 保持纯成功游标。** 这与当前 `TaskRun` 已保存窗口、scope 和 `change_scan_completed` 的方式一致，也符合 `docs/execution.md:130` 的“保存在 Task/Run”。新增 `TaskRun.change_scan_progress JSONB | None` 以及展示时间列 `window_start/window_end`；`ResearchCursor.watermark` 继续只表示完整窗口与最终报告均已成功提交的边界。

`change_scan_progress` 建议形状：

```json
{
  "version": 1,
  "status": "paging",
  "after_watermark": 142,
  "after_change_id": null,
  "page_limit": 200,
  "query_snapshot": {
    "queries": ["..."],
    "source_ids": ["..."],
    "selection_policy_version": "v1"
  },
  "counts": {
    "candidates_discovered": 320,
    "candidates_screened": 142,
    "selected": 9,
    "unexamined": null
  },
  "selection_state": {
    "selected_chunks": [],
    "query_records": [],
    "coverage": {}
  },
  "source_package_id": "uuid-or-null",
  "updated_at": "2026-10-21T09:02:00Z"
}
```

- Run 现有的 `change_watermark_start/end`、`scope_fingerprint` 是原窗口，不得由后续 Run 改写。
- `window_start/window_end` 是独立展示时间；不能从 `as_of`、`collected_at` 或 `started_at` 隐式推导。
- `status` 只允许 `paging | scanned`。`paging` 表示原窗口仍有未扫描区；`scanned` 表示原始 ChangeRecord 窗口已耗尽但最终 cursor 尚未提交。
- `after_watermark` 是最后一个**已筛选且选择结果已持久化**的 ChangeRecord。当前 watermark 全局唯一，单列足够；若实现采用 `(watermark,id)` 复合续页，则同时保存 `after_change_id`，NULL 仅在尚未越过原起点时出现。
- `query_snapshot` 冻结原查询条件和规则版本，防止 Task 配置变化改变续扫语义。
- `selection_state` 保存跨页累计的持久引用、query 记录、覆盖统计和已处理候选身份；只放引用，不复制正文。
- `source_package_id` 指向最近已提交的 partial Evidence Package。普通新 Run 可从它复制本 Run 所需输入，retry 则在复制 package 后同步改指新包。

有效性约束：`watermark_start <= after_watermark <= watermark_end`；状态受控；JSONB 顶层为 object。数据库约束只做可表达的基础检查，完整形状由 Python dataclass/Pydantic 解码器统一验证。`change_scan_completed` 只在终态根据 `status=scanned` 明确写成 true/false，不能在缺字段时默认 true。

新 Run 开始时按 `task_id`、`scope_fingerprint`、`change_watermark_end > cursor.watermark`、`change_scan_completed IS DISTINCT FROM true` 查找最近一条未完成 Run，优先恢复其原窗口、`query_snapshot` 和 page floor。若没有，才按 cursor/initial lookback 打开新窗口。拒绝比当前 cursor 更旧的 stale progress，避免旧失败 Run 在新 cursor 已推进后重新生效。

checkpoint 的提交单位应是“一页筛选结果 + 该页对 Evidence Package 的增量”。`select_evidence` 成功保存 package 后，可同步前进 checkpoint；下游 Research Agent 失败时不必重做已完成的增量扫描。这不推进成功 cursor，只允许后续 Run 从下一页或复用已保存 package 继续。

这里有两种合法的报告粒度，必须在实现前固定一种：

1. 每个 Run 只扫描一页并形成该页报告；报告成功但窗口未完时 Run 可 succeeded，progress 仍为 `paging`，下一 Run 处理下一页。
2. 一个 Run 循环读取直到窗口耗尽或达到明确扫描上限，再把累计证据交给 Agent；progress 中的 `selection_state/source_package_id` 负责跨 Run 续扫。

无论选哪种，`cursor` 都只在原始窗口耗尽、最终 Research Agent 成功且 Analysis/Report 提交时前进；不可因为 `selected_count` 达 token 上限就丢掉尚未 screened 的候选。

不要把 `after_watermark` 写回 `ResearchCursor.watermark`。后者只能保存**完整窗口终点**；把半页位置当作成功游标会跳过未筛候选。

### 6. 最小接入点与调用顺序

1. `backend/src/stocks/changes.py` / `backend/src/stocks/repository.py`：
   - 保留 `record_change` 的同一事务协议；不要从多个写入服务单独 advance cursor。
   - 增加 `load_or_start_scan`：同一 Task + Scope 有未完成 Run/progress 时返回原窗口和查询；否则以当前 cursor/initial lookback 和当前已提交计数创建新窗口。`open_window` 保持纯读取，现有调用点见 `backend/src/stocks/changes.py:182-224`。
   - 增加文档要求的 `Repository.search_changes`（内部复用 `changes_in_window`）：原始 ChangeRecord 按 `(watermark,id)` 稳定分页。若 source/kind 是该查询的正式扫描条件，必须在 SQL `WHERE` 中先过滤再 `LIMIT`；若先按全量记录取页、后在 Python 过滤，则 continuation 必须使用全量页的原始 `(watermark,id)` floor，不能使用过滤后候选的最后水位。
   - 返回明确的 `page_after_watermark/page_after_change_id` 和 `window_exhausted`。所谓完成，是**本次冻结的查询条件**在窗口中已无可读记录；不能用聚合后的候选数判断。实现必须保证完整页不会因后置过滤变成空页并误报完成。
   - 增加 `checkpoint_scan`（把本轮已持久化的 package 增量与 page floor 一起更新，不 commit）和 `complete_scan`（推进 cursor 并删除 progress，不 commit），由调用方控制事务。

2. `backend/src/stocks/research/service.py`：
   - 将 `select_evidence` 的查询阶段拆成“读取/筛选增量页 + 读取背景语义候选 + 统一选择”，并接收执行器提供的 fixed window/query/after position；不能在 Research Service 内重新打开或放宽窗口。
   - 将预算明确分成“扫描上限”和“输入选择预算”：扫描预算决定一页候选数；token 预算只限制模型输入。达到 token 预算时仍要继续 checkpoint 已返回但被 screened 的候选，不能把未 checked 的记录误标为 screened。
   - 如果采用“每 Run 一页并出报告”，package 正常保存并交给 Agent，`EvidencePackage.change_scan.completed` 为 false；`TaskRun.change_scan_progress.status` 保持 `paging`。后续 Run 从 checkpoint 继续，不能因为已有报告而跳过余页。若当前产品要求一份继续扫描后的完整报告，应选择上面的累计模式。
   - 现有 `select_evidence` 在 237 行 commit。Package 增量与 page checkpoint 必须在同一事务：要么把 commit 移到调用方，要么增加 `persist/commit=False` 变体。绝不能先提交 checkpoint 再因 package 失败而丢输入，也不能先提交 package 再丢分页位置。

3. `backend/src/stocks/execution/service.py`：
   - `_bind_change_window`（835-862）改为调用 `load_or_start_scan`，同时恢复原窗口/checkpoint；当前这里直接 `open_window` 并 commit。
   - `_run_evidence_selection`（1130-1182）把 fixed window、query snapshot 和 after position 传给 `select_evidence`，并把 package 的 scan 快照写入 Run/Step output。
   - `_final_output`（1804-1836）应只依据 package/Step 的真实 scan 状态生成 `change_scan.completed`，不能缺省 true。
   - `_complete_change_scan`（865-893）必须要求：最终 Research Agent `TaskStepResult` 为 `succeeded`；本 Run 的 `Analysis` 和 `Report` 都存在并引用同一 package；原始 ChangeRecord 窗口已耗尽。随后调用 `complete_scan`，但不要在函数内部 commit。
   - 为最终 Research Agent Step 增加专用成功路径：同一事务内插入 StepResult/Analysis/Report，验证 Run 所有权，更新 Run 为 succeeded，必要时推进 cursor 并删除 scan progress，最后一次 commit。扫描未完成时仍可保存报告并成功关闭 Run，但保留 progress 且不得推进 cursor。

4. `backend/src/stocks/models.py` 与 `backend/alembic/versions/0001_initial.py`：
   - 给 `TaskRun` 增加 `change_scan_progress JSONB`、`window_start`、`window_end`，并对窗口/进度的基础范围加 check。
   - 给 `EvidencePackage` 增加 `change_scan JSONB`，保存该 Run 实际处理的窗口、page floor、查询快照、`completed` 和覆盖计数。初始建表直接同步 `0001_initial.py`，不新增历史增量迁移。

### 7. Retry 的复制规则

- 当前 `retry_run` 从失败的旧 Run 复制 config/model/prompt snapshot，创建新 Run，调用 `_copy_completed_work`；只复制连续成功的非 `research_agent` Step，并对 `select_evidence` 输出中的 package id 重写，见 `backend/src/stocks/execution/service.py:363-520`。
- 当前 `_copy_completed_work` **不复制** `change_watermark_start/end`、`scope_fingerprint` 或 `change_scan_completed`，因为 `_enqueue_run` 用新的 Task 快照创建 Run，这些字段保持空，见 `backend/src/stocks/execution/service.py:195-218`。重试执行时会重新 `_bind_change_window`，可能打开一个新窗口，丢失原窗口未扫完部分。
- 因此重试必须把旧 Run 的 `change_watermark_start/end`、`scope_fingerprint`、`window_start/end` 和 `change_scan_progress` 复制到新 Run；若旧 Run 在 `select_evidence` 成功后才失败，复用复制的 package 和新 Run 包 id；若 scan 已 `scanned` 只差 Agent，不得重新取得新窗口。`select_evidence` 当前复制 queries/coverage/selected chunks，见 `backend/src/stocks/execution/service.py:461-483`，但还没有窗口/scan 字段，需要随 package 一起复制。
- 对于 `select_evidence` 已成功、`research_agent` 失败的重试，当前会重新运行 Agent 并使用复制的 package；这是正确方向。不要重新扫描已经完成的窗口，除非 package/scan 未完成。

### 8. 原子推进与边界 pitfalls

- **先固定窗口再取计数**：`open_window` 的顺序是 `current_watermark` 后查游标（`changes.py:201-206`）；并发 writer 可能让计数前进但当前游标未变。只要取到的 `end` 是已提交值即可，不要在同一 Run 中二次放宽 `end` 来追逐新变化；否则原窗口和 package 快照不一致。
- **不要把 `collected_at`、`published_at`、自增主键或 `recorded_at` 当 cursor**：这些值无法证明无未提交空洞。现有 counter 行锁机制是正确基础。
- **共享计数行是全局串行点**：所有研究可见写入都争用同一 `ChangeCounter` 行；`record_change` 应放在该事务的派生数据写入之后、提交之前，之后不得再进行慢速外部 I/O，避免把全文下载或模型调用纳入锁的临界区。当前入库的 S3 写入发生在数据库事务前（`repository.py:187-230`），保持这个顺序。
- **不要只保存单一 `after_watermark` 而不定义过滤语义**：由于当前 watermark 全局唯一，单列在现 Schema 可续页；但若增加 kind/source 过滤或改变排序，必须同时记录原始页边界和已扫描到的未过滤 floor。推荐从一开始就使用 `(watermark, change_id)`，便于查询稳定且不依赖“水位唯一”的实现细节。
- **不要将“短页”当作整个窗口完成**：只有当页读取已经到 `end` 且没有查询过滤造成的未扫描候选，才能推进。对 `candidate` 聚合后的数量更不能作为原始分页完成判据。
- **不要用过滤后的候选推进分页**：`changed_candidates` 在限页后才按 source 过滤，见 `backend/src/stocks/changes.py:369-408`；如果整页都被过滤，它可能返回空列表，但窗口仍有记录。continuation 必须来自未过滤的 ChangeRecord 页。
- **不要提前推进**：报告生成成功后，如果 scan 还有下一页，Run 可以是 succeeded 且 package 可复查，但 `ResearchCursor` 不应前进；否则下一窗口会绕过剩余候选。
- **区分 scan checkpoint 与 success cursor**：checkpoint 可随已保存的 Evidence Package 增量前进，以便 Agent 失败后不重扫；`ResearchCursor` 只能在完整窗口和最终报告都成功时前进。两个状态必须在 API 和 CLI 输出中分开，避免把“已扫描/待报告”的中间进度展示成已完成。
- **不要在 `select_evidence` 内部直接推进**：它的 Step 可能被重试、可能只处理一页，且当前 commit 发生在 `research/service.py:233-243`。推进必须由成功终态编排控制。
- **注意 skipped 最终步骤**：`_complete_change_scan` 当前没有检查最终 Research Agent Step/Report 是否成功；必须显式检查 `TaskStepResult(status="succeeded")`、`Analysis`、`Report` 和同一 package/run id，否则“无条件跳过 agent”会错误消费窗口。
- **注意 owner fence 与提交顺序**：`_assert_run_ownership` 只在写 Step 前、持 Run 行锁时检查（`execution/service.py:1003-1029`）；若 Report、cursor 和 Run 终态分三次提交，Owner 可在窗口里结束 Run，造成 Report 已保存、Run cancelled、cursor 已推进的任意组合。终态必须持 Run 行锁，并把 Step/Report、Run 条件 UPDATE 和 cursor 放在同一 commit；条件 UPDATE 必须带 `status='running' AND process_token=:token`，无匹配即整体回滚。
- **不要让 checkpoint 越过未持久化的筛选结果**：checkpoint 前进会告诉下一次 Run “这一页已处理”，但 package 若失败则实际证据输入已丢失。checkpoint 与 package 增量要同一提交；终态 cursor/progress 删除也同理。下游 Agent/Report 失败可以保留已推进的 scan checkpoint，但成功 cursor 必须保持不动。
- **不要回滚已提交证据**：`select_evidence` 和采集各自提交派生资料（`execution/service.py:672-680`）；原子性针对“StepResult、Analysis/Report、cursor”，不能用 rollback 假装证据包或 Document 从未保存。
- **scope 变更**：`scope_fingerprint` 已经以 task+scope 为键（`changes.py:139-160`）；新 scope 的新 Run 不应继承旧 continuation。重试要使用旧 Run 保存的 scope 快照，避免当前 Task YAML 变化造成错误复用。
- **同一 Run 唯一 package**：数据库有 `uq_evidence_packages_run`（`models.py:190-191`）。在一次 Run 尚未生成 Report 前，可以增量更新该 Run 自己的 package；一旦 Analysis/Report 保存，package 必须保持不可变。后续 Run 使用自己的新 package，并可从 checkpoint 的 `source_package_id` 复制累计输入。
- **cursor 首次插入竞态**：`advance_cursor` 先 `SELECT ... FOR UPDATE`，没有行时再插入，见 `backend/src/stocks/changes.py:297-319`。同一 Task 通常由唯一活跃 Run 约束串行化，但完成阶段仍应使用 Task advisory lock 或 `INSERT ... ON CONFLICT DO UPDATE ... WHERE existing.watermark < new`，避免并发完成时的唯一键冲突或倒退。
- **`window_start/window_end` 尚未持久化**：业务契约要求 Run 展示这两个时间，但当前 `TaskRun` 只有水位和 created/started/finished 时间，没有独立展示字段。打开或恢复原窗口时，应把展示时间随 Run 一起冻结；不要把 `as_of` 或 `started_at` 隐式解释为窗口，否则续扫时无法稳定重现。

## External References

- PostgreSQL 17 / pgvector 0.8.7 is the deployed database image in `compose.yaml:1-15`; the counter-row lock and window queries rely on PostgreSQL transaction semantics.
- The backend declares SQLAlchemy 2.x, psycopg 3.x and pgvector 0.3+ in `backend/pyproject.toml:1-18`; the recommendation uses SQLAlchemy `Session` transactions and PostgreSQL JSONB rather than a new service.
- Repository contract references: `docs/execution.md:106-145` (window/commit ordering, cursor and resume), `docs/research.md:51-106` and `:115-117` (scope, scan statistics, package and retry), `docs/data.md:287-301` (search paths and change queries), `docs/validation.md:23-28` (acceptance boundaries).
- Related specs: `.trellis/spec/backend/database-guidelines.md`, `.trellis/spec/backend/worker-guidelines.md`, `.trellis/spec/guides/cross-layer-thinking-guide.md`.

## Caveats / Not Found

- 当前代码没有 `repository.search_changes` 的公开函数；`changes.py` 中的 `changes_in_window`/`changed_candidates` 是直接可复用的基础，但语义查询路径与增量路径尚未在 `select_evidence` 合并。
- 没有现成的持久 scan continuation 表/字段；现有 `change_scan_completed` 只是布尔结果，不足以恢复分页位置。
- 现有测试证明水位/分页 helper 以及“手工调用 `_complete_change_scan` 不推进/推进”的片段，但没有证明 `select_evidence` 消费窗口、真实 Run 续扫、retry 保留原窗口，或 Report 与 cursor 的单事务提交。`test_research_run_records_its_window_and_advances_the_cursor` 在 `backend/tests/test_change_watermarks.py:717-829` 直接调用 helper，不能替代端到端验收。
- `docs/execution.md:126-130` 要求保留原窗口、查询条件、分页位置，并要求结果和游标原子；`docs/research.md:55`、`docs/research.md:106` 要求 package 保存实际扫描信息。实现必须同时更新这些契约的唯一归属文档，但本研究报告不改变文档。
- 未运行任何本机测试、数据库、Alembic、API 或外部集成验证；按仓库规范，运行验证只能在测试服务器 `100.120.14.84` 的任务隔离目录执行。
