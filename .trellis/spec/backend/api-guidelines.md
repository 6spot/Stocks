# HTTP 读取接口规范

适用于 `backend/src/stocks/api.py` 中面向 Owner 本机的只读接口。产品语义与字段含义以
`docs/interface.md`、`docs/research.md` 为唯一来源；本文件只规定工程实现契约。

## Scope / Trigger

- Trigger：新增或修改任何返回 Document、Chunk、原件字节、Evidence Package、Analysis
  或 Report 的路由。
- 这些接口只读：不得采集、解析、索引、调用模型，也不得写回任何状态表。
- 未经认证的入口只绑定 `127.0.0.1`；跨设备或公网开放归 V1-07 访问控制任务，不在这里加半个认证。
- 本组路由只读单个报告，**没有 redirect 或"最新报告"别名**；V0 由 Owner 从 `/api/reports` 列表自己取 id，不额外加跳转路由。

## Signatures

当前只读路由（全部 GET，复用 `stocks.db.session_factory()`）：

```
GET  /api/documents
GET  /api/documents/{document_id}
GET  /api/documents/{document_id}/original
GET  /api/reports
GET  /api/reports/{report_id}
GET  /api/reports/{report_id}/evidence/{chunk_id}
```

UUID 路径参数直接声明为 `uuid.UUID`，让 FastAPI 返回 `422` 而不是在路由里手写解析分支。
列表查询必须有排序和 `limit` 上限（现有实现 `ge=1, le=200`）。

## Contracts

- **按引用版本读取**：报告及其证据只按 Evidence Package 里保存的 `document_id` / `chunk_id`
  / `parse_batch_id` / `document_version` 解析，绝不跟随 `document_collection_states.latest_document_id`，
  也不重新访问来源站点。当前采集指针作为**独立字段**返回（`is_current_collection_pointer`、
  `collection_state`、`latest_collection_states`），使"新版本已入库"与"旧报告仍指向旧证据"能同时成立。
- **引用必须属于本 Run 的证据包**：片段不在该 Run 的 `selected_chunks` 内一律 `404`，
  不得代为检索其他资料填补。
- 响应只输出已持久化的安全字段。Run 快照、`ModelCall`、`error` 不得包含 API key、
  Authorization、endpoint 明文、完整外部 URL 或模型响应正文。
- 原件端点返回**存储的原始字节原样**，不重新解码或改写；只降级 media type：
  HTML/XHTML 及纯文本 → `text/plain`，其他 → `application/octet-stream`。
  必带响应头：`X-Content-Sha256`（实际字节哈希）、`X-Content-Hash-Match`（与 Document
  `content_hash` 的比较结果）、`X-Content-Type-Options: nosniff`、
  `Content-Security-Policy: default-src 'none'`；`?download=true` 时追加
  `Content-Disposition: attachment`。哈希不一致只记 warning 日志并如实反映在头里，不改内容。
- `coverage_gaps` 是"取材时记录的缺口 + 读取时观察到的缺口"并集去重，观察值使用稳定词表：
  `document_not_stored`、`chunk_unavailable`、`partial_source_content`、
  `snapshot_storage_unconfigured`、`chunk_not_in_current_parse_batch`、
  `chunk_not_in_current_index`、`run_not_succeeded`。资料缺失表达为缺口，不得写成"事实不存在"，
  也不得因片段缺失而返回空正文冒充成功。`version_matches` 是独立校验位（包内记录的
  `document_version` 与 Document 行是否一致），**不计入 `coverage_gaps`**：该词表已被
  quickstart/interface 当作对外语义契约，随意加词会摊到多个归属文档。

## Validation & Error Matrix

| 条件 | 处理 |
| --- | --- |
| 路径 id 未知，或请求的 chunk 不在该 Run 的证据包内 | `404`，稳定短消息 |
| 包内引用解析不到 Document/Chunk 行 | `200` + `chunk_available=false` + 对应缺口；**不得改成 404 或空正文**，那会抹掉证据可见性 |
| 请求路径 id 非 UUID | `422`（由 FastAPI 类型校验产生） |
| Report→Analysis→EvidencePackage 链断裂、`selected_chunks` 或 `coverage.gaps` 形状非法 | `409`（防御分支：相关 FK 均 `NOT NULL` 且 `ondelete=CASCADE`，当前 Schema 下几乎不可达，不要求测试） |
| 对象存储未配置（endpoint/bucket/密钥不齐） | `503` + `SNAPSHOT_STORAGE_UNAVAILABLE` |
| S3 读取抛任何异常 | `503` + 同一稳定码，只回异常类型到日志，不回 endpoint、凭据或异常原文 |
| 快照字节与记录哈希不符 | `200` 返回原字节，`X-Content-Hash-Match: false` + warning 日志 |

## Good / Base / Bad Cases

- Good：新版本入库后打开旧报告，正文仍是旧 Chunk，`is_current_collection_pointer=false`，
  同时 `latest_collection_states` 显示指针已前移。
- Base：只采集未索引的 Document，报告片段仍可读，缺口含 `chunk_not_in_current_index`。
- Bad：为了"方便"把证据解析到 `latest_document_id`；把 HTML 原件以 `text/html` 返回；
  对象存储不可用时返回空正文或 `200`；用宽 `except Exception` 把配置错误伪装成可重试故障。

## Tests Required

- 只读 API 测试用真实 PostgreSQL（`TEST_DATABASE_URL`）+ 注入的 S3 读写函数，不依赖真实外部服务。
- 至少断言：引用版本不随新版本改变、失败 Run 不出现在报告列表、原件字节与哈希逐字节一致、
  HTML 被降级为 `text/plain` 且安全头存在、非 HTML 走 `application/octet-stream`、
  未配置存储 → `503` 稳定码且响应体不含 endpoint、外来 chunk → `404`、缺失行 → 缺口词表齐全。
- `/api/documents` 列表对**每个版本行**都返回其 `(source_id, dedupe_key)` 的采集状态（不只是指针行），
  并包含 `dedupe_key`；改动该路由时必须用同库 v1/v2 两行同时断言 `collection_state` 均非空且
  各自 `version`/`content_hash` 不变。
- `409` 与 `run_not_succeeded` 属防御分支，当前 Schema 下不可达，不为它们写假可达测试。
- 参考 `backend/tests/test_report_api.py`。

## Wrong vs Correct

### Wrong

```python
# 路由跟随最新版本，等于用当前数据改写历史报告
# stocks.repository.get_latest_document() 只能用于采集/处理类入口，不能用在证据读取里
document = get_latest_document(session, item.source_id, item.dedupe_key)
```

```python
# Session 已关闭后再读 ORM 属性 → DetachedInstanceError
with Session(engine) as session:
    document = persist_document(session, ...)
assert row["id"] == str(document.id)
```

### Correct

```python
# 按证据包保存的 id 读取，当前指针单独返回
document = session.get(Document, referenced_document_id)
entry["is_current_collection_pointer"] = str(pointer.latest_document_id) == str(document.id)
entry["collection_state"] = _collection_state_payload(pointer)
```

```python
# 在会话内取出纯值，测试只比较 plain id
with Session(engine) as session:
    saved = {"document_id": document.id, "chunk_id": chunk.id, "version": document.version}
assert listed[0]["id"] == str(saved["document_id"])
```
