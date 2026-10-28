# 证据预算保留与 partial 文本清理

## 背景

SK Hynix 定向 Run 的 Evidence Package 未选中 Reddit 文档。排查确认：语义查询检索到了 Reddit（查询 `SK Hynix` 排名第 1，相似度 0.559），但增量变更先选中 122 个 chunk 并耗尽 30k 预算，两个语义查询 `selected_count=0`、缺口 `token_budget`。同时 Reddit `partial` 内容被保存为 JSON+HTML，chunk 文本含 `{"summary":"<!-- ...

## 目标

1. 保留 30k 总预算边界，为语义 direct 查询预留至少 20%，避免增量变更完全饿死当前主题的精确检索。
2. 将 RSS/Atom `partial` 内容保存为可解析正文（标题 + HTML 摘要），避免 JSON 包装和标签噪声进入 chunk/embedding。

## 验收标准

- [x] 增量路径最多使用 `max_tokens - max_tokens//5`；语义查询仍可使用未用预算。真实 Run `45aa0738` 中语义 direct 查询从 0 选中变为 8 选中。
- [x] 有大量增量候选时，direct 语义查询选中了 Reddit SK Hynix 文档（Evidence Package 100 chunks，报告 `4c449b9b` 引用 `E94`）。
- [x] `partial` payload 改为 HTML 正文；`tests/test_collection.py` 62 passed，断言不含 `{"summary"`。重新采集后 3 篇 v2 chunk 已无 JSON/HTML 包装，SK Hynix v2 被 Run `f726f3e0` 选中。
- [x] 远程定向回归通过（test_collection 62、test_incremental_evidence 11、test_research 9）；Run `f726f3e0` 选中 Reddit v2，报告 `87a74b0b` 引用 Reddit 讨论。
- [x] 总预算仍为 30k，增量使用 24k、语义保留 6k；未取消来源/文档边界，partial 仍为 partial。
