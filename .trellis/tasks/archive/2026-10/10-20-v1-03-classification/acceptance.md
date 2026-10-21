# V1-03 验收记录

任务：Decision Model 轻量分类与受控标签。
唯一领域契约：`docs/data.md`（分类与标签）、`docs/execution.md`（Decision 路由）、`docs/validation.md`（V1 统一处理）。
实施计划：[implement.md](implement.md)。

## 已实现（当前代码）

1. **受控词表**：`config/taxonomy.yaml` 保存 `version` 与 categories / event_types / topics / industries / regions 的稳定 `id`。`stocks.taxonomy` 由它派生发给模型的 JSON Schema，并对返回结果做确定性归一化。词表外的类别、主题、行业或地域、重复值、错类型、未知字段、空理由均为契约违规。
2. **薄 Decision Adapter**：`stocks.decision` 只实现 `openai_compatible` 一个协议（复用 `ModelClient.research_chat` 的有界重试、结构化输出与按序回退）；其他 `protocol` 在读取配置时直接报错，不假设兼容。业务 Schema 与 Prompt 约束由 Stocks 持有，不由 Provider 定义。
3. **预算输入**：分类只读取当前成功解析批次的片段，`CLASSIFICATION_MAX_CHARACTERS`（默认 6000，200–40000）限制实际发给模型的字符数，块间分隔符也计入预算；截断状态随输入记录。
4. **当前结果保存**：`documents.classification` JSONB 保存 `category`、`event_type`、`topics[]`、`companies[]`、`symbols[]`、`related_industries[]`、`regions[]`、`should_extract`，以及 `method`、`model_ref`、`model_alias`、`reported_model_name`、`taxonomy_version`、`taxonomy_fingerprint`、`classified_at`、`usage` 与 `reason_or_evidence_ref`（理由、解析批次、`chunk_ids`、输入字符数、是否截断）。**没有 `document_classifications` 或任何分类历史表**；处理状态沿用 `document_processing_states` 的 `classify` 行。
5. **接入 Worker**：`StageRegistry` 新增惰性 `classify_factory`，与 `index_factory` 同构。路由与词表只在真正领取 `classify` 阶段时读取，缺失/损坏/协议不支持只让该次尝试失败且不自动重试（`DECISION_ROUTING_UNAVAILABLE` / `TAXONOMY_UNAVAILABLE`），不影响解析、索引、采集或研究。
6. **should_extract 语义**：分类器把 `should_extract` 作为显式适用性写入 `classify` 阶段。`false` 让 `facts` 以 `NOT_APPLICABLE` 跳过；显式重分类得到 `true` 时被跳过的 `facts` 重新回到 `pending`；已完成的 Fact 不被重开。失败、超时、词表外结果**不会**被写成 `should_extract=false`。
7. **失败不破坏证据**：分类失败只记录该阶段的安全错误，不改写上一次成功保存的分类结果、原件、Chunk 与已有向量。
8. **API 读取**：`/api/documents` 与 `/api/documents/{id}` 的 Document payload 增加 `classification`（无结果时为 `null`），供后续控制台读取。

## 顺带修正的两个既有缺陷

- `run_next_document_stage` 在同一 `collected_at` 时按**阶段名**排序，会让 `classify` 抢在产生其输入的 `parse` 之前。改为按 `DOCUMENT_STAGE_ORDER`（parse → index → classify → facts）做同年龄 tie-break。
- 分类写入前先 `assert_stage_owned`，使被新解析批次作废的旧尝试在提交前即被拒绝，而不是依赖 `apply_stage_outcome` 的二次校验。

## AC 结果

| AC | 结论 | 依据 |
| --- | --- | --- |
| AC-1 宏观无股票资料可成功分类并语义召回；行业政策不会自动标为直接提及某股票 | 通过（语义召回部分见下方限制） | `test_macro_document_classifies_with_empty_company_tags`（`companies=[]`、`symbols=[]`、`related_industries=[banking]` 分开保存）；`test_company_mention_is_recorded_with_its_symbol` 反向覆盖明确提及；真实链路 e2e 的 `industry relevance kept separate from company mentions` |
| AC-2 正常成功路径一次分类返回标签和抽取决策；超词表或无效结果进入有限重试/失败 | 通过 | `test_one_call_returns_tags_and_the_extraction_decision`（断言恰好 1 次请求、strict JSON Schema、片段输入）；`test_out_of_vocabulary_answer_is_retried_then_fails_instead_of_false`；`test_provider_failure_is_a_diagnosable_error_not_a_false_decision`；`test_unimplemented_decision_protocol_is_refused`；e2e 的 `exactly one Decision Model call` 与 `CLASSIFICATION_REJECTED` 路径 |
| AC-3 显式重分类更新当前 JSONB、不新增 Document，不自动重抽取已完成 Fact | 通过 | `test_reclassification_updates_the_current_jsonb_without_a_new_document`（1 个 Document、facts 仍 completed）；`test_reclassification_from_no_extraction_to_extraction_reopens_facts`；`test_failed_classification_keeps_the_previous_result_and_the_original`；`test_stale_classification_owner_cannot_overwrite_a_newer_result`；e2e 的 `reclassification created no new Document version` |

## 实际验证（测试服务器 `100.120.14.84`，任务隔离目录）

远程目录 `/tmp/stocks-v1-03-classification-c1`（editable 已指向该目录，`stocks.__file__` 已核对为
`/tmp/stocks-v1-03-classification-c1/backend/src/stocks/__init__.py`）；容器
`stocks-test-v1-03-postgres`（127.0.0.1:55452）、`stocks-test-v1-03-s3`（127.0.0.1:18355）。
数据库密码从容器环境注入，未写入仓库、命令行参数或本记录。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 分类专项 | `pytest -q tests/test_classification.py` | **30 passed** |
| 受影响处理/Worker/Provider/API | `pytest -q tests/test_classification.py tests/test_worker_processing.py tests/test_processing_retry.py tests/test_providers.py tests/test_report_api.py` | **101 passed, 1 warning** |
| 执行/API/解析/索引 | `pytest -q tests/test_execution.py tests/test_api.py tests/test_processing.py tests/test_embeddings.py` | **98 passed** |
| 调度/采集/仓储/研究/S3/检索 CLI | `pytest -q tests/test_scheduler.py tests/test_collection.py tests/test_repository.py tests/test_research.py tests/test_s3.py tests/test_search_cli.py` | **101 passed** |
| 全新空库结构 | 新建 `stocks_fresh_v103` 后 `alembic upgrade head` + `alembic check` | `0001`；`No new upgrade operations detected` |
| 结构落地核对 | `information_schema.columns` 查 `documents.classification` | `classification|jsonb|YES` |
| 真实子进程处理 smoke | `python tests/v1_02_processing_smoke.py`（真实 `worker --once`、`stocks.processing` 子进程、真实 PG + S3） | **36/36 PASS**，`summary: OK` |
| 真实 HTTP 分类闭环 | `/tmp/v103_decision_e2e.py`：本地 OpenAI 兼容 stub + 真实 `stocks.processing parse` / `reprocess --execute` 子进程 | **26/26 PASS**，`summary: OK` |

e2e 覆盖：真实 CLI 解析 → 真实 `reprocess --stage classify --execute` → 断言恰好 1 次
`/v1/chat/completions` 调用、Bearer 头、`strict` JSON Schema 来自 `config/taxonomy.yaml`、
系统提示把文档标为不可信数据、模型只读到已解析片段（不含原始 HTML）且不超预算 → 阶段
`completed` + `processor_version=decision-model-v1` + `applicability={"requires_extraction": true}`
→ JSONB 中 category/topics/related_industries/companies/symbols/should_extract/method/model_ref/
taxonomy_version/usage/reason_or_evidence_ref 全部正确 → 未新增 Document 版本 → `status` 仍可读。

## 未完成 / 受限（不得视为已通过）

- **没有真实第三方 Decision Provider 凭据**。上述 e2e 用本地 OpenAI 兼容 stub 验证协议、
  词表、存储与传播链路，不证明任何具体厂商的模型质量、真实限流或计费行为。真实 Provider
  闭环与词表规模需在 V1-09 阶段验收中核对。
- **AC-1 的“语义召回”部分未在本任务验证**：分类结果进入 JSONB 后，按标签过滤检索尚未
  接入 `stocks.search`（V1-06 统一取材负责），本任务只验证了标签被正确保存与可读取。
- Fact 表与抽取/Source Mapper（V1-04）未实现：`facts` 阶段只可能因分类显式
  `should_extract=false` 而 `NOT_APPLICABLE` 跳过，或保持 `pending` + `PROCESSOR_NOT_AVAILABLE`。
- `docs/data.md` 的分类字段表仍是归属契约；本任务未新增字段，只是把既有字段落到实现。
- PA-1 真实模型闭环、PA-2 既有 HTML 缺陷、V1-05 变化水位、受保护的 Web 控制台、ARM 部署
  验收不在本任务，未验证也未关闭。

## 清理

本次创建的 `stocks-test-v1-03-postgres`、`stocks-test-v1-03-s3` 容器、`/tmp/stocks-v1-03-s3-data`
数据目录、`/tmp/stocks-v1-03-classification-c1` 远程代码目录与 `/tmp/.v103pg`、`/tmp/v103.env`
临时文件在验证结束后删除。其他任务（v0-02/v0-03）的容器与目录不属于本次，未触碰。
