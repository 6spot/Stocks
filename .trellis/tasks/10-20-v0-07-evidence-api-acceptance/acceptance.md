# V0-07 验收结论（2026-10-20）

## 已实现并验证

- 只读报告/证据/原件 API：`/api/reports`、`/api/reports/{id}`、
  `/api/reports/{id}/evidence/{chunk_id}`、`/api/documents/{id}`、
  `/api/documents/{id}/original`（`backend/src/stocks/api.py`）。
- 证据一律按 Run 的 Evidence Package 中保存的 Document/Chunk 版本解析，不跟随
  `document_collection_states.latest_document_id`，也不访问来源站点；当前采集指针作为独立字段返回。
- 原件返回存储的原始字节，media type 只降级（HTML/XHTML/文本 → `text/plain`，其他 →
  `application/octet-stream`），并带 `X-Content-Sha256`、`X-Content-Hash-Match`、
  `X-Content-Type-Options: nosniff`、`Content-Security-Policy: default-src 'none'`。
- 未新增表、字段或迁移；全新库 `alembic upgrade head` + `alembic check` 无漂移。

## AC 逐项结果

| AC | 结果 | 依据 |
| --- | --- | --- |
| AC-1 真实端到端 | **未验收（外部依赖阻塞）** | 测试服务器与本工作区均无模型 Provider 凭据（`MODEL_API_BASE_URL`/`MODEL_API_KEY`/`MODEL_NAME` 与可用 `/embeddings`）。缺 Embedding 无法产生真实 pgvector 索引，缺研究模型无法运行真实 Research Agent，因此"真实 SEC 采集 → 解析 → 索引 → 真实模型研究 → 阅读该报告"整链未跑。已实测 SEC Feed `HTTP 200` 可达；替代证据为真实 `store_snapshot` 写入真实对象存储并按引用读回 + uvicorn 全路由 smoke，二者都不能替代真实模型链路。 |
| AC-2 版本与失败边界 | **已验收** | `tests/test_report_api.py`：新版本入库后旧报告仍返回旧 Document 版本/旧片段/旧原件；失败 Run 不产生报告、不进入列表、不替换最后成功报告。真实 PostgreSQL + 全新库初始化下通过。 |
| AC-3 安全与运行边界 | **已验收** | HTML 原件以 `text/plain` 返回且不可能以可执行形式输出；冒烟服务仅 `127.0.0.1` 监听；`compose.yaml` 未改，仍 `127.0.0.1:8000:8000`；外部依赖与未验证项已在本文件与 `research/verification-2026-10-20.md` 记录。 |

## 实际执行的验证（均在测试服务器隔离目录）

- `pytest -q tests/test_report_api.py` → 7 passed；复核补强后 `test_report_api.py + test_api.py` → 9 passed
- `pytest -q tests/test_api.py tests/test_repository.py` → 10 passed
- `alembic upgrade head` + `alembic check` → 全新库无漂移
- `compileall` → 通过
- 真实 S3（本任务独立 seaweedfs 容器与 bucket）写入 + uvicorn HTTP smoke 两轮 → 通过
- 详细环境、输出与清理见 `research/verification-2026-10-20.md`

## 遗留项（不得视为已完成）

1. **V0 AC-1 真实闭环仍未验收**：需要 Owner 提供模型 Provider 凭据后，在测试服务器按
   quickstart 跑一次真实采集 + 真实索引 + 真实研究 + 阅读该报告，并把 `run_id`、脱敏计数与
   实际响应补进本任务记录。
2. `config/sources.yaml` 的联系邮箱仍是 `owner@example.com`，真实 SEC 采集前必须由 Owner 决定
   对外披露地址（SEC 对缺少联系身份的自动客户端返回 403）。
3. V0-08 验收中发现的解析器缺陷（正文直接位于 `article`/`main`/`body` 而无内层块元素时
   逐字符换行）仍未修复，归属后续任务；真实 HTML 入库时可能影响 Chunk 质量。

## V0 阶段退出条件

V0 的"证据阅读"契约（`docs/validation.md`）已由 API 实现并验证；"真实原件 + 真实模型研究"的
端到端演示因外部凭据缺失保持未完成。V0 阶段验收在 AC-1 补跑前不得标记为通过。
V1 各任务不依赖真实模型凭据即可继续推进（其自身验证使用固定响应与注入存储）。
