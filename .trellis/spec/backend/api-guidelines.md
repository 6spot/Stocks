# Owner HTTP 接口规范

适用于 `backend/src/stocks/api.py` 中面向单一 Owner 的接口。产品语义与字段含义以
`docs/interface.md`、`docs/research.md` 为唯一来源；本文件只规定工程实现契约。

## Scope / Trigger

- Trigger：新增或修改任何返回 Document、Chunk、原件字节、Evidence Package、Analysis、
  Report、Source、Task/Run 或处理积压的路由，以及触发/重试/结束操作。
- 读取路由不得顺带执行采集、解析、索引或模型调用。恢复操作只排队或调用既有
  Task/Run/阶段服务；HTTP 进程不执行 Worker 工作。
- 除 `/health` 和登录外，所有路由（包括原件下载）由应用级中间件验证 Owner 会话；
  所有变更请求（包括登出）还必须验证与会话绑定的 CSRF token。新增子应用或路由
  不能绕过该边界。
- 本组路由读取单个报告，**没有 redirect 或"最新报告"别名**；Owner 从 `/api/reports`
  列表取得 id，不额外加跳转路由。

## Signatures

当前会话与读取路由（复用 `stocks.db.session_factory()`）：

```
GET  /health
POST /api/session/login
POST /api/session/logout
GET  /api/session
GET  /api/overview
GET  /api/sources
GET  /api/documents
GET  /api/documents/{document_id}
GET  /api/documents/{document_id}/facts
GET  /api/documents/{document_id}/original
GET  /api/facts
GET  /api/facts/{fact_id}
GET  /api/search
GET  /api/tasks
GET  /api/runs
GET  /api/runs/{run_id}
GET  /api/processing
GET  /api/reports
GET  /api/reports/{report_id}
GET  /api/reports/{report_id}/evidence/{chunk_id}
```

UUID 路径参数直接声明为 `uuid.UUID`，让 FastAPI 返回 `422` 而不是在路由里手写解析分支。
列表查询必须有排序和 `limit` 上限；公开页面通常为 `ge=1, le=200`。变更请求模型使用
`extra="forbid"`，恢复操作只接受既有服务定义的确认字段。

变更路由：

```
POST /api/tasks/{task_id}/runs
POST /api/runs/{run_id}/retry
POST /api/runs/{run_id}/continue
POST /api/runs/{run_id}/end
POST /api/processing/retry
```

## Contracts

- **按引用版本读取**：报告及其证据只按 Evidence Package 里保存的 `document_id` / `chunk_id`
  / `parse_batch_id` / `document_version` 解析，绝不跟随 `document_collection_states.latest_document_id`，
  也不重新访问来源站点。当前采集指针作为**独立字段**返回（`is_current_collection_pointer`、
  `collection_state`、`latest_collection_states`），使"新版本已入库"与"旧报告仍指向旧证据"能同时成立。
- **引用必须属于本 Run 的证据包**：片段不在该 Run 的 `selected_chunks` 内一律 `404`，
  不得代为检索其他资料填补。
- **分页稳定且有界**：时间序列使用 `(timestamp, UUID)` 游标，Source/Task 使用稳定
  text id，Document 版本使用版本号，处理积压使用 Document + 阶段元组；多取一行判断
  `has_more`，内部预读上限不得悄悄放宽公开页上限。
- **重复操作可判定**：已有活跃 Run 时手动触发返回现有 Run 与 `created=false`，不先
  解析 YAML 或模型配置。结束 Run、释放运行中 Document 阶段必须由请求体显式确认旧进程
  已停止；服务端仍校验实际状态和所有权。
- **状态类别不折叠**：Source 采集成功/无更新/部分失败/来源失败、更新预期
  （`fresh|stale|no_content|unknown_content_time|unconfigured`）和后处理积压分别返回。
  未配置 `expected_freshness` 时不能凭采集间隔推断 `stale`；配置了更新预期时，以
  `source_updated_at`（缺失时用 `published_at`）判断，不能把 `collected_at` 当披露时间。
- 响应只输出已持久化的安全字段。Run 快照、`ModelCall`、`error` 不得包含 API key、
  Authorization、endpoint 明文、带查询参数的 URL 或模型响应正文。外部 URL 通过统一
  过滤保留可追溯的 scheme/host/path，不带 query 和 fragment。
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
| 未认证、会话损坏或过期 | `401`，稳定错误码；能区分必需会话、无效和过期 |
| 变更请求缺少或错配 CSRF token | `403`，不改变状态；必须先于模型配置解析 |
| 手动触发或续扫所需模型配置不可用 | `503` + `MODEL_CONFIGURATION_UNAVAILABLE`，固定消息，不创建 Run，不回显环境变量、端点或解析器原文 |
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
  对象存储不可用时返回空正文或 `200`；用宽 `except Exception` 把配置错误伪装成可重试故障；
  把缺失模型凭据的 `ValueError` 原样返回成 `500` 或异常文本。

## Tests Required

- API 测试用真实 PostgreSQL（`TEST_DATABASE_URL`）+ 注入的 S3 读写函数，不依赖真实外部服务。
- 至少断言：引用版本不随新版本改变、失败 Run 不出现在报告列表、原件字节与哈希逐字节一致、
  HTML 被降级为 `text/plain` 且安全头存在、非 HTML 走 `application/octet-stream`、
  未配置存储 → `503` 稳定码且响应体不含 endpoint、外来 chunk → `404`、缺失行 → 缺口词表齐全。
- 认证覆盖：公开 health/login、私有读取与原件拒绝匿名、签名/过期/损坏 Cookie、
  登录失败限流、变更 CSRF、登出，以及外部错误中的 URL/凭据不泄露。
- 模型配置边界：手动触发和续扫在缺少环境变量或路由无效时返回稳定 `503`，
  响应不包含变量名、端点或异常原文，且不写入新 Run；已有活跃 Run 的重复触发
  仍先返回该 Run。
- 恢复覆盖：重复触发返回既有 Run、重试关联原 Run、续扫创建后续 Run、
  未确认结束返回 `OWNER_CONFIRMATION_REQUIRED`，确认后才释放所有权。
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
