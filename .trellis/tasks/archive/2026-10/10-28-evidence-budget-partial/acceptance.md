# 验收记录：证据预算保留与 partial 文本清理

日期：2026-10-28。

## 修复

- `select_evidence` 保留 `max_tokens // 5`（30k 中 6k）给语义查询，增量变更最多使用 `max_tokens - reserve`（24k）。
- 选择策略版本从 `v1.0` 升为 `v1.1`，Evidence Package 记录 `semantic_reserve_tokens` / `incremental_budget_tokens`。
- `_partial_payload` 输出 `<article><h1>标题</h1>摘要</article>` HTML，content type 为 `text/html; charset=utf-8`，不再保存 `{"summary":...}` JSON。

## 验证

- 远程隔离测试库：`tests/test_collection.py` **62 passed**、`tests/test_incremental_evidence.py` **10 passed**、`tests/test_research.py` **9 passed**。
- 真实 SK Hynix Run `45aa0738`：Evidence Package 选中 **100** 个 chunk；两个语义查询分别 `selected_count=8` 和 `1`；Reddit 文档 `4f39a2f4-4546-4dcd-a06c-ba1899992385` 被选中。
- 报告 `4c449b9b-fa73-43e2-915a-2ef2ccd4fd55` 生成成功，正文引用 Reddit `[E94]`，并明确区分 Reddit 转述、CNBC 报道与无法核验的价格/估值。
- 总预算没有扩大；增量与语义边界保留。

## v2 补验（2026-10-28）

- 限流恢复后重新采集 Reddit，生成 3 篇 HTML partial 新版本（`95530482…`、`1084fd3e…`、`345870d4…`）；SK Hynix v2 chunk 以正文开头，不含 JSON/`<!-- SC_OFF -->` 包装。
- SK Hynix 定向 Run `f726f3e0` 成功：Evidence Package `selected_count=113`、`estimated_selected_tokens=26734`，`semantic_reserve_tokens=6000`、`incremental_budget_tokens=24000`；Reddit v2 在 `selected_chunks` 中，direct 查询选中 8 条、background 选中 5 条。
- 报告 `87a74b0b-5728-4e2b-b850-5b84b0a6905b` 生成成功，正文明确讨论 Reddit 内容及其证据边界。
- 远程定向测试共 **82 passed**（collection 62、incremental 11、research 9）。

## 未完成

- `change_scan.completed=false`（增量分页 1/7）仍是既有独立缺口；本次预算保留不改变该扫描边界。
- Reddit 的 facts 仍有 1 篇 `MODEL_RESPONSE_INVALID`；不影响已完成的文本检索与分类。
