# 设计

## 预算分层

在 `select_evidence` 中计算 `semantic_reserve = max_tokens // 5`（20%），增量路径的实际上限为 `max_tokens - semantic_reserve`。语义查询继续使用 `max_tokens` 总上限，因此可以使用保留额度以及增量路径未用完的余额。总预算不变；若语义查询无命中，最多闲置保留额度，不自动把全库内容塞入。

这是有序预算，不是提高上限：增量变更仍是新信息优先，但 direct/background 语义查询获得最低可用空间。

## Partial 内容

`_partial_payload` 输出 `<article><h1>标题</h1>摘要</article>` 的 UTF-8 HTML，content type 为 `text/html; charset=utf-8`。摘要来自 Feed `summary`/Atom `content`，可能本身含 HTML；解析器按 HTML 提取正文。仍然标记 `completeness=partial`，不伪造完整文章。

## 验证

- 单元测试覆盖预算分配和 partial HTML 形状；
- 远程定向回归；
- 重新采集 Reddit，确认新版本 chunk 无 JSON 包装；
- 重跑 SK Hynix；检查 Evidence Package 是否包含 Reddit。
