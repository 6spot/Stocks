# V0-07 实际验证记录（2026-10-20）

所有检查均在测试服务器 `100.120.14.84` 的本任务隔离目录执行；本机只做编辑与打包。
凭据（数据库密码、S3 密钥）为本次临时生成，不写入仓库；远程只输出脱敏字段。

## 环境

- 代码目录：`/tmp/stocks-v0-07-evidence-api-01`（当前工作区 tar 传输，排除 `.git/.venv/.env/__pycache__/._*`）
- Python：`3.12.15`（复用 `/opt/stocks-test-runtime/tool-venvs/stock-tools`，`uv pip install --no-deps --force-reinstall -e .` 重新指向本任务目录，`stocks.__file__` 已核对为 `.../stocks-v0-07-evidence-api-01/backend/src/stocks/__init__.py`）
- 数据库：本任务新建容器 `stocks-test-v0-07-evidence-api-postgres`，`pgvector/pgvector:0.8.7-pg17-bookworm`，绑定 `127.0.0.1:55441`，随机测试密码，独立数据库
- 对象存储：本任务新建容器 `stocks-test-v0-07-evidence-api-s3`，`chrislusf/seaweedfs`，绑定 `127.0.0.1:18345`，独立 bucket `stocks-v007`
- 说明：先尝试复用其他任务遗留的 seaweedfs（18334），其 master 无可用 data node、PutObject 报 `No writable volumes`，属其他任务资源，按规范不改动它，改为为本任务新建容器。

## 结构一致性（本轮无模型/表变更，仍按规范核对）

```
alembic upgrade head   -> Running upgrade -> 0001, Initial intelligence storage.
alembic check          -> No new upgrade operations detected.
```

## 单元与回归测试

```
.venv/bin/python -m pytest -q tests/test_report_api.py            -> 7 passed
.venv/bin/python -m pytest -q tests/test_api.py tests/test_repository.py -> 10 passed
.venv/bin/python -m compileall -q src/stocks/api.py tests/test_report_api.py -> OK
```

`tests/test_report_api.py` 覆盖：报告按引用版本读取（新版本入库后旧报告不变）、失败 Run 不产生/不替换报告、原件字节与哈希一致且 HTML 降级为安全文本、非 HTML 走 `application/octet-stream`、附件下载头、未配置对象存储时 503 稳定错误码且不泄露 endpoint、引用不在包内 404、片段缺失/未索引/部分内容作为可见缺口而非沉默成功、`/api/documents` 原有形状不变。

测试首版存在自身缺陷（Session 关闭后访问已分离 ORM 实例，`DetachedInstanceError`），已改为在会话内取出纯 ID 后断言；这不是产品代码缺陷。

## 质量复核后的二次验证（同一隔离环境）

静态复核无 BLOCKING，采纳后改动了 `api.py`（chunk 引用比较改用 UUID 归一化、
`coverage.gaps` 形状非法转 409）、`test_report_api.py`（补 `/api/documents` 多版本行为与
单片段路由复用断言）及三份文档，因此重跑受影响范围：

```
pytest -q tests/test_report_api.py tests/test_api.py -> 9 passed
compileall src/stocks/api.py tests/test_report_api.py -> OK
uvicorn 127.0.0.1:8124 + 真实 S3 重写入 smoke：
  report detail 200 / coverage_gaps=[] / cited=1 / document_version=1 / 正文为引用片段
  单片段路由 200；大写形式 UUID 引用同样 200（归一化生效）
  外来 chunk 404；原件 200 且 x-content-hash-match: true
```

## 真实 HTTP smoke（uvicorn + 真实 S3 + 独立数据库）

服务：`.venv/bin/uvicorn stocks.api:app --host 127.0.0.1 --port 8123`，`ss -ltnp` 确认仅 `127.0.0.1:8123` 监听。

数据写入走真实 `stocks.repository.ingest_document(store=stocks.s3.store_snapshot)`，即真实 boto3 PutObject：

- 快照对象键 `sources/sec/58dacb7f…3e5c`，与 Document `content_hash` 一致，对象存储实际可读回。

实际响应（脱敏）：

| 检查 | 结果 |
| --- | --- |
| `GET /health` | `{"status":"ok"}` |
| `GET /api/reports` | 1 条，含 `report_id/run_id/analysis_id/task_id/title/created_at/run_status=succeeded` |
| `GET /api/reports/{id}` | 顶层键 `report, run, analysis, evidence_package, evidence, cited_chunk_ids, coverage_gaps, latest_collection_states`；`cited_chunk_ids` 1 项；`coverage_gaps=[]`；片段 `document_version=1`、正文与定位可读 |
| `GET /api/documents/{id}/original` | `200`；`x-content-sha256` 等于返回字节的哈希且与记录 `content_hash` 一致；`x-content-hash-match: true`；`content-type: text/plain`（源内容为 HTML）；`x-content-type-options: nosniff`；`content-security-policy: default-src 'none'`；正文为存储的原始字节，未被执行也未改写 |
| `GET /api/reports/{id}/evidence/{chunk}` | 返回该 Run 的 `evidence_package_id`、`as_of` 与片段/Document 版本 |
| 未知 `report_id` | `404` |
| 不属于该报告证据包的 `chunk_id` | `404` |

## 真实来源连通性（仅探测，未采集入库）

`https://www.sec.gov/news/pressreleases.rss` 在测试服务器上返回 `HTTP 200`（使用 `config/sources.yaml` 当前的占位联系身份）。

## 未完成 / 受限

- **AC-1 真实端到端未执行**：测试服务器与本工作区均无模型 Provider 凭据（`MODEL_API_KEY`/`MODEL_NAME`/可用 `/embeddings` 端点）。缺 Embedding 无法产生真实 pgvector 索引，缺研究模型无法运行真实 Research Agent，因此"真实来源采集 → 索引 → 真实模型研究 → 阅读该报告"整链未验收。
- 真实 SEC 采集前需 Owner 决定对外披露的联系邮箱（规范与 quickstart 都要求替换 `owner@example.com`），本轮未擅自发送个人邮箱。
- AC-2、AC-3 已由上述数据库/HTTP 实证覆盖；AC-1 保持未验收。
- 未运行与本次改动无关的全量套件；`collection/processing/embeddings/research/execution/providers/s3` 等文件本轮未改动，故未重复运行（V0-08 归档时同库全量 168 passed）。

## 待清理

容器 `stocks-test-v0-07-evidence-api-postgres`、`stocks-test-v0-07-evidence-api-s3`，目录 `/tmp/stocks-v0-07-evidence-api-01`、`/tmp/stocks-v0-07-s3-data`、远程临时脚本与 `/root/.v007-db-pass`。
