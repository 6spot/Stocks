# V1-04 验收记录

任务：按需 Fact 与结构化 API 字段映射。
唯一领域契约：`docs/data.md`（Fact、结构化 API）、`docs/execution.md`（Fact 阶段执行）、`docs/validation.md`（V1 统一处理）。
实施计划：[implement.md](implement.md)。

## 已实现（当前代码）

1. **通用 Fact 表**：单一 `facts` 表，`origin_method` 为 `llm_extraction` 或 `source_mapping`；类型相关细节放 `attributes` JSONB，**不按金融指标建专表**。字段覆盖 `fact_type`、`subject_name`、`symbol`、`metric_id`、`statement`、`value`、`unit`、`period_start/end`、`available_at`、`evidence_ref` 与处理器/配置指纹。
2. **证据定位**：文本 Fact 的 `evidence_ref` 记录 `chunk_id`/`parse_batch_id`/`position`；映射 Fact 记录 RFC 6901 `pointer` 与来源 `content_hash`/`document_version`。两者都能回到已存原始字节。
3. **幂等与修订**：`(document_id, fact_key)` 唯一约束；`fact_key` 由来源方法、事实类型、证据位置、指标与主体推导。重试/显式重处理不倍增相同事实；修订值来自新的 Document 版本（新 `document_id`），旧 Fact 不被覆盖，旧报告引用保持有效。
4. **未知单位不猜**：`value IS NULL OR unit IS NOT NULL` 表约束 + 确定性校验。抽取结果若只有数字没有单位，保留文字陈述并丢弃数字；mapper 的 `numeric` 字段若未配置 `unit` 直接拒绝配置。
5. **结构化 API 来源**：`connector: http_api` 与 RSS 互斥（API 不配置 `feed_url`，RSS 不配置 `mapper`）。采集只做一次有界请求，把原始响应字节与声明内容类型存成 Document 版本；相同字节复用、变化字节产生新版本。纯数值响应**不需要分类或向量**即可被 Fact 查询。
6. **Source Mapper**：确定性字段映射，无模型调用。字段缺失/null 不产生 Fact，全部缺失报 `MAPPING_NO_FIELDS_MATCHED`；数值路径上的非数值报 `MAPPING_VALUE_NOT_NUMERIC`；时间接受完整 ISO 8601 或 `YYYY-MM`/`YYYY` 期间并按 UTC 期间起点锚定。
7. **按需文本抽取**：仅当分类 `should_extract=true` 时调用 Generation Model；`false` 显式 `NOT_APPLICABLE` 跳过；未决判定以 `CLASSIFICATION_PENDING` 等待且不消耗尝试次数。抽取结果中不在当前解析批次的 `chunk_id` 会被拒绝（`EXTRACTION_CITATION_INVALID`）。
8. **惰性能力解析**：生成模型路由与 mapper 表都在真正领取 `facts` 阶段时读取；缺失/mapper 缺失只让该次尝试等待或失败，不影响解析、分类、索引与采集。纯结构化部署在完全没有生成模型配置时仍可映射。
9. **读取入口**：只读 `/api/facts`（来源/Document/类型/代码/指标过滤）、`/api/documents/{id}/facts`、`stocks.processing facts --source-id|--document-id-report`；都不触发抽取。

## 依赖关系变更

`facts` 的硬依赖从 `classify` 改为无硬依赖：
- 结构化响应的输入是原始字节，不是文本片段，`parse` 对它是 `unsupported`；
- `classify` 与 `facts` 之间是**适用性关系**而非输入依赖，处理器自行读取 `should_extract` 判定。

`_propagate_skips` 改为按传递闭包迭代，并区分「下游输入被跳过」与「决定其适用性的阶段被跳过」两种传播；`clear_wait_reason` 在分类完成时清掉 `CLASSIFICATION_PENDING`。

## AC 结果

| AC | 结论 | 依据 |
| --- | --- | --- |
| AC-1 一份原始 API 响应可映射多条 Fact 并追到具体字段；文本 Fact 可定位原片段 | 通过 | `test_mapping_produces_facts_that_cite_their_fields`、`test_extraction_stores_facts_citing_stored_chunks`、e2e（3 个配置字段 → 3 条 Fact，全部 `evidence_ref.kind == json_pointer` 且带 `content_hash`） |
| AC-2 未知单位保持未知，修订值与旧报告引用并存，重复处理不增相同事实 | 通过 | `test_fact_without_a_unit_is_refused_instead_of_guessed`、`test_number_without_a_unit_keeps_the_statement_and_drops_the_value`、`test_repeated_extraction_does_not_duplicate_facts`、`test_revision_on_a_new_version_keeps_the_old_fact`、e2e（`reprocess did not duplicate identical Facts`） |
| AC-3 should_extract=false 跳过 LLM 抽取但原文仍可检索；结构化响应不要求向量即可被 Fact 查询 | 通过 | `test_should_extract_false_skips_extraction_but_keeps_the_original`、`test_structured_response_is_mapped_without_a_model_or_vectors`、`test_undecided_classification_waits_instead_of_dropping_facts`、e2e（`the mapped response completed without any model or embedding route`） |

## 实际验证（测试服务器 `100.120.14.84`，任务隔离目录）

远程目录 `/tmp/stocks-v1-04-facts-c1`（editable 已指向该目录，`stocks.__file__` 已核对）；容器
`stocks-test-v1-04-postgres`（127.0.0.1:55462）、`stocks-test-v1-04-s3`（127.0.0.1:18365）。
数据库密码从容器环境注入，未写入仓库、命令行参数或本记录。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 全量 pytest | `pytest -q tests/` | **333 passed, 1 warning** |
| 全新空库结构 | 新建 `stocks_fresh104` 后 `alembic upgrade head` + `alembic check` | `0001`；`No new upgrade operations detected` |
| 真实子进程处理 smoke | `python tests/v1_02_processing_smoke.py`（真实 `worker --once`、`stocks.processing` 子进程、真实 PG + S3） | **36/36 PASS**，`summary: OK` |
| 真实 Fact 闭环 | `/tmp/v104_e2e.py`：`collect_source` 真实服务路径 + 真实 `stocks.collection`/`stocks.processing facts`/`reprocess` 子进程 + 真实 PG + S3 | **23/23 PASS**，`summary: OK` |

e2e 覆盖：`http_api` 来源按配置发出查询参数并返回一个 Document 版本 → 响应按自身内容类型与哈希存储 →
来源登记为 `http_api` → 相同字节复用（无重复版本）→ 真实 `stocks.processing facts` 子进程映射 3 条 Fact
（值 3.1/2.9、单位 percent 来自配置、`2026-08` 期间锚定为 `2026-08-01`、`available_at` 来自响应自身
`released_at`、文本字段映射为 statement）→ 显式 reprocess 不倍增相同事实 → 只读查询按来源返回全部 3 条。

修复过程中发现并修正的真实缺陷（均有对应测试或 e2e 断言）：

- `run_facts_stage` 在无 mapper/无生成路由时被 runner 记为 `failed`（且不自动重试），正确行为是 waiting；
- CLI `facts` 子命令缺少 `--sources-config`，导致真实部署中 mapper 永远不可见（`PROCESSOR_NOT_AVAILABLE`）；
- `map_document_facts` 用调用方 Session 之外的 `Document` 实例刷新时报 `InvalidRequestError`，改为在自身 Session 内加锁重读；
- 月度期间 `2026-08` 无法解析，导致真实 API 响应映射失败。

## 未完成 / 受限（不得视为已通过）

- **没有真实第三方 HTTP API 凭据**。e2e 用固定响应注入 `SafeFetcher`（fetch 强制 HTTPS，本地 HTTP mock
  不可用），其余环节（存储、映射、CLI、查询）都是真实执行。真实外部 API 的响应结构、限流与计费行为未验证。
- **没有真实 Generation Model 凭据**：文本抽取路径用 `httpx.MockTransport` 验证契约与失败边界，未验证任何
  具体厂商的抽取质量。
- mapper 只覆盖示例中的 CPI 指标；更多来源与指标由 V2-02 扩展，本任务不承诺指标覆盖面。
- 采集游标、可靠变化水位（V1-05）、Document/Fact 统一取材（V1-06）、受保护 Web 控制台与 ARM 部署未涉及。
- `/api/facts` 与其他 API 一样尚未做访问控制（V1-07）。
- Fact 未接入语义检索候选合并：`stocks.search` 仍只返回 Chunk，Document/Fact 统一去重属 V1-06。

## 清理

本次创建的 `stocks-test-v1-04-postgres`、`stocks-test-v1-04-s3` 容器、`/tmp/stocks-v1-04-s3-data`
数据目录、`/tmp/stocks-v1-04-facts-c1` 远程代码目录及 `/tmp/.v104pg`、`/tmp/v104.env`、临时
sources/models/e2e 文件在验证结束后删除。其他任务的容器与目录不属于本次，未触碰。
