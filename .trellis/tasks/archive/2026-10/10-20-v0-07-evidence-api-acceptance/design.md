# V0-07 技术方案

## 边界与数据流

- API 复用 Repository 与报告存储，读取指定历史 document_id，不重定向到当前源站或最新版本。
- 阶段验收记录写入本任务 research/，docs/validation.md 继续维护唯一验收契约。

## 建议代码落点

- `backend/src/stocks/api.py`
- `backend/src/stocks/s3.py`
- `compose.yaml`
- `docs/quickstart.md`

标注拟新增或随前置任务确定的路径是建议，不代表代码已存在；后续以实际模块组织为准。
仓库不使用 GitHub Runner CI；API、Compose 和部署检查只在测试服务器执行，已有说明见
`docs/quickstart.md` 与后端的远程测试规范。

## 输入输出与契约

`docs/interface.md`, `docs/research.md`, `docs/validation.md`, `docs/quickstart.md` 是唯一业务契约来源。输入引用前置任务的持久记录，输出满足 PRD 各 AC；不把本任务文档变成另一套字段规范。跨层 Schema、事务及失败语义在开工复核中结合已落地代码确定。

## 开工复核后的接口契约（2026-10-20）

复核结论：`backend/src/stocks/api.py` 目前只有 `/health` 与 `/api/documents`；`Report`、`Analysis`、`EvidencePackage`、`TaskRun`、`TaskStepResult`、`ModelCall` 已由 V0-06 持久化，`stocks.repository` 已提供 `get_document`、`get_collection_state`、`list_document_versions`、`list_parse_chunks`、`read_document_bytes`。因此 V0-07 只增加只读路由，不新增表、字段或迁移。

新增只读路由（全部 GET，复用 `session_factory()`，不写库）：

| 路由 | 返回 |
| --- | --- |
| `/api/reports` | 按 `created_at` 倒序的报告索引：`report_id`、`run_id`、`analysis_id`、`task_id`、`title`、`created_at`；`task_id` 过滤，`limit` 有界 |
| `/api/reports/{report_id}` | 报告正文 + Run 状态/objective/scope/时间/安全错误、Analysis 的 summary/findings/open_questions/evidence_refs、Evidence Package 的查询记录与覆盖统计（含其 `coverage`）、实际引用片段解析结果、每份 Document 的版本与当前采集指针 |
| `/api/reports/{report_id}/evidence/{chunk_id}` | 单个实际引用片段的正文、定位器、Document 版本/来源/时间/完整性，以及该 Document 的解析与索引状态 |
| `/api/documents/{document_id}` | Document 详情：元数据、同 `(source_id, dedupe_key)` 版本列表、当前采集状态指针、解析/索引状态、原件定位 |
| `/api/documents/{document_id}/original` | 该 Document 自己的 `snapshot_key` 原始字节；HTML/XHTML 以 `text/plain` 返回，其他未知类型 `application/octet-stream`；`?download=true` 时加 `Content-Disposition: attachment`；附带 `X-Content-Sha256` |

边界规则：

- 报告及其证据只按已保存的 `document_id`/`chunk_id` 读取，绝不重定向到 `latest_document_id` 或重新访问来源站点；响应同时给出该资料当前采集指针，使“旧报告仍指向旧版本”可验证。
- 引用片段必须属于该 Run 的 Evidence Package `selected_chunks`；不在包内返回 404，不代为检索其他资料。
- 未知 ID 与不属于任何报告的证据 → 404；对象存储未配置或读取失败 → 503 + 稳定错误码（`SNAPSHOT_STORAGE_UNAVAILABLE`），响应不泄露 endpoint、凭据或异常原文。
- 包内引用但 Document/Chunk 行已缺失 → 仍 `200`，以 `chunk_available=false` + 缺口词表达，不改写成 404 或空正文。Analysis/Report/Package 链断裂与 Package `selected_chunks`/`coverage.gaps` 形状非法 → 409（防御分支，当前 Schema 下 FK 均 NOT NULL + CASCADE，几乎不可达，不要求测试覆盖）。
- 未知文档请求解析失败时不静默返回空正文：以实际取得的字节按安全文本提供，并保留 Document 自身 `completeness`/`fetch_error` 字段。
- 响应只输出已持久化的安全字段；`ModelCall` 与 Run 快照不含 API key、Authorization、endpoint 明文或模型响应正文。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。外部服务凭据/目标环境缺失时保留验证限制，不标记实际接通。后续任务不得把本任务未完成能力当已实现。

## 验收记录落点

本任务 `research/` 只保存实际执行记录：真实来源采集计数、脱敏样例、数据库与对象存储核对结果、真实模型 Run 的 `run_id` 与报告读取路径，以及无法执行时的具体原因。测试服务器 SSH 凭据、模型密钥、S3 凭据一律不写入仓库。
