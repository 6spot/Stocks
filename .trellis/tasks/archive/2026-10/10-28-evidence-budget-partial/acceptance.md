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

## 未完成

- 重新采集 Reddit 以生成干净的 HTML partial 新版本时，Reddit RSS 返回 429；现有 v1 仍保存旧 JSON 包装，报告虽已使用它，但文本清理未在真实新版本上验证。待限流恢复后重试采集。
- `change_scan.completed=false`（增量分页 1/7）仍是既有独立缺口；本次预算保留不改变该扫描边界。
- Reddit 的 facts 仍有 1 篇 `MODEL_RESPONSE_INVALID`。
