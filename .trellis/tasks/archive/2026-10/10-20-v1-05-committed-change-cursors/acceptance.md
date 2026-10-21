# V1-05 验收记录

- 验证时间：2026-10-21（Asia/Singapore）
- 验证环境：测试服务器 `100.120.14.84`，任务隔离目录 `backend`，`.venv` 指向持久工具环境 `/opt/stocks-test-runtime/tool-venvs/stock-tools`
- 凭据处理：SSH 口令与 `DATABASE_URL`/S3 密钥仅在当前会话与远端一次性环境文件中注入，未写入仓库、脚本、命令日志或本文件
- 本机仅用于编辑与打包，未在本机执行 pytest、Alembic、数据库或 API 验证

## 结构一致性（全新数据库）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 空库初始化 | `python -m alembic upgrade head`（先 drop 再 create 全新库） | exit 0，`Running upgrade -> 0001, Initial intelligence storage.` |
| 模型/建表一致 | `python -m alembic check` | exit 0，`No new upgrade operations detected.` |
| 约束实际落地 | `pg_get_constraintdef` 读取 `ck_task_runs_change_scan_progress` | `change_scan_progress IS NULL OR jsonb_typeof(...) = 'object'`，与 `models.py` 一致 |

未测试旧版升级或历史数据迁移：开发阶段直接重建。

## 测试执行

全部设置 `TEST_DATABASE_URL` 指向上述全新库；`-p no:randomly` 固定顺序；记录的是实际输出，没有把 skip 记作通过。

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 本任务核心 | `pytest tests/test_change_watermarks.py -q` | 28 passed，0 skipped |
| 执行接入 | `pytest tests/test_execution.py -q` | 59 passed，0 skipped |
| 相邻受影响面 | `pytest tests/test_research.py tests/test_collection.py tests/test_repository.py tests/test_classification.py tests/test_facts.py -q` | 116 passed |
| 处理/索引写入点 | `pytest tests/test_processing.py tests/test_processing_retry.py tests/test_embeddings.py tests/test_worker_processing.py -q` | 99 passed |
| 全量回归（结构变更 + 六处写入点横跨采集/处理/分类/抽取，影响面广，因此扩大范围） | `pytest -q` | 366 passed，1 warning（第三方 Starlette 弃用提示），0 failed，0 skipped |

静态检查：`python -m compileall -q src tests alembic` 通过。`backend/pyproject.toml` 只声明 `pytest` 作为 dev 依赖，仓库未配置 ruff/mypy，因此没有可运行的 lint/type 检查；此点如实记为“工具不存在”，不记作通过。

## 验收条件

**AC-1（07:59 开始、08:01 提交、08:00 扫描）：通过。**
`test_a_transaction_that_commits_later_gets_a_higher_watermark` 使用两个真实 Session：先开启慢事务并 `record_change()` 持计数行锁，另起线程执行快写入；断言慢事务提交前快写入者一定被阻塞（`assert not fast_done_before_slow_commit`），因此快窗口的水位不可能包含晚提交的慢变化，两条 `ChangeRecord` 获得按提交顺序排列的不同 watermark。
`test_window_end_contains_every_committed_change_behind_it` 证明 `end` 之前不存在未提交空洞；`test_failed_transaction_leaves_no_change_and_no_gap` 证明回滚不留下水位空洞。

**AC-2（晚到旧资料、迟完成处理仍进入后续窗口，保留真实时间）：通过。**
`test_window_scan_does_not_depend_on_collection_time` 与 `test_watermark_is_not_the_collection_timestamp` 证明水位与 `collected_at`/`published_at` 无关；`test_changed_candidates_group_one_document_into_one_candidate`、`test_fact_changes_are_traceable_to_their_fact` 覆盖分类/索引/Fact 完成事件按 Document/Fact 归入候选并保留引用。`recorded_at` 与 Document `published_at` 分别保存，晚到资料保留真实发布时间。

**AC-3（分页续扫沿用原窗口；失败不推进；结果与游标同事务）：通过。**
`test_pagination_resumes_the_same_window`、`test_normal_run_resumes_the_latest_unfinished_checkpoint_for_its_scope`、`test_unfinished_checkpoint_selection_skips_malformed_newer_progress`（全部候选损坏时以 `SCAN_PROGRESS_INVALID` 失败，不静默开新窗口）、`test_retry_copies_the_source_window_and_scan_position` 覆盖续扫与 retry。
`test_cursor_only_advances_after_a_completed_scan`、`test_cursor_never_moves_backwards` 覆盖游标单调性。
`tests/test_execution.py` 中 `test_final_report_failure_rolls_back_final_step_and_keeps_scan_checkpoint` 证明 `render_report` 抛错时 Analysis/Report 均回滚、Step 记 failed、检查点保留 `status='scanned'` 且游标不推进；`test_final_ownership_loss_commits_no_success_artifacts` 证明最终提交前失去所有权时没有任何成功工件落地；跳过最终 Agent 以 `FINAL_RESEARCH_STEP_NOT_SUCCEEDED` 失败且不推进游标。
`test_normal_runs_advance_through_frozen_paged_scan` 证明连续 3 个正常 Run 在原窗口内按 `after_watermark` 1/2/3 推进、后续 Run 即使改动配置仍使用冻结的 `query_snapshot`，直到窗口扫完才推进游标。

**AC-4（两个 Task 互不消耗；范围变更重新初始化；边界不丢不重）：通过。**
`test_cursor_is_per_task_and_per_scope`、`test_changed_scope_reinitializes_instead_of_reusing_the_cursor`、`test_scope_fingerprint_is_order_independent_and_task_scoped` 覆盖隔离与重初始化。
`test_filtered_pages_limit_in_sql_and_preserve_raw_continuation` 与 `test_candidate_grouping_cannot_move_raw_pagination` 证明过滤在 `LIMIT` 之前执行、续点是原始 `(watermark, change_id)` 而非按 Document 聚合后的水位，因此筛选页与重复候选都不会跳过记录。

## 文档与命令

- `docs/data.md`：变化记录写入点、`search_changes()` 先过滤后分页契约、V1-06 未实现边界；并修正先前遗留的过期陈述（原“采集未实现游标/分类/Fact/证据 API”改为实际状态，并区分 per-Source Feed 游标与研究侧变化游标）。
- `docs/execution.md`：窗口绑定、检查点、原子收尾与失败语义；检索入口路径改为实际的 `stocks.changes.search_changes()` 与 `stocks.repository.search_chunks()`。
- `docs/validation.md`、`docs/quickstart.md`：当前能力边界与命令。
- CLI 冒烟：`python -m stocks.execution cursor --task <不存在的 task>` 返回 `{"error": {"code": "TASK_NOT_FOUND", ...}}`，`--help` 列出 `cursor` 子命令；两个子命令均为只读，测试已断言 `cursor` 对缺失游标输出 `null`。

## 未完成 / 受限 / 待真人验收

- **V1-06 范围未实现**：增量 Document/Fact 候选与历史背景证据的统一合并、跨来源去重与覆盖统计未做，本任务只交付原始 change scan、检查点与游标。文档按此边界写明，不得把分页状态描述成已完成统一证据合并。
- **真实第三方 API 未接通**：本轮全部使用测试服务器上的 mock Provider/embedding 与本地 fixture Source。SEC/FRED 等真实 `http_api` 来源、真实 Decision/Generation Provider 闭环需在 V1-09 部署验收中由 Owner 核对。
- **Web/控制台未实现**：属 `V1-07`/`V1-08` 范围，暂无 UI 可供人工验收。
- **跨进程 kill 续扫演练未单独执行**：双事务并发属性已由真实两连接测试覆盖，跨进程 kill 演练属于可选补充，未记作已验证。
- **性能量级未测**：共享计数行会把研究可见写入的最终提交串行化，这是设计取舍（见 `design.md`）。当前只在测试库小数据量下验证正确性，未做吞吐基准。
- **扫描进度体积随窗口增长**：`change_scan_progress.scanned_document_ids` 保存窗口内已扫描 Document 身份的去重集合，`counts.candidate_documents_scanned` 由它求值得到，因此集合大小随窗口内不同 Document 数线性增长，且每个已完成页都重写这份 JSONB。`initial_lookback` 为 `null` 时首个 Run 的窗口起点是 0，即整段历史，超大窗口的体积与逐页重写成本需要在真实数据量下确认。可选缓解是配置正数 `initial_lookback` 限制首次窗口。
  本轮没有给它加人为上限：任何“截断列表但仍报计数”的做法都会把未知写成已知，违反覆盖真实性要求。留给 V1-06 与 `ChangeRecord` 持久行（窗口内 `document_id` 可重算）一起决定是保留引用集合还是改为按窗口重算。
