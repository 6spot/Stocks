# Journal - me (Part 1)

> AI development session journal
> Started: 2026-10-20

---



## Session 1: Bootstrap development guidelines
<!-- trellis-session: v=2 fp=4457ff4c72bb9597 -->

**Date**: 2026-10-20
**Task**: Bootstrap development guidelines
**Branch**: `main`

### Summary

Defined backend and frontend engineering guidelines, with focused testing and no default full test suite; archived bootstrap task.

### Main Changes

- Added Trellis backend and frontend development specs

### Git Commits

| Hash | Message |
|------|---------|
| `354a843` | docs: define Trellis development guidelines |

### Testing

- [OK] Checked Markdown formatting and local links; no code tests for documentation-only changes

### Status

[OK] **Completed**


## Session 2: Plan V0 V1 V2 delivery roadmap
<!-- trellis-session: v=2 fp=9061a526abe4dffa -->

**Date**: 2026-10-20
**Task**: Plan V0 V1 V2 delivery roadmap
**Branch**: `main`

### Summary

Planned 21 delivery tasks under V0/V1/V2 milestones with dependencies and acceptance criteria; retained all 25 records in planning and archived this session in the journal.

### Main Changes

- Created roadmap, three milestones, and 21 delivery tasks with PRD/design/implementation plans and context manifests.
- Preserved pending delivery tasks; no product implementation or task completion was claimed.

### Git Commits

| Hash | Message |
|------|---------|
| `45ba7e5` | docs: plan V0 V1 V2 delivery tasks |

### Testing

- [OK] Validated all 25 task context manifests, parent/child links, dependency DAG, Markdown links, and acceptance coverage.
- [OK] git diff --cached --check passed; no product tests run for this documentation-only planning change.

### Status

[OK] **Completed**

### Next Steps

- Review and implement V0-01 raw document persistence and immutable versions.


## Session 3: V0-01 raw document versions
<!-- trellis-session: v=2 fp=0235b8f37e091193 -->

**Date**: 2026-10-20
**Task**: V0-01 raw document versions
**Branch**: `main`

### Summary

Implemented versioned raw Document ingestion with S3-first immutable snapshots, PostgreSQL identity constraints, duplicate-byte reuse, read APIs, API metadata, and backend tests. Documented the remote Docker test environment and validated a fresh pgvector schema plus 11 passing backend tests.

### Git Commits

| Hash | Message |
|------|---------|
| `5309ae5` | feat: persist versioned raw documents |
| `79f2f6f` | docs(trellis): document remote backend test environment |

### Status

[OK] **Completed**


## Session 4: V0-02 real source collection and frontend testing guidelines
<!-- trellis-session: v=2 fp=2eece081fe3fd2fe -->

**Date**: 2026-10-20
**Task**: V0-02 real source collection and frontend testing guidelines
**Branch**: `feat/v0-02-real-source-collection`

### Summary

Implemented safe SEC RSS/Atom collection with article snapshot persistence, immutable Document versions plus separate collection state, partial/error handling, SSRF and resource limits. Documented backend collection/testing rules and completed the archived bootstrap frontend spec set. Verified fresh pgvector schema and related tests in the remote Python 3.12 environment.

### Git Commits

| Hash | Message |
|------|---------|
| `9496d61` | feat: collect complete article snapshots from SEC |
| `0a34055` | docs(trellis): capture collection and frontend testing rules |

### Status

[OK] **Completed**


## Session 5: V0-03 parsing and locatable chunks
<!-- trellis-session: v=2 fp=03c3ea13e44a18ca -->

**Date**: 2026-10-20
**Task**: V0-03 parsing and locatable chunks
**Branch**: `feat/v0-02-real-source-collection`

### Summary

Implemented immutable parse batches and locatable HTML/plain-text Chunks with CLI/API status, reprocessing protection, and parsing/evidence test guidance. Verified relevant tests remotely on Python 3.12 with fresh PostgreSQL/pgvector; real SEC AC-4 remains explicitly unaccepted.

### Main Changes

- Added HTML/XHTML/plain-text parsing, source ranges, semantic locators, and budgeted overlapping chunks.
- Added immutable parse batches, latest-attempt/current-success state, safe reprocessing, and S3/API configuration validation.
- Documented parsing behavior and frontend/backend testing rules; archived V0-03.

### Git Commits

| Hash | Message |
|------|---------|
| `409da44` | feat: parse documents into immutable locatable chunks |
| `96ba369` | docs(trellis): capture parsing and evidence testing rules |

### Testing

- [OK] Remote Python 3.12.15 with fresh PostgreSQL 17/pgvector: 47 passed (processing, repository, S3).
- [OK] Alembic upgrade and check passed on an empty database; Ruff passed; local related suite: 38 passed, 9 skipped.
- [OK] Real SEC AC-4 remains unverified because the configured contact identity and current source evidence chain are not trustworthy.

### Status

[OK] **Completed**

### Next Steps

- Start V0-04 only on a later user instruction.


## Session 6: V0-04 embedding indexing and pgvector search
<!-- trellis-session: v=2 fp=c567eca7c8fb9fc3 -->

**Date**: 2026-10-20
**Task**: V0-04 embedding indexing and pgvector search
**Branch**: `feat/v0-04-vector-search`

### Summary

Implemented current-batch Chunk embeddings, filtered pgvector semantic search, bounded exact search, adjacent context reads, visible index coverage, provider compatibility checks, and focused PostgreSQL tests. Verified 59 focused tests, fresh-schema Alembic consistency, pgvector HNSW configuration, and documented the remaining limit that no live external Embedding Provider was called.

### Git Commits

| Hash | Message |
|------|---------|
| `7056ff6` | feat: add pgvector embedding indexing and evidence search |
| `b786454` | docs(trellis): capture vector search testing rules |
| `9152598` | docs: plan staged delivery boundaries |
| `6e4b12c` | chore(task): record V0-04 implementation progress |

### Status

[OK] **Completed**


## Session 7: V0-05 manual research Task and Agent
<!-- trellis-session: v=2 fp=25233291737da60f -->

**Date**: 2026-10-20
**Task**: V0-05 manual research Task and Agent
**Branch**: `feat/v0-05-manual-task-agent`

### Summary

Delivered configured manual research Task/Run execution with immutable snapshots, durable step/model records, bounded Provider fallback, test-server-only verification, and V0-05 archive.

### Main Changes

- Added TaskDefinition, TaskRun, TaskStepResult, and ModelCall persistence plus atomic active-Run ownership.
- Added YAML Task/Agent configuration, ordered repository search and research Agent execution, evidence references, and safe CLI output.
- Extended Provider calls with per-target timeouts, bounded retry/fallback, JSON-schema validation, and safe call metadata.
- Documented the mandatory test-server-only workflow and removed GitHub Runner test CI.

### Git Commits

| Hash | Message |
|------|---------|
| `34757a1` | feat: add manual configured research task runs |
| `a6c3e28` | docs(trellis): confine verification to the test server |
| `f77544a` | chore(task): record V0-05 implementation and acceptance |

### Testing

- [OK] Remote Python 3.12.15 + PostgreSQL 17.11/pgvector: 153 relevant tests passed.
- [OK] Fresh database alembic upgrade head and alembic check passed; compileall passed.
- [OK] Real external Provider and real Repository evidence Agent run remain unverified.

### Status

[OK] **Completed**

### Next Steps

- Start V0-06 evidence package, analysis, and report persistence, reusing the same test server without deleting other tasks' containers.


## Session 8: V0-06 evidence package, analysis and report persistence
<!-- trellis-session: v=2 fp=6c67ef29d59b2e60 -->

**Date**: 2026-10-20
**Task**: V0-06 evidence package, analysis and report persistence
**Branch**: `feat/v0-06-evidence-analysis-report`

### Summary

Persist a per-Run Evidence Package and derive Analysis plus Report from it, with citation validation enforced inside the model route; all verification ran on the test server.

### Main Changes

- Added stocks.research.service: bounded select_evidence queries persist objective/scope/as_of/rule version/budget, per-query role and gaps, and selected Chunk Document/parse-batch versions; unknown candidate counts stay null.
- research_agent now consumes only the saved package; findings are validated against selected citations inside the provider call, and Analysis plus deterministic Markdown Report commit atomically with the succeeded StepResult.
- Renamed the repository_search step kind to select_evidence across config validation, tasks.yaml, fresh-schema definitions and docs; evidence refs in StepResult/Analysis/Report store identity fields only.
- Tracked Trellis tooling (scripts, bundled skills, agents config, workflow.md, spec/guides) and planned V0-08 archive review fixes for the version pointer, partial-retry, malformed feed URL and charset defects.

### Git Commits

| Hash | Message |
|------|---------|
| `1aad2fb` | chore(trellis): track project workflow tooling |
| `fe2c349` | docs(trellis): plan V0-08 archive review fixes |
| `36b467d` | feat: persist evidence packages, analyses and reports |
| `0afafde` | docs(trellis): record constraint parity and host tool gaps |
| `c38c59f` | chore(task): record V0-06 implementation and acceptance |
| `91de27a` | chore(task): archive 10-20-v0-06-evidence-analysis-report |
| `165db9e` | docs(trellis): repair task links after archiving |

### Testing

- [OK] [OK] Test server 100.120.14.84 isolated dir, Python 3.12.15 + PostgreSQL 17.11/pgvector 0.8.7: tests/test_research.py + tests/test_execution.py 59 passed.
- [OK] [OK] Affected suites (embeddings, search_cli, repository, processing, api, collection, providers, s3) 102 passed; compileall passed.
- [OK] [OK] Fresh database alembic upgrade head plus alembic check reported no drift; 16 public tables and uq_evidence_packages_run / uq_analyses_run / uq_reports_run confirmed.
- [OK] [BLOCKED] No real model Provider credentials on the test server, so the real closed-loop report remains unverified; V0-07 owns it.

### Status

[OK] **Completed**

### Next Steps

- V0-08 collection and version fixes must land before V0-07 final end-to-end acceptance; V0-07 adds the report/evidence read API and the real source plus real model run.


## Session 9: V0-08 collection and version pointer fixes
<!-- trellis-session: v=2 fp=39d816910e33551c -->

**Date**: 2026-10-20
**Task**: V0-08 collection and version pointer fixes
**Branch**: `feat/v0-08-document-collection-fixes`

### Summary

Fixed the four defects found reviewing archived V0-01/V0-02: the latest-content pointer now follows the collection state, partial content always retries, malformed feed article links fail only their own entry, and the declared charset survives acquisition.

### Main Changes

- get_latest_document() reads document_collection_states.latest_document_id instead of the highest version number, so A -> failed partial -> recovered A keeps pointing at A while both immutable versions stay readable.
- _should_recheck() handles partial first, so a source that publishes no time signal still gets a full-text retry; unknown times stay unknown and complete unchanged items keep skipping.
- Feed article links that cannot be resolved become that entry's INVALID_ARTICLE_URL with position and reason only; the rest of the batch continues. Malformed redirect Location is rejected by httpx and stays on the existing REQUEST_FAILED path, so no unreachable branch was added.
- Content-type allow listing compares only the media type while the stored content type keeps its charset, letting the parser strictly decode non-UTF-8 bodies without re-encoding the original bytes.
- Corrected V0-01/V0-02 archive records that still claimed the tasks were unimplemented, and repaired task links after two archive moves.

### Git Commits

| Hash | Message |
|------|---------|
| `828f5be` | fix: align recent content pointer and collection input edges |
| `fe7c424` | docs(trellis): correct V0-01/V0-02 archive status notes |
| `dbed88b` | chore(task): record V0-08 collection fixes and acceptance |
| `9465053` | docs(trellis): point task references at the archived V0-08 |
| `bd002bb` | docs(trellis): record httpx streaming fixture pitfalls |

### Testing

- [OK] [OK] Test server 100.120.14.84, fresh task database in the reused pgvector container: 168 passed across repository, collection, processing, api, s3, embeddings, search_cli, research, execution and providers.
- [OK] [OK] alembic upgrade head plus alembic check reported no drift; compileall passed.
- [OK] [FOUND] Pre-existing V0-03 parser defect: text directly inside article/main/body without an inner block element is chunked one character per line. Recorded, not changed in this task.

### Status

[OK] **Completed**

### Next Steps

- V0-07 report and evidence read API plus the real source and real model end-to-end acceptance; it must read recent content through the collection state pointer, not the highest version number.


## Session 10: V0-07 报告证据读取 API 实现与远程验收
<!-- trellis-session: v=2 fp=2dffe642f9837479 -->

**Date**: 2026-10-21
**Task**: V0-07 报告证据读取 API 实现与远程验收
**Branch**: `feat/v0-07-evidence-api-acceptance`

### Summary

新增只读报告/证据/原件 API，按引用版本读取且不跟随采集指针；真实 S3 往返 + uvicorn smoke 通过；AC-2/AC-3 已验收，AC-1 因测试服务器缺模型凭据保持未验收。

### Git Commits

| Hash | Message |
|------|---------|
| `bc70735` | feat: read reports and the exact evidence they cited |
| `01b7cc4` | docs(trellis): record the V0-07 evidence API contracts and acceptance |
| `9f9e26b` | chore: record journal |
| `5c0def3` | docs(task): record V0-07 acceptance with AC-1 still unverified |
| `2d7f296` | chore(task): archive 10-20-v0-07-evidence-api-acceptance |
| `fbc5381` | docs(trellis): keep V0 open until the real closed loop is accepted |

### Notes

- 分支 `feat/v0-07-evidence-api-acceptance` 已推送到 origin；远程资源（本任务自建的
  postgres/seaweedfs 容器与隔离目录）已全部清理，其他任务容器未动。
- V0-07 已归档，但 V0 父任务故意保持 `planning`：`task.py list` 的 8/8 done 只反映子任务
  归档，不代表阶段退出条件达成。
- Owner 决定将真实闭环延后到最后统一验收，登记于
  `.trellis/tasks/10-20-stocks-roadmap/pending-acceptance.md`（PA-1 真实模型闭环、
  PA-2 解析器逐字符换行缺陷）。

### Status

[OK] **Completed**（V0-07 API 与 AC-2/AC-3 已验收；AC-1 转统一验收轮次）

### Next Steps

- V1-01 独立 Worker 定时调度与 Run 恢复：开工前需与 Owner 确认 V1-01/V1-02 边界
  （采集步骤种类归属 V1-02，V1-01 只做 trigger schedule + Worker 进程 + 异常 Run 结束）。
- 统一验收轮次：补模型凭据与 SEC 联系邮箱后，按 PA-1 跑 V0 真实闭环并回填记录。


## Session 11: 统一前端技术选型与开发规范
<!-- trellis-session: v=2 fp=40923586cab64763 -->

**Date**: 2026-10-21
**Task**: 统一前端技术选型与开发规范
**Branch**: `feat/v0-07-evidence-api-acceptance`

### Summary

统一 Nova 风格、Lucide、Motion、react-resizable-panels、dnd-kit 与 Zustand 的前端规范，同步架构、界面文档和 V1-08 规范引用。按用户要求不创建 Trellis 任务，以会话记录归档本轮工作；V1-08 保持 planning。

### Main Changes

- 补充设计变量、图标可访问性、动画与减少动态效果、响应式面板及拖拽交互规则。
- 确定 Zustand 客户端共享状态、selector、持久化白名单及上下文清理边界。
- 保留此前 Pi/Trellis 多模型配置相关未提交改动，未纳入本轮提交。

### Git Commits

| Hash | Message |
|------|---------|
| `dde68da` | docs(frontend): standardize UI stack and development conventions |

### Testing

- [OK] 本轮仅修改文档，已人工审阅 Git 差异及相关表述；未运行测试、构建、lint 或类型检查。

### Status

[OK] **Completed**

### Next Steps

- V1-08 实施时按实际依赖版本确认 API，并在指定测试服务器执行相关验证。


## Session 12: V1-01 scheduled Worker and Owner recovery delivery
<!-- trellis-session: v=2 fp=ff11d98ab57e6db5 -->

**Date**: 2026-10-21
**Task**: V1-01 scheduled Worker and Owner recovery delivery
**Branch**: `feat/v0-07-evidence-api-acceptance`

### Summary

交付并推送独立采集/研究定时 Worker、持久调度、Owner 确认停机后的结束/重试及证据复用。测试服务器 142 项相关 pytest、新库初始化/结构检查、容器 CLI smoke 9 阶段、SIGKILL/end/retry 18 项通过；本任务远程资源已清理。PA-1 真实模型闭环仍延后，默认 PyPI 构建下载超时，测试镜像副本构建成功。保留无关 Pi/Trellis 工作，按用户要求转入 V1-02 规划。

### Git Commits

| Hash | Message |
|------|---------|
| `8f67efe` | feat(execution): add scheduled workers and owner-confirmed run recovery |

### Status

[OK] **Completed**


## Session 13: Pi/Trellis/Magpie 角色组与子进程反馈配置归档
<!-- trellis-session: v=2 fp=7ee439b9959a445d -->

**Date**: 2026-10-21
**Task**: Pi/Trellis/Magpie 角色组与子进程反馈配置归档
**Branch**: `feat/v0-07-evidence-api-acceptance`

### Summary

按用户要求提交并归档配置任务：主会话使用 OpenAi 组，Implement/Check 使用 Deepseek/Qwen 组；修正 Pi 平台与派发提示，补齐公开进度、失败/重试/静默展示和 BLOCKED 返回。准备离线回归用例，保留运行验收未执行状态；BatchMode 未提供可用认证，未进行密码认证或本机测试，归档不表示验收通过。

### Git Commits

| Hash | Message |
|------|---------|
| `431abb1` | fix(pi): bind role groups and report subagent progress and blockers |

### Status

[OK] **Completed**


## Session 14: 完成 V1-02 文档处理恢复
<!-- trellis-session: v=2 fp=1195620b4a5bc992 -->

**Date**: 2026-10-21
**Task**: 完成 V1-02 文档处理恢复
**Branch**: `feat/v0-07-evidence-api-acceptance`

### Summary

实现统一文档处理阶段队列、独立 Worker 处理槽位及有界重试与 Owner 恢复入口；远程验证 106 项相关测试、34/34 子进程 smoke，新数据库初始化与 alembic check 通过。V1-02 已提交并归档。

### Git Commits

| Hash | Message |
|------|---------|
| `d192a4f` | feat(processing): add resumable document stages and backlog recovery |

### Status

[OK] **Completed**


## Session 15: Inline Pi Trellis and remove OpenCode integration
<!-- trellis-session: v=2 fp=2a5ed1f8112ac83d -->

**Date**: 2026-10-21
**Task**: Inline Pi Trellis and remove OpenCode integration
**Branch**: `chore/pi-trellis-inline-opencode`

### Summary

Removed Pi Trellis subprocess dispatch, routed Pi workflow through the main session, and deleted the project .opencode integration. Preserved Trellis task context and shared CLI OpenCode support. Archived task 10-21-pi-trellis-inline-workflow; validation limitations are recorded in its acceptance.md.

### Git Commits

| Hash | Message |
|------|---------|
| `86d5376` | chore(pi): inline Trellis and remove OpenCode integration |

### Status

[OK] **Completed**


## Session 16: V1-05 提交可见变化水位与可继续扫描
<!-- trellis-session: v=2 fp=1f49a9187cb7b757 -->

**Date**: 2026-10-21
**Task**: V1-05 提交可见变化水位与可继续扫描
**Branch**: `main`

### Summary

实现并验证 V1-05：变化记录与业务写入同事务（共享计数行锁使水位顺序等于提交顺序），六个写入点接入；研究 Run 固定 (start,end] 窗口与版本化扫描检查点，恢复同 Task+Scope 最新有效未完成检查点，retry 复制来源窗口；原始变化页在 SQL 中先过滤后 LIMIT 并返回 (watermark, change_id) 续点；最终 Step/Analysis/Report/游标/Run 成功原子提交，失败或失去所有权不推进游标。测试服务器全新库 alembic upgrade head 与 alembic check 均 exit 0，watermark 28 + execution 59 用例通过（无 skip），全量 366 passed。AC-1 用两个真实 PostgreSQL 连接断言快写入者被计数行锁阻塞。修正 docs/data.md 遗留过期陈述与 docs/execution.md 检索入口路径。V1-06 统一增量/历史证据合并仍未实现。

### Git Commits

| Hash | Message |
|------|---------|
| `281e065` | feat(changes): make research increments follow committed changes |

### Status

[OK] **Completed**


## Session 17: V1-06 Document/Fact 统一增量取材与覆盖
<!-- trellis-session: v=2 fp=8f93b65b4923d16e -->

**Date**: 2026-10-21
**Task**: V1-06 Document/Fact 统一增量取材与覆盖
**Branch**: `main`

### Summary

实现并验证 V1-06：select_evidence 同时读取固定窗口内的增量 Document/Fact 与历史语义背景，按 Document 合并为一个 evidence_groups 证据链；同一 Chunk 不因增量与语义双路径重复计数，Fact 挂在来源 Chunk 上，Document 与 Fact 不再算两条独立支持。新增 evidence_groups JSONB、current_document_hits、facts_for_documents、EvidenceCollector 与冲突标记；覆盖统计区分 discovered/screened/selected/证据组/filtered-out/unexamined，触顶时未知保持 null。测试服务器全新库 alembic upgrade head 与 check 均 exit 0，全量 373 passed（含 7 个新增用例），无 skip。跨 Document 转载识别与高级跨源冲突解释留在后续；真实第三方 API 待 V1-09 验收。

### Git Commits

| Hash | Message |
|------|---------|
| `b21c0b5` | feat(research): unify incremental changes with historical evidence |

### Status

[OK] **Completed**


## Session 18: V2-01 authorized Tool MCP and on-demand research supplement
<!-- trellis-session: v=2 fp=1412aa1c8c72fc1d -->

**Date**: 2026-10-22
**Task**: V2-01 authorized Tool MCP and on-demand research supplement
**Branch**: `main`

### Summary

Implemented deny-by-default Tool catalog, remote MCP Streamable HTTP client, bounded Repository-first supplement loop, append-only research_inputs ledger merged into report/evidence API, retry ledger preservation and empty-evidence success. Verified on test server 100.120.14.84: 229 passed across affected modules; fresh-DB alembic upgrade head + check clean. Real remote MCP provider/credentials remain unverified pending Owner input.

### Git Commits

| Hash | Message |
|------|---------|
| `0359a6f` | feat(tools): add authorized MCP retrieval with a research input ledger |

### Status

[OK] **Completed**


## Session 19: V2-02 JSON/XML/PDF 解析适配与 PDF 文章获取
<!-- trellis-session: v=2 fp=eb8613be56c2ad4f -->

**Date**: 2026-10-22
**Task**: V2-02 JSON/XML/PDF 解析适配与 PDF 文章获取
**Branch**: `main`

### Summary

按 V2 任务列表逐个实施：parse 阶段新增 JSON（含 +json）、XML（含 +xml）与 PDF 解析，分别用 JSON Pointer、元素路径、页码+页内字符范围定位；数值与单位按原文保留，损坏/重复键/超限/无文字层按稳定错误码失败而不产生空正文。RSS 文章获取接受 application/pdf，处理器版本升到 semantic-text-v2（旧文档下次解析生成新的不可变批次，历史批次与已引用片段保留）。config/sources.yaml 增加公开 Fed 新闻 RSS 与需 Owner 确认的 SEC EDGAR submissions 示例（disabled）。

### Main Changes

- 新增 stocks.processing.structured（JSON Pointer / XML 元素路径定位、重复键与非有限数拒绝、值/字符/深度上限）与 stocks.processing.pdf（逐页取文、页码定位、页数/字符上限、pypdf 依赖）
- parse_content() 按 MIME 族分发并升级处理器版本；collection/http.py 与 ARTICLE_ACCEPT 允许 application/pdf
- config/sources.yaml 增加 fed_press_releases（enabled）与 sec_edgar_submissions（disabled 样例）；docs/data.md、docs/quickstart.md、docs/validation.md、docs/architecture.md 同步契约与待验收边界

### Git Commits

| Hash | Message |
|------|---------|
| `bc2220c` | feat(parsing): locate JSON, XML and PDF originals as citable chunks |

### Testing

- [OK] 测试服务器隔离目录：test_parsing_formats 14 passed、test_processing+test_processing_retry 67 passed、test_collection 48 passed、worker_processing/facts/classification/api/report_api 98 passed
- [OK] 全新空库 alembic upgrade head + alembic check（无差异）
- [OK] 真实来源 drill 10/10 pass：公开 Fed RSS 真实采集 20 条入库且 20/20 原件哈希与库内一致；真实 HTML 42/42 片段可回查原文、重跑复用批次、显式重处理保留历史 Chunk；真实 Fed PDF 189,823 字节经文章获取通道解析为 8 页 15 Chunk、页内字符一一映射

### Status

[OK] **Completed**

### Next Steps

- 待 Owner 最终验收：Fed/SEC 来源换成真实联系邮箱、SEC EDGAR CIK 与访问政策确认及真实 API 映射；XML XPath 字段映射、OCR、PDF 表格结构重建未实现
- 继续按任务列表实施 V2-03 来源冲突与跨来源复核


## Session 20: V2-03 来源独立性与冲突证据呈现
<!-- trellis-session: v=2 fp=40aade6b6e69862d -->

**Date**: 2026-10-22
**Task**: V2-03 来源独立性与冲突证据呈现
**Branch**: `main`

### Summary

按 V2 任务列表逐个实施：只有来源自己声明的原件才合并。新增 stocks.origins 在采集时记录 canonical 链接 / 文章绝对地址 / feed 绝对 http(s) id（归一化后作为合并键），feed <source>/atom:source 只作转载出处，标题、摘要、向量相似与 opaque 标识都不合并；未声明的资料记为 single_document（未知，不等于已确认唯一）。Evidence Selection 输出 coverage.origin_groups[]/origin_group_count/merged_origin_group_count 与 metric_comparisons[]，同一 (subject, symbol, metric) 的多条 Fact 并列列出并分别标记 value_conflict/basis_mismatch/period_mismatch；render_report() 确定性生成 Sources and Conflicts 段落，报告详情接口与 Web 报告页展示独立原件数、同一原件（N 份）与冲突取值，均不平均、不覆盖、不标为已核实。

### Main Changes

- 新增 stocks.origins（canonical 链接解析、URL 归一化、document_origin/origin_key）与采集侧 Document.extra.origin；feed 声明来源作为 declared_sources 保留但不参与合并
- research/evidence.py 新增 EvidenceGroup.origin、group_origins()、metric_comparisons()；repository.document_origins() 一次读回本次选中 Document 的声明
- Evidence Package 覆盖新增 origin_group_count/merged_origin_group_count/origin_groups/metric_comparisons，selected_chunks 与 evidence_groups 带 origin_id（组内含 origin_group_size）；models/0001 增加 evidence_packages.metric_comparisons
- render_report() 生成确定性 Sources and Conflicts 段落；API 与 Web 报告页展示来源独立性、同指标取值对比与同一原件标记；config/tasks.yaml 提示词要求按独立原件计数
- docs/research.md、docs/data.md、docs/interface.md、docs/quickstart.md、docs/validation.md 同步契约与待验收边界

### Git Commits

| Hash | Message |
|------|---------|
| `8a8bcc3` | feat(research): group declared originals and list conflicting values |

### Testing

- [OK] 测试服务器隔离目录 test_evidence_conflicts 13 passed；test_collection+test_incremental_evidence 56 passed；test_report_api+test_research+test_execution 78 passed
- [OK] 全新空库 alembic upgrade head + alembic check 无差异（evidence_packages.metric_comparisons 与模型一致）
- [OK] 前端 npm run typecheck 通过、npm run test 6 文件 12 tests passed（含报告页来源独立性/冲突用例）
- [OK] 真实来源核对：Fed/SEC 公开 RSS 各 20 条真实条目得到 20 个互不相同的原件键（无误合并）；SEC 真实新闻页声明 canonical → canonical_url、Fed 页面无 canonical → article_url

### Status

[OK] **Completed**

### Next Steps

- 待 Owner 最终验收：真实多来源转载成对样例与真实同指标冲突值；事件关系库、来源真值评分与独立核查平台不在范围内
- 继续按任务列表实施 V2-04 历史报告对照与 V2-05 覆盖与费用验收


## Session 21: V2-04 基于原始证据的历史报告比较
<!-- trellis-session: v=2 fp=e7662ed8fd0957ef -->

**Date**: 2026-10-22
**Task**: V2-04 基于原始证据的历史报告比较
**Branch**: `main`

### Summary

按 V2 任务列表逐个实施：报告对照只读两份已保存报告各自的快照（Analysis 的 findings/open questions、Evidence Package 的 scope/as_of/coverage、引用 Document 版本与 Fact），不重新研究、不调用 Tool、不改写旧报告。结论按 (kind, 规范化 statement) 全等才算持续，改写按消失+新增呈现；Document 按来源声明的原件合并并标版本/内容变化，两侧原件都可打开；同指标 Fact 按 (subject_name, symbol, metric_id) 标 value/basis/period 变化并给出真实取值，不平均不覆盖；覆盖缺口增减、未知未扫描数量保持未知，晚于较早 as_of 的资料单列为真实来源变化。自比 400、未知 404、失败 Run 409，每组差异截断 50 条。无数据库结构变更。

### Main Changes

- 新增 research/comparison.py compare_report_sides()：结论/问题/Document/Fact/覆盖/晚到资料/警告分项比较，每组 _bounded(MAX_COMPARISON_ITEMS=50) 带 truncated
- api.py 新增 GET /api/reports/{report_id}/comparison?other_report_id=...：按 created_at 排序并给出 ordering.swapped，自比 400、未知报告 404、Run 非 succeeded 409；_report_side() 复用 _report_chain/_research_ledger/_report_research_refs/_resolve_evidence 读取两侧快照
- 前端新增报告对照卡片（默认预选同一 Task 最近的成功报告、两侧原件链接、指标修订与晚到资料展示）、api/schemas.ts 与 dashboardApi.reportComparison，并接入研究页报告阅读区
- 新增 backend/tests/test_report_comparison.py 与 frontend report-comparison.test.tsx；docs/research.md、docs/interface.md、docs/validation.md、docs/quickstart.md 同步对照契约与待验收边界

### Git Commits

| Hash | Message |
|------|---------|
| `511b677` | feat(research): compare saved reports against their own evidence |

### Testing

- [OK] [OK] 测试服务器隔离目录：test_report_comparison 4 passed；test_report_api+test_api+test_research+test_execution+test_evidence_conflicts 94 passed
- [OK] [OK] 该任务无结构变更：全新空库 alembic upgrade head + alembic check 无差异
- [OK] [OK] 前端 npm run typecheck 通过、npm run test 7 文件 14 tests passed（含报告对照默认预选、两侧原件链接、无对照对象时不渲染）

### Status

[OK] **Completed**

### Next Steps

- 待 Owner 最终验收：真实连续 Run 的两份报告人工对照，真实同一 Document 新版本被后续报告引用与同一指标跨报告修订的成对样例；逐条 Finding 关系图、严格历史知识回放不在范围内
- 继续按任务列表实施 V2-05 覆盖费用与异常分析及 V2 验收


## Session 22: V2-05 覆盖费用与异常分析及 V2 验收
<!-- trellis-session: v=2 fp=92416339e214a101 -->

**Date**: 2026-10-22
**Task**: V2-05 覆盖费用与异常分析及 V2 验收
**Branch**: `main`

### Summary

按 V2 任务列表逐个实施 V2-05：把每个模型调用的供应商与供应商自报费用落到 ModelCall，再在控制台按已存记录呈现研究覆盖与模型用量。未知的候选剩余与未上报费用保持未知，不猜测不折算；异常与积压继续复用既有重试、续扫、结束 Run 与处理积压能力，不新增调度或告警平台；无历史迁移，只改 0001 初始化定义。

### Main Changes

- ModelCall 新增 provider 与 cost_usd（numeric(14,6)，非负约束），同步 backend/alembic/versions/0001_initial.py
- providers 解析 usage.cost/usage.cost_usd：布尔、字符串、非有限、负数与超限一律按 MODEL_RESPONSE_INVALID 拒绝，未返回则费用保持未知；执行服务落库并在步骤 usage 输出 cost_usd 与 cost_unknown_attempts，CLI run JSON 同步
- api 新增 _usage_summary()（调用/成功/失败/回退计数、Token 求和、耗时、只汇总已上报费用并给出已知与未知调用数、models 截断标记）与 _latest_research_coverage()（取最近成功报告的证据包覆盖、缺口与补证输入，失败 Run 不计入）；/api/runs/{id} 与 /api/reports/{id} 增加 usage，/api/overview 增加 research
- 前端新增共享 usage-summary 组件，Overview 增加研究覆盖与近期模型用量卡片，Run 详情增加步骤用量、Provider 与费用列，报告页增加本次研究用量与 Run 入口
- docs/execution.md、docs/interface.md、docs/quickstart.md、docs/validation.md 同步用量与覆盖语义，并新增 backend/tests/test_coverage_usage_api.py 与 frontend usage-summary.test.tsx

### Git Commits

| Hash | Message |
|------|---------|
| `ba03897` | feat(console): show research coverage and provider-reported cost |

### Testing

- [OK] 测试服务器 100.120.14.84 隔离目录：全新空库 alembic upgrade head + alembic check 无差异
- [OK] test_coverage_usage_api + test_providers 12 passed；test_execution 63 passed（含新全流程供应商上报费用用例）；test_console_api + test_report_api + test_report_comparison 94 passed；test_evidence_conflicts + test_collection + test_research + test_api 71 passed
- [OK] 前端 npm run typecheck 通过、npm run test 8 文件 15 tests passed、npm run build 通过

### Status

[OK] **Completed**

### Next Steps

- 待 Owner 最终验收：真实第三方端点返回费用、真实远程 MCP 服务与凭据、真实多来源转载与冲突指标样例、两份真实连续报告用于对照卡、真机移动/ARM 部署验收；V2-01~V2-05 其余待验收边界见 docs/validation.md
- V2-01~V2-05 已全部交付并归档，V2 进入最终验收阶段


## Session 23: 修复解析器容器文本逐字符换行并整理待验收清单
<!-- trellis-session: v=2 fp=4bac9ee6f94ce43c -->

**Date**: 2026-10-22
**Task**: 修复解析器容器文本逐字符换行并整理待验收清单
**Branch**: `main`

### Summary

V2 任务列表已全部交付归档后，处理跨任务待验收登记中的 PA-2 解析器缺陷：正文直接位于 article/main/body 时，逐字符文本节点被当成逐字符证据片段，Chunk 文本变成逐字符换行。修复后容器自身的连续文本与行内元素合并为一个证据片段，并把处理器版本提升为 semantic-text-v3，使旧批次不被复用。同时把各阶段人工/第三方待验收项集中到 docs/validation.md 的「待最终验收」清单。

### Main Changes

- parser._walk_evidence() 统一走 _walk_block()，新增 CONTAINER_ELEMENTS（article/body/html/main）按结构元素递归，容器直接文本与行内元素合并为一个证据片段；PROCESSOR_VERSION 提升为 semantic-text-v3
- 新增回归用例 test_text_directly_inside_container_stays_one_evidence_run（body/main/article 三种容器）与 test_inline_text_directly_inside_container_keeps_order_and_ranges
- 契约与规范同步：docs/data.md 写明容器连续文本合并规则，.trellis/spec/backend/testing-guidelines.md 移除“正文必须放在内层块元素”的规避说明
- docs/validation.md 新增「待最终验收」清单（真实模型闭环、远程 MCP、SEC/Fed 联系方式、同源冲突样例、连续两份报告、真实费用端点、调度限流、移动/ARM、第三方闭环与发布），并在 .trellis/tasks/10-20-stocks-roadmap/pending-acceptance.md 记录 PA-2 修复与清单入口

### Git Commits

| Hash | Message |
|------|---------|
| `fbc6c4a` | docs(validation): consolidate pending human and third-party acceptance items |
| `6f4ffb7` | fix(parsing): merge container text instead of one segment per character |
| `79875f1` | docs(trellis): record the PA-2 parser fix and the pending acceptance list |
| `46e200e` | chore(parsing): bump the parse processor version to semantic-text-v3 |

### Testing

- [OK] 测试服务器 100.120.14.84 隔离目录：test_processing.py 38 passed；解析/采集/处理重试/仓库/冲突 117 passed；批次复用相关 5 文件 151 passed；后端全量 475 passed（1 个既有 Starlette 弃用告警）
- [OK] 真实来源复核：2 份真实 Fed 与 2 份真实 SEC 新闻正文，修复前 Fed 两页分别出现 18/17 个单字符片段（S\nk\ni\np...），修复后 0 个单字符片段、来源范围回查 0 处不一致

### Status

[OK] **Completed**

### Next Steps

- 待 Owner 最终验收：真实模型 Provider 凭据与真实外网闭环（PA-1）、真实远程 MCP 与凭据、真实返回费用的端点、SEC/Fed 真实联系邮箱、真实转载与冲突样例、连续两份真实报告人工对照、真机移动/ARM 与公网发布


## Session 24: F-01 保留 Fact 重处理历史并归档
<!-- trellis-session: v=2 fp=97e9e3d6f30005ad -->

**Date**: 2026-10-22
**Task**: F-01 保留 Fact 重处理历史并归档
**Branch**: `fix/fact-reprocess-history`

### Summary

移除 Fact 显式重处理的破坏性清空，补充历史报告与失败回滚回归，归档 F-01；运行验证因测试服务器认证不可用而未执行。

### Main Changes

- 移除文本抽取与 Source Mapper 重处理时的 clear_facts 及该函数，保留既有 (document_id, fact_key) 复用、事务和阶段所有权检查。
- 补充相同/变化/遗漏候选、新位置追加、提交前失败回滚与历史报告重处理回归；同步 docs/data.md 和数据库规范。
- 提交并推送 fix/fact-reprocess-history，归档 F-01；父任务 F-04 依赖保持未解除。

### Git Commits

| Hash | Message |
|------|---------|
| `8e6e459` | fix(facts): preserve historical facts across explicit reprocessing |
| `ff0807b` | docs(trellis): reconcile roadmap records and record the five repair tasks |

### Testing

- [OK] 未执行：SSH 免交互认证返回 Permission denied (publickey,password)，exit 255；未回退本机测试。
- [OK] 待远程补跑 tests/test_facts.py 与 test_report_comparison.py 的历史报告用例。

### Status

[OK] **Completed**

### Next Steps

- 开始 F-02：支持历史解析批次证据的 Run 重试。


## Session 25: 五项实现偏差修复交付
<!-- trellis-session: v=2 fp=55cea6dfb9144dcd -->

**Date**: 2026-10-22
**Task**: 五项实现偏差修复交付
**Branch**: `fix/processing-default-filter`

### Summary

依次实施并交付 F-01 至 F-05：Fact 历史身份保留、历史解析批次重试、补证 Fact 统一视图、按实际引用比较报告、积压面板默认筛选。全部提交、归档并推送到各自 fix 分支；运行验证因测试服务器认证不可用而未执行。

### Main Changes

- F-01：移除 Fact 显式重处理的破坏性清空，保留 ID/值/来源，补历史报告与失败回滚回归。
- F-02：冻结引用校验改为按已成功完成的不可变批次读取，新取材仍只选当前批次。
- F-03：合并初始与补证冻结 Fact 视图，Agent 输入、报告与 API 共用最终同指标比较。
- F-04：报告比较只取 Analysis/Findings 实际引用，Fact 值来自冻结投影，未引用材料不误计。
- F-05：积压面板初始改为 status=failed、stage 为空，补查询序列化断言。
- 五个分支顺序叠加，父任务仍 planning，I-1 至 I-4 未确认，未合并 main。

### Git Commits

| Hash | Message |
|------|---------|
| `8e6e459` | fix(facts): preserve historical facts across explicit reprocessing |
| `8341e01` | fix(research): read frozen retry evidence from its saved parse batch |
| `7ab3b14` | fix(research): include adopted supplement facts in research and conflicts |
| `3de76df` | fix(reports): compare only the evidence a saved report cited |
| `d65152c` | fix(web): open the processing panel on failed stages, not a failed stage |
| `f6fdf21` | docs(trellis): record the five delivered repairs and their unexecuted verification |

### Testing

- [OK] 未执行：SSH 免交互认证返回 Permission denied (publickey,password)，exit 255；未回退本机测试、类型检查或构建。
- [OK] 待远程补跑各叶子 acceptance.md 列出的最小测试与 API/前端检查。

### Status

[OK] **Completed**

### Next Steps

- 取得测试服务器认证后补跑五个叶子的最小验证，并在集成分支验证 I-2/I-3 联合场景。


## Session 26: V2 集成验收与证据范围校正
<!-- trellis-session: v=2 fp=58c0fc5d5962ef2e -->

**Date**: 2026-10-23
**Task**: V2 集成验收与证据范围校正
**Branch**: `main`

### Summary

在指定测试服务器隔离库完成 V2 相关 133+40=173 项测试及新库初始化无漂移；真实两报告双向对照与九表全行哈希检查通过；SEC Apple JSON 采集、四字段 JSON Pointer 映射及原件回查通过，全文 parse 超过 5000 值限制；Fed XML 真实解析通过、本轮 PDF 超时。校正 V0/V1 窄范围证据表述，统一记录设备、MCP、来源修订、费用、外部 S3 与长期运行剩余条件。V1/V2 保持 planning，保留真实运行现场，无产品代码变更。

### Git Commits

| Hash | Message |
|------|---------|
| `5a94136` | docs: record V2 integration acceptance and precise evidence limits |

### Status

[OK] **Completed**
