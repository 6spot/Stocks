# Research: V2-01 authorized Tools/MCP — section-level document updates

- Query: Which exact section-level updates do `docs/execution.md`, `docs/data.md`, `docs/research.md`, `docs/validation.md` and `docs/quickstart.md` need for authorized Tools/MCP, Repository-first supplemental research, the `research_inputs` evidence chain, gap/failure semantics and time labels — and where do those updates conflict with V2-02/V2-03/V2-04?
- Scope: internal (docs + current code/tests); no external MCP specification was consulted
- Date: 2026-10-22

## Findings

### 1. Verified starting state (what the docs may claim today)

The working tree changed while this research ran: an unreleased partial Tools package, a
Tool config file and Tool unit tests now exist, but nothing in the Agent/Task runner,
config loading, API or docs refers to them.

| Artifact | Verified state | Reference |
| --- | --- | --- |
| Tool package | `config.py`, `errors.py`, `client.py`, `__init__.py` exist: deny-by-default config, authorization intersection helper, JSON-schema validation, MCP Streamable HTTP client with budgets, size/depth limits, DNS/redirect safety, SSE parsing, result mapping, stdout-free error codes | `backend/src/stocks/tools/config.py:23`, `:492-715`, `backend/src/stocks/tools/client.py:897-990`, `backend/src/stocks/tools/errors.py` |
| Tool config file | Exists, deny-by-default, comment says tools are opt-in | `config/tools.yaml` (`tools: []`) |
| Agent grants | The only Agent still declares `tools: []`; `AgentConfig.tools` rejects any non-empty list (`max_length=0`) | `config/tasks.yaml:118`, `backend/src/stocks/execution/config.py:483` |
| Tool tests | `backend/tests/test_tools.py` exists (17 tests: authorization, wire sequence, budgets, SSE, redirect/DNS, credential env, size/depth, timeout, session restart, protocol mismatch, log redaction), all with `httpx.MockTransport` | `backend/tests/test_tools.py:231-676` |
| Run integration | Missing. `stocks.tools` is imported only by its own package and `test_tools.py`; no Tool loop in `_run_agent`, no Tool config loaded from `config/` at runtime | grep of `backend/src` shows no `stocks.tools` import outside the package |
| `research_inputs` | Appears only in `docs/execution.md:133`, `:145`, `docs/research.md:118`, `:148` and the task PRD; no model field, table or API reads it | grep across `backend/src`, `backend/tests` |
| Supplementary tests | `backend/tests/test_research_supplement.py` does not exist yet (planned in `implement.md`) | `backend/tests/` listing |
| Evidence Package | One row per Run with mutable JSONB `selected_chunks`, `evidence_groups`, `coverage`, `change_scan`; no append-only input layer | `backend/src/stocks/models.py:242-300` |
| Analysis | Copies package coverage; no independent `as_of`, window or final-input snapshot | `backend/src/stocks/models.py:303-328` |
| Citation validation | Requires persistent current Chunk + Document + parse batch + Document version | `backend/src/stocks/research/service.py:735-795` |
| Agent input | Reads only `package.selected_chunks`; empty readable evidence fails with `EVIDENCE_UNAVAILABLE` even when a Fact-only group exists | `backend/src/stocks/execution/service.py:1887-1948` |
| Gap vocabulary | Package-recorded gaps plus fixed read-time observed gaps, merged and de-duplicated | `backend/src/stocks/research/service.py:698-732`, `backend/src/stocks/api.py:1931-1960` |
| Commit rule | Analysis and Report are built and flushed inside the transaction that commits the succeeded StepResult, so a Run never records a succeeded research step without its report | `backend/src/stocks/execution/service.py:2184-2259` |

Consequence for the doc work: the contract edits below must land **with** the Run
integration, not before it. Until then `docs/execution.md:28` and
`docs/quickstart.md:386-387` correctly describe current behavior, and the stale-parts
list is exactly those two spots (not "documentation says no implementation exists
anywhere" — the Tool package itself has no docs at all today).

### 2. `docs/execution.md`

Owning doc for Task/Run, budgets, time fields and Tool/MCP boundary.

**(a) Line 28 — replace the tools clause.** Today the V1-subset paragraph ends with the
clause that a research Agent's `tools` list must be empty and no external Tool/MCP call is
made. Split it: keep the V1 statement that the research Task shape is exactly one
`select_evidence` + one final `research_agent`, and add the V2-01 rule, for example:

> V2-01 起研究 Agent 可声明 `tools`；列表中的每个 ID 必须是 `config/tools.yaml` 中已启用
> 工具的显式子集，且同时被 Task/Agent 白名单授权。未配置、已停用、越权或未通过参数校验的
> 请求在发出网络请求前拒绝；`tools` 为空时行为与 V1 相同。

Do not weaken the same paragraph's atomic-commit and checkpoint sentences.

**(b) `## Tools 与远程 MCP` (lines 281-291) — expand in place**, keeping the five existing
bullets and adding:

1. **Effective permission** = configured & enabled tool ∩ Task allowlist ∩ Agent allowlist ∩
   schema-valid request. State that the model cannot change scope, `as_of`, incremental
   window, budgets, credentials, endpoint or grants, and that no native function calling is
   required: the model emits a provider-neutral structured request (`tool`, `arguments`,
   `purpose`, gap/question addressed).
2. **Execution boundary** — Stocks validates then executes; Repository read/search first,
   external call only for missing/stale/conflicting/cross-check needs. Remote Streamable HTTP
   only; stdio stays out of scope.
3. **Budgets and termination** — per-tool and per-Run call/token/time budgets, stop on
   exhaustion, duplicate query, or no progress; the loop stays inside the existing final
   Research Agent step (no new orchestration, no implicit sub-agents).
4. **Denial and failure** — unauthorized/unknown/disabled/invalid requests are denied before
   network access; transient failures get bounded retries; exhaustion becomes an explicit
   gap and may still yield a valid report from existing or supplemented evidence.
5. **Credentials and output hygiene** — credentials from environment/security config only;
   never in Run snapshots (extend line 57's "Task 和 Run 不保存 endpoint 明文、API key 或
   Authorization"), ModelCall rows, logs, StepResult, tool results or report output. Raw tool
   responses are data and are not pasted into reports; adopted content goes through the
   Document path, and only Tool-call metadata (id, request fingerprint, usage, error, input
   references) is persisted.

**(c) `#### 研究增量窗口与共享资料引用` (lines 119-137) — extend the time table.** Keep the
existing rows; add the canonical meanings from §6 below and one explicit paragraph:
initial `EvidencePackage` is the immutable Repository-selection snapshot; the final
`research_inputs` is the appended list of everything actually used (initial package ref +
supplemental saved Document versions, Chunk/Fact refs, tool-call identities, roles, reasons,
time relations). Add: adopted evidence published or updated after `as_of` must be flagged in
the report, `as_of` is a research reference time rather than a knowledge cutoff, and
`window_*` / `change_watermark_*` do not forbid authorized supplemental acquisition.

**(d) Line 145 rule 5 — make `research_inputs` precise.** Replace "最终采用的输入 ID、
版本、选择原因或查询条件" with the exact field list: Document content version, Chunk/Fact
reference, tool-call identity, selection role/reason, query conditions, and the time
relations (published / updated / collected / available / period). State that it is
append-only, that retries keep the original package and append only genuinely new inputs,
and that earlier Analysis/Report rows are never rewritten.

**(e) Lines 51 and 53 — citation contract decision.** Both currently require each finding to
cite at least one selected Chunk. V2-01 must either (i) keep the Chunk requirement and state
that Fact-only supplemental evidence cannot support a persisted finding, or (ii) extend the
contract: a finding may cite a persisted Fact whose `evidence_ref` resolves to its source
Document version. The current code implements (i) (`research/service.py:798-916`,
`execution/service.py:1944-1948`), so option (ii) is a code change, not a doc-only edit.
Whichever is chosen must be stated identically in `docs/research.md` and `docs/quickstart.md`.

### 3. `docs/research.md`

Owning doc for selection, research agent behavior and Analysis/Report.

**(a) `### Agent 补充检索与故障边界` (lines 114-123)** — the bullets already cover
Repository-first, adopt-then-cite and append-only inputs. Add, without duplicating field
specs from `execution.md`:

- the **gate**: stored incremental and historical Repository evidence is queried first;
  external Tools are called only for missing, stale, conflicting or cross-check needs;
- the **lifecycle** of an adopted response: save exact bytes/response through the unified
  Source/Repository entry (`ingest_document()`), process only the needed source to obtain a
  Chunk or Fact (do not wait for the global backlog), then append to `research_inputs`;
- **discovery vs evidence**: search results and snippets are discovery only and can never be
  cited; they may remain open questions, coverage limitations or model inference;
- **immutability**: the initial package is not rewritten and earlier Analysis/Report rows
  stay intact.

**(b) Input table (lines 131-138) and Research Agent behavior (lines 140-150)** — add a
`research_inputs` row (final, appended, superset of the package), restate that
`allowed_tools[]`/`budget` come from trusted config and that the model cannot expand them,
and extend lines 148-150 with the termination/denial rules from §2(b). Line 166 already
states the Chunk-only rule for the current implementation; update it only together with the
§2(e) decision.

**(c) Evidence Package data contract (lines 57-112)** — keep the illustrative YAML but
label `research_inputs` there as the final appended view when V2-01 lands, and state that the
package itself is a selection snapshot rather than the complete input set. Line 112's
sentence about Fact-only groups (`ref: null`, `fact_refs`) should reference the §2(e)
decision so the two docs cannot drift.

**(d) New gap/failure matrix** — add a subsection next to the failure bullet (line 121) with
the categories in §7. Keep it design-level: name codes only once the implementation fixes
them.

**(e) Out of scope here** — do not add cross-document reprint detection, source-independence
scoring, conflict interpretation or report-comparison behavior; see §8.

### 4. `docs/data.md`

Owning doc for sources, unified ingest and Fact/time semantics. Note the file is 334 lines
and the implement plan warns about injection truncation; edits must be made against the full
file.

**(a) Lines 13-14, 18-30, 32-57 (discovery vs evidence, unified input)** — the flow already
mentions "授权 Tool" (line 20, 36, 41). Add the missing operational rules: on-demand Source
registration for a discovered page, the single ingest entry point (`ingest_document()` at
`backend/src/stocks/repository.py:151-243`, immutable versions, idempotency by
`(source_id, dedupe_key, content_hash)`), processing limited to the source needed for the
current research step, and the security limits (type/size, HTTPS and address checks,
rate limits, log redaction) that the Tool client already enforces.

**(b) `### 来源与发布者` (lines 59-73) — reconcile with the model.** The bullet list says
`publisher / original_publisher` and `storage_ref`. Current code has publisher on `Source`
only (`backend/src/stocks/models.py:43-50`) and `trust_tier_snapshot`, `snapshot_key`,
`original_url`, `content_hash` on `Document` (`backend/src/stocks/models.py:444-488`);
`DocumentInput` has no publisher field (`backend/src/stocks/repository.py:41-55`). Either
mark the list as the logical contract with the current column mapping, or trim it; do not
leave the doc implying a `Document.publisher` column exists. Add the search case explicitly:
for content found through a search/MCP channel, the publisher is the original site, and the
search service never raises trust tier.

**(c) Lines 114 and 123-129** — keep V0/V1 wording, but state that V2-01 supplemental
acquisition reuses this same entry point and that a failure to process an adopted response
is recorded as a gap against the Run, never as "the material does not exist".

**(d) `#### 数值与时间语义` (lines 275-279)** — reuse the §6 time table instead of
restating one-off meanings; add that a Fact created from a supplemental source keeps
`period_start/end`, `available_at`, unit and its source `document_id`, and that `available_at`
is the public-knowability time rather than the ingest time.

**(e) `## 来源可信度、证据与安全` (lines 317-334)** — lines 329-332 already cover untrusted
tool returns, whitelist/parameter boundaries enforced by Stocks, read-only defaults and
redaction. Add only what is missing: credentials exclusively via environment/security
configuration; adopted tool bytes must be persisted before citation; tool results are never
the evidence object itself.

### 5. `docs/validation.md`

**(a) Line 34 (`- **授权补证**`)** — replace the single bullet with three verifiable cases
mirroring the PRD ACs:

1. sufficient stored evidence produces no external call; missing/stale/conflicting evidence
   permits only authorized calls; unauthorized or injected requests cannot expand permission;
2. an adopted supplemental response becomes a persisted Document/Fact/Chunk traceable to the
   original bytes; an unsaved search snippet cannot masquerade as full-text evidence;
3. tool failure, processing timeout or no progress leaves explicit gaps, terminates within
   bounds, never reports silent success, and a retry keeps the initial package plus appended
   inputs.

**(b) Line 28 style boundary note** — once implemented, add a "已实现与待补的边界" sentence
for V2-01 in the same style as the V1-09 paragraph, including the test-server-only rule and
that a real Streamable HTTP service plus original-bytes chain must be validated against an
actual configured endpoint (no credentials ⇒ mark as unverified, never fall back locally).

**(c) Lines 38-43** stay as-is; line 40 already provides the "tool returns are data" rule.

### 6. Canonical time labels (single table to be owned by `docs/execution.md`)

| Label | Meaning | Current anchor |
| --- | --- | --- |
| `published_at` | first publication at the source; nullable | `docs/execution.md:127`, `docs/data.md:67`, `models.py:472` |
| `source_updated_at` | source-declared update; nullable; does not by itself create a new content version | `docs/data.md:67`, `docs/data.md:125`, `models.py:473` |
| `available_at` | when a Fact/value became publicly knowable; not the ingest time | `docs/data.md:267`, `models.py:765-848`, `:841` |
| `collected_at` | when Stocks actually acquired the bytes/response | `docs/execution.md:127`, `models.py:474` |
| `period_start/end` | observation/measurement period of the value | `docs/data.md:267` |
| `as_of` | Run research reference time, default Run start; not a strict knowledge cutoff | `docs/execution.md:133` row, `:135-137` |
| `window_start/end` | display interval tied to the change watermark; not a hard cutoff | `docs/execution.md:126-137` |
| `change_watermark_start/end` | committed-change scan boundary, `(start,end]` | `docs/execution.md:128`, `:143` |
| `generated_at` | Analysis/Report generation time; currently `Analysis.created_at` / `Report.created_at` — decide whether the doc keeps the name `generated_at` or names the columns | `models.py:303-328` |

Rules to state with it: no field substitutes for another; adopted evidence dated after
`as_of` is allowed but must be flagged; reports keep the real source times of the snapshot
they used, and later updates never rewrite an old report.

### 7. Gap and failure semantics

Two layers exist today and the docs should keep them distinct:

- **Recorded at selection time** (`backend/src/stocks/research/service.py:698-732`):
  `no_queries`, `no_selected_evidence`, `no_direct_matches`, `no_background_matches`,
  `no_<role>_selected`, `coverage_unavailable`, `retrieval_truncated`, `token_budget`,
  `invalid_query_limit`, `embedding_route_unavailable`, `embedding_failures`,
  `unindexed_chunks`, `partial_source_content`.
- **Observed at read time** (`backend/src/stocks/api.py:1931-1960`): `document_not_stored`,
  `chunk_unavailable`, `partial_source_content`, `snapshot_storage_unconfigured`,
  `chunk_not_in_current_parse_batch`, `chunk_not_in_current_index`, `run_not_succeeded`.

Recommended categories for the doc matrix, mapped to distinct wording rather than one bucket:
exhausted search (no match within the recorded queries/index), source/tool unavailable,
bounded/truncated scan (remaining candidates unknown — never zero-filled), screened but not
selected/budget-filtered, processing/indexing failure, partial source content, tool denied /
unknown / disabled / invalid request, transient tool failure after bounded retry, no progress
or duplicate query, and no report because the final Agent or Analysis failed.

Two invariants to write explicitly: missing material is never reported as "the fact does not
exist", and Tool-specific codes get names only after the implementation contract is fixed
(the safe error vocabulary already present in `backend/src/stocks/tools/errors.py` and the
`TOOL_*`/`MCP_*` codes in `config.py`/`client.py` is a good starting point but is not yet a
documented, stable gap vocabulary).

### 8. Cross-task conflicts

| Task | Boundary to preserve | Evidence |
| --- | --- | --- |
| V2-02 (source/parser expansion) | New source types, PDF/XML/JSON parsing, OCR, parser formats and parser retries belong to V2-02. V2-01 must stay format-generic and report unsupported/processing failures as gaps. | `.trellis/tasks/10-20-v2-02-source-parser-expansion/prd.md` (范围与要求, 不在范围内) |
| V2-03 (source conflicts) | Cross-document reprint detection, source independence, conflict interpretation and conflict UI belong to V2-03. V2-01 only preserves publisher/refs/units/evidence values and must not claim origin resolution or add fuzzy reprint merging. V2-03 lists V1-09 as its dependency, not V2-01. | `.../10-20-v2-03-source-conflicts/prd.md` |
| V2-04 (report comparison) | Historical report comparison API/UI belongs to V2-04 and depends on V2-03. V2-01 may keep prior Analysis as background but must not add comparison behavior or `previous_report_ref` UI. | `.../10-20-v2-04-report-comparison/prd.md` |
| V2-02 depends on V2-01 | V2-02's PRD declares V2-01 as its prerequisite; V2-01's acceptance therefore gates later work. Keep the dependency wording intact when editing docs. | `.../10-20-v2-02-source-parser-expansion/prd.md` 前置依赖 |
| `docs/quickstart.md` | Not in the PRD's 归属契约 list but explicitly required by `implement.md` ("运行能力更新 quickstart"). Update it as part of V2-01 rather than treating it as incidental. | `.trellis/tasks/10-20-v2-01-authorized-tools-mcp/implement.md` 收尾 |
| `docs/architecture.md` | Line 132 lists "MCP | Python MCP SDK，远程 Streamable HTTP", while `backend/src/stocks/tools/client.py` is a hand-written JSON-RPC/SSE client with no SDK import. This is a tech-choice drift, not a V2-01 doc deliverable; raise it with the Owner instead of silently editing architecture.md. | `docs/architecture.md:132`, `backend/src/stocks/tools/client.py:261-990` |

### 9. `docs/quickstart.md` (exact sections)

**(a) Lines 358-387 (Tasks, scheduling and the Worker)** — update only after the Run
integration lands. Line 386-387 currently reads "It does not collect, parse, index, or call
external Tools; the Agent input is untrusted JSON data and the configured `tools` list must
be empty." Replace with the implemented behavior: a research Agent may name only enabled
tools from `config/tools.yaml`; an unconfigured, disabled or unauthorized id rejects the
Task configuration, and an empty list keeps V1 behavior.

**(b) New Tool/MCP configuration section** — describe only fields that exist in
`ToolConfig`/`ToolBudgets`/`ResultMappingConfig` (`backend/src/stocks/tools/config.py:482-660`):
`id`, `enabled` (default false), public HTTPS `endpoint` without query/fragment,
optional `remote_name`, optional `bearer_token_env` (environment-variable name only),
`authorization_prefix`, `timeout_seconds`, `max_json_depth`, `max_sse_events`,
`input_schema` (object, `additionalProperties: false`), optional `output_schema`, `budgets`
(`max_calls`, `max_tokens`, `max_request_bytes`, `max_result_bytes`), and `result_mapping`
(`output_path` or `fields`). `config/tools.yaml` exists and ships deny-by-default with
`tools: []`.

**(c) Environment and credential guidance** — list placeholder variable names only (for
example `STOCKS_<TOOL>_TOKEN` referenced by `bearer_token_env`); never real values; note that
missing credentials fail before any request (`TOOL_CREDENTIAL_MISSING`) and that endpoints,
headers and bodies are excluded from logs and Run snapshots.

**(d) Inspection commands** — add only for capabilities that exist at the time of writing.
Today `config/tools.yaml` can be inspected as a config file, but there is no CLI/API to list
authorized tools or Tool calls, so do not document one. Reserve mentioning
`tests/test_tools.py` as an existing unit test file, and label
`tests/test_research_supplement.py` as planned/unverified until it exists and runs.

**(e) Evidence-chain and gap documentation** — extend the existing report section
(lines 560-574) with `research_inputs`, the recorded-versus-observed gap split, the time
labels, and how to open a supplemental item's original bytes. Keep the test-server rule at
lines 348-356 unchanged, including the "report unverified rather than fall back locally"
sentence.

### 10. Files found

| Path | Relevance |
| --- | --- |
| `docs/execution.md` | Task/Run contract; line 28 stale tools clause; line 281 Tools section; lines 119-137 time/window table; line 53 citation rule |
| `docs/research.md` | Lines 57-112 package contract; 114-123 supplemental bounds; 125-150 agent input/behavior; 166 current Chunk-only rule |
| `docs/data.md` | Lines 13-57 discovery vs evidence; 59-73 publisher list vs model; 114/123-129 ingest; 252-279 Fact/time; 317-334 security |
| `docs/validation.md` | Line 34 V2 authorization bullet; 28 V1-09 implemented/planned style; 38-43 shared rules |
| `docs/quickstart.md` | Lines 348-356 test-server rule; 358-387 Task/Worker behavior; 560-574 report gaps; 85/184/248 config examples |
| `config/tools.yaml` | Deny-by-default Tool config; comment states opt-in |
| `config/tasks.yaml` | Line 118 `tools: []` for the only Agent |
| `backend/src/stocks/tools/config.py` | `MCP_PROTOCOL_VERSION = "2025-11-25"` (:23); budgets (:492-509); result mapping (:511-553); `ToolConfig` (:556-682); `ToolConfiguration` (:684-713); `authorize_tools` (:715-760); `load_tool_configuration` (:762) |
| `backend/src/stocks/tools/client.py` | Streamable HTTP client, DNS pinning, SSE parsing, session restart, budgets |
| `backend/src/stocks/tools/errors.py` | Stable safe error hierarchy |
| `backend/tests/test_tools.py` | 17 MockTransport tests; nearest reusable pattern for the future Run integration tests |
| `backend/src/stocks/execution/service.py` | `_run_agent` input/validation (:1887-2106); `_analysis_and_report` inside the committing transaction (:2184-2259) |
| `backend/src/stocks/research/service.py` | Recorded gaps (:698-732); citation validation (:735-795); output validation (:798-916) |
| `backend/src/stocks/api.py` | Observed gaps and merged `coverage_gaps` (:1931-1960) |
| `backend/src/stocks/models.py` | `Source` (:43-50); `EvidencePackage` (:242-300); `Analysis` (:303-328); `Document` (:444-488); `Fact` (:765-848) |
| `backend/src/stocks/repository.py` | `DocumentInput` (:41-55); `ingest_document()` (:151-243) |

### 11. Related specs

- `.trellis/spec/backend/testing-guidelines.md` — tests, compilation, DB and Compose
  verification run only on `100.120.14.84` in the task-isolated directory; no local fallback.
- `AGENTS.md` — doc ownership rule (one contract per owning doc), minimal implementation, no
  migration/compatibility work in this phase.
- `.trellis/spec/guides/agent-execution-guide.md` — bounded diagnosis and blocking handoff
  rules referenced by AGENTS.md rule 10.

## Caveats / Not Found

- No tests were run, no files outside this `research/` directory were written, and no git
  operations were performed. Everything above is static inspection of the working tree at
  2026-10-22.
- No external MCP specification was consulted. `MCP_PROTOCOL_VERSION = "2025-11-25"`
  (`backend/src/stocks/tools/config.py:23`) and the Streamable HTTP details in `client.py`
  are internal observations only and were not validated against an upstream revision.
- A previous note in this session cited "docs/execution.md lines 385-387" for the stale tools
  text; that file has 291 lines. The stale statements are `docs/execution.md:28` and
  `docs/quickstart.md:386-387`.
- The Tool package, `config/tools.yaml` and `backend/tests/test_tools.py` appeared during
  this work and are not referenced by runtime code, docs, or Task config. Treat their
  commit state, review state and test results as unknown; do not describe Tools/MCP as
  implemented end-to-end.
- No CLI or API surface exists for listing authorized tools, inspecting tool calls, or
  reading `research_inputs`; any quickstart command beyond `config/tools.yaml` inspection
  would be speculative.
- `docs/data.md` is 334 lines; the implement plan warns that automatic injection may truncate
  it, so field-level edits must be made against the full file.
- `docs/architecture.md:132` claims the project uses the Python MCP SDK while the current
  client is hand-written. Flagged for the Owner; not resolved here.
