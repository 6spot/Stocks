# V1-06 验收记录

- 验证时间：2026-10-21（Asia/Singapore）
- 验证环境：测试服务器 `100.120.14.84`，任务隔离目录，`.venv` 指向持久工具环境 `/opt/stocks-test-runtime/tool-venvs/stock-tools`
- 本机仅用于编辑与打包，未在本机执行 pytest、Alembic、数据库或 API 验证；凭据未写入仓库或本文件

## 结构一致性（全新数据库）

| 检查 | 结果 |
| --- | --- |
| `alembic upgrade head`（先 drop 再 create 全新库） | exit 0 |
| `alembic check` | `No new upgrade operations detected.` |
| `evidence_packages.evidence_groups` | 新 JSONB 列，`NOT NULL DEFAULT '[]'::jsonb`，与 `models.py` 一致 |

不测试旧版升级或数据迁移：开发阶段直接重建。

## 测试执行

全部设置 `TEST_DATABASE_URL` 指向上述全新库，`-p no:randomly` 固定顺序；没有任何 skip 被记作通过。

| 范围 | 结果 |
| --- | --- |
| 新增 `tests/test_incremental_evidence.py` | 7 passed |
| `pytest -q`（全量） | **373 passed**，1 warning（第三方 Starlette 弃用提示），0 failed，0 skipped |
| `python -m compileall -q src tests alembic` | 通过 |

格式/类型工具：仓库 `backend/pyproject.toml` 未声明 ruff/mypy，因此没有可运行的 lint/type 检查。另行在测试服务器用 `pyflakes` 扫描本次改动的文件，仅剩 `repository.py` 中一行**改动前就存在**的未使用 import，不属本次引入。

## 验收条件

**AC-1（Document 与其 Fact 同时命中只算一条证据链；其他事实仍进入候选；分类错误资料可语义召回）：通过。**
`test_document_and_fact_merge_across_incremental_and_semantic_paths` 让同一 Document 同时经增量变化与语义背景命中、并带两条 Fact（同指标与一条无关的 `unemployment_rate`）：结果 `evidence_groups` 只有 **1** 组，`chunk_ids` 含该 Document 的两个 Chunk，`fact_refs` 同时保留两条 Fact（未把无关事实丢掉），但只计 1 条证据链。
`test_failed_classification_does_not_filter_semantic_recall` 证明分类状态不参与召回过滤：classification 为失败状态时资料仍可被语义召回选中（处理失败是覆盖缺口，不是“不存在”）。

**AC-2（增量与历史重复不双算；直接资料与宏观背景同包且角色清楚）：通过。**
`test_incremental_and_history_do_not_double_count_chunks` 让同一 Document 的两条 Chunk 同时经增量与语义两条路径返回：`candidates_discovered == 2`、`candidates_screened == 2`（按不同 Document+Chunk 计，不因路径重复翻倍），而 `evidence_group_count == 1 < selected_count == 2`，明确区分“入选 Chunk 数”与“独立证据链数”。
`test_new_and_historical_material_keep_distinct_roles` 证明一个 Run 内 `new_information`（窗口内新变化）与 `historical_context`（纯语义背景）同时进入同一 Evidence Package，且 `new_information_group_count` / `historical_context_group_count` 各为 1；重复命中同一 Chunk 时会记录语义侧的 `match_role` 而不重复加入材料。

**AC-3（触顶时真实覆盖缺口与续扫；未知不填零；筛选未入选不阻止游标推进）：通过。**
`test_truncated_semantic_query_preserves_unknown_counts_and_raw_cursor` 让语义查询 `truncated=True`：`candidates_discovered is None`、`unexamined_count is None`（均不填 0）、`retrieval_truncated is True`，同时原始变化页仍返回真实 `after_watermark` / `after_change_id` 续点并写入 `change_scan`。
`test_completed_filtered_scan_can_advance_cursor` 用 `max_tokens=1` 让已扫描候选全部因预算未入选：`filtered_out_count > 0`、`unexamined_count == 0`、`selected_count == 0`，随后 `checkpoint_scan` + `_complete_change_scan` 仍能完成窗口并推进游标——筛选后未入选与未扫描被分别表达，且不阻塞正常推进。
`test_conflicting_facts_are_retained_and_marked` 覆盖同主体/同指标/同单位但数值不同的两条 Fact 并列保留并都标 `conflict=True`，不做平均或覆盖。

## 实现位置

- `backend/src/stocks/research/evidence.py`（新增）：`EvidenceCollector` / `EvidenceGroup` / `mark_conflicting_facts`，按 Document 合并、保留 Chunk 与 Fact 全部原始引用。
- `backend/src/stocks/research/service.py`：`select_evidence` 先读固定窗口的增量候选与 Fact，再执行语义查询并统一合并；新增 `evidence_groups`、`evidence_group_count`、`fact_count`、`filtered_out_count` 与 new/historical 分项计数；`selected_chunks` 增加 `evidence_role` / `chunk_ids` / `fact_ids` / `reasons` / `kinds`。
- `backend/src/stocks/facts.py`：新增有界 `facts_for_documents()`。
- `backend/src/stocks/repository.py`：新增 `current_document_hits()`，只读当前成功解析批次的 Chunk。
- `backend/src/stocks/models.py` / `alembic/versions/0001_initial.py`：`EvidencePackage.evidence_groups` JSONB。
- `backend/src/stocks/execution/service.py`：Agent 输入把 Fact 与角色挂到其来源 Chunk；retry 复制与 Step 输出包含 `evidence_groups`。
- `docs/research.md`、`docs/data.md`、`docs/validation.md`、`docs/quickstart.md`：更新实际能力与统计口径。

## 未完成 / 受限 / 待真人验收

- **跨 Document 转载识别未实现**：仅凭标题或向量相似不做合并；已确认同源内容的跨 Document 分组仍属后续能力。文档已如实写明。
- **高级跨源冲突解释未实现**：当前只在同主体/指标/单位/期间口径下标记数值冲突并并列保留，不做平均或自动裁决。
- **真实第三方 API 未接通**：全部使用测试服务器上的 mock Provider/embedding 与本地 fixture；SEC/FRED 等真实来源与真实 Decision/Generation Provider 闭环需 V1-09 由 Owner 验收。
- **无 UI 可人工验收**：控制台属 V1-07/V1-08，本轮只保证 JSONB 与 API/CLI 可读取。
- **分类失败路径未做真实模型调用验证**：测试通过持久状态构造验证“分类失败仍可语义召回”，未触发真实分类模型。
- **性能未测**：增量页按 Document 读取当前 Chunk 与 Fact，页面规模受 `change_page_limit` 与 `limit_per_query` 约束；大窗口下的延迟与 Token 成本尚未做真实数据量基准。
