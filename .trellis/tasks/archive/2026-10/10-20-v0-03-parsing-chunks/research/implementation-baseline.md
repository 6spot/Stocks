# V0-03 实现基线（2026-10-20）

## 已落地前置

- V0-01 已提供 `stocks.repository.ingest_document()`、不可变 Document 版本和 S3 原件读取。
- V0-02 已提供 SEC RSS/Atom 发现、真实 HTML 获取、`text/html` 原件入库和部分内容状态。
- `backend/src/stocks/models.py` 中现有 `DocumentChunk` 只有 Document、位置、文本和
  Embedding 字段，没有处理器版本、源位置、解析状态或批次关联，不能用于追溯重处理。
- `backend/tests/test_repository.py` 需要 `TEST_DATABASE_URL` 和真实 PostgreSQL；
  `backend/tests/test_collection.py` 用固定 HTTP 响应验证采集边界。
- 当前没有前端，也没有解析、Embedding、检索、Task/Run 或报告实现。

## 采用的约束

- MIME 范围只取当前真实来源实际产生的 `text/html`、`application/xhtml+xml`、
  `text/plain`。PDF/JSON/XML 等后续扩展明确标为 `unsupported`，不返回空成功。
- 纯文本的 `locator` 使用严格解码后字符范围。HTML 的每个片段保存语义块类型、
  元素路径和原始 HTML 字符范围；跨块 Chunk 保存有序位置段。
- 解析结果不可变。`document_parse_states.latest_batch_id` 表达最近尝试，
  `current_batch_id` 表达最近成功结果。失败重处理不会清除当前成功批次。
- 相同 Document、处理器版本和配置指纹的普通处理结果复用；`--reprocess` 新开批次。
- 不把原始 HTML 作为成功 Chunk 文本，不另存整篇清洗正文，不引入外部解析服务。

## 验证依据

- 领域契约：`docs/data.md` 的“后台解析、片段与索引”“检索”和“来源可信度、证据与安全”。
- 阶段验收：`docs/validation.md` 的 V0“可检索证据”与证据阅读底线。
- 工程规范：`.trellis/spec/backend/` 的数据库、错误处理、质量与远程测试环境。
- 远程环境：`100.120.14.84`；Python 3.12、隔离 pgvector、临时 S3 和随机测试凭据，
  不在仓库或日志中记录密码、正文或完整敏感 URL。
