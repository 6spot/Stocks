# Research: EvidencePackage snapshot hash, append-only research_inputs, and AC-3 no-evidence semantics

- Query: Design the minimal model/API/report compatibility changes for an
  initial EvidencePackage snapshot hash plus append-only `research_inputs`;
  determine how existing tests construct EvidencePackage / run snapshots; and
  decide whether "no initial evidence + failed Tool supplementation" ends as a
  succeeded report with gaps or as a failed Run, based on PRD AC-3 and current
  schema/step semantics.
- Scope: internal (repository code, tests, specs, owning docs). External MCP
  protocol details are out of scope and covered by
  `authorized-tools-mcp-doc-recommendations.md`; the pipeline/session mechanics
  are covered by `supplemental-research-pipeline.md`; the test matrix by
  `v2-01-test-strategy.md`.
- Date: 2026-10-22

## Findings

### 0. Answers

1. **Where the data belongs.** Implemented shape: the append ledger is
   `TaskRun.research_inputs` (plus a frozen copy on `Analysis.research_inputs`),
   and `EvidencePackage` gets **no** new columns. The "initial snapshot hash" is
   computed on demand from the package (`evidence_package_hash`) and pinned as
   the first ledger entry (`kind="selection_snapshot"`). This differs from the
   task's `design.md`, which still says the three fields live on
   `EvidencePackage`; the implemented shape matches the owning docs
   (`docs/execution.md:133` "`research_inputs` | Task Run / Analysis").
2. **AC-3 recommendation.** No evidence + failed/timed-out/exhausted Tool
   supplementation must end as a **succeeded Run with a Report containing
   `findings=[]` and explicit gaps**, not as a failed Run. The code gate
   (`EVIDENCE_UNAVAILABLE` in `_run_agent`) has already been removed during this
   session; the remaining gate is `config/tasks.yaml` `evidence.minItems: 1`.
3. **Test impact.** Because `EvidencePackage` is unchanged, the five existing
   `EvidencePackage(...)` constructors **do not** need a hash argument (an
   earlier draft of this note assumed otherwise). What needs new coverage is the
   ledger merge, the retry ledger copy, the Analysis frozen copy, and the AC-3
   flip itself.

### 1. Verified state at reading time (implementation is in flight)

`git status` shows V2-01 partially applied and uncommitted; files were edited by
another process while this research ran, so every claim below was re-verified at
the end of the session. Because `backend/src/stocks/execution/service.py` grew
from ~2 280 to ~2 660 lines during the session, references to that file are
given by function name with an indicative line range.

| Artifact | Verified state |
| --- | --- |
| `backend/src/stocks/models.py` | `TaskRun.research_inputs` (`:242`, check `ck_task_runs_research_inputs_array` `:183-186`); `Analysis.research_inputs` (`:346`, check `:326-330`); `EvidencePackage` **unchanged**, no `initial_snapshot_hash` anywhere |
| `backend/alembic/versions/0001_initial.py` | matching `research_inputs` columns + named array checks for `task_runs` and `analyses`; no `evidence_packages` change |
| `backend/src/stocks/research/service.py` | `INITIAL_SNAPSHOT_FIELDS` (`:57-72`, `run_id` excluded), ledger kinds (`:74-76`), `evidence_package_snapshot` (`:693`), `evidence_package_hash` (`:707`), `merged_research_refs(package, ledger)` (`:718-796`), `research_ledger_gaps` (`:799`), `selection_snapshot_entry` (`:818`) |
| `backend/src/stocks/research/supplement.py` | new: plan schema/bounds, Tool execution, ingest+stages, `append_research_entries()` (`:335`) with `SELECT ... FOR UPDATE` on the TaskRun, hash pin + `EVIDENCE_PACKAGE_REWRITTEN`, gap codes (`:82-104`) |
| `backend/src/stocks/execution/service.py` | partially wired: `ExecutionContext` seams (`session_factory`, `tool_client_factory`, `stage_registry`, `snapshot_store`, `tools_config_path`), `create_or_get_run(tools_config_path=...)`, `_agent_evidence()` reading `merged_research_refs(package, run.research_inputs)` (`:1960-2005`), `_supplement_rounds()` (`:2065`) called from `_run_agent`, and the old `EVIDENCE_UNAVAILABLE` empty-evidence raise **removed**. Still missing: `Analysis.research_inputs` population, supplemented coverage merge, and the `retry_run` ledger copy |
| `backend/src/stocks/api.py` | unchanged — report detail still resolves `_selected_chunks(package)` (`:1596-1639`) |
| `config/tasks.yaml` | unchanged — `tools: []` (`:118`), `evidence.minItems: 1` (`:141`), `unavailable_evidence_failure` example (`:55-72`) |
| `backend/tests/` | no test references `research_inputs` yet |

### 2. Field structure

#### 2.1 `TaskRun.research_inputs` — the append-only ledger

JSONB array, `NOT NULL DEFAULT '[]'::jsonb`, with `jsonb_typeof(...) = 'array'`
enforced at the table level. Three record kinds, discriminated by `kind`:

```jsonc
// 1. boundary entry, written once by the first successful append
{"kind": "selection_snapshot",
 "evidence_package_id": "<uuid str>",
 "evidence_package_hash": "<64 hex>",
 "recorded_at": "<iso8601>"}

// 2. adopted input (citable), keys required by merged_research_refs()
{"kind": "input",
 "input_id": "<stable string>",         // required
 "tool_id": "<authorized tool id>|null",
 "document_id": "<uuid str>",           // required
 "document_version": 3,                 // required int, bool rejected
 "parse_batch_id": "<uuid str>",        // required
 "source_id": "<source id>",            // required
 "title": "...",
 "chunks": [                            // required, non-empty
   {"chunk_id": "<uuid str>", "position": 0,
    "chunk_ids": ["..."], "fact_ids": ["..."]}],
 "timing": [{"label": "published_after_as_of", "value": "<iso8601>"}],
 "purpose": "...", "query": {}}

// 3. recorded gap (not citable)
{"kind": "gap", "code": "tool_timeout", "tool_id": "<id>|null",
 "purpose": "...", "message": "<bounded safe text>", "recorded_at": "<iso8601>"}
```

`evidence_package_hash()` and the `selection_snapshot` entry are what make an
append safe: the first append pins the hash of the frozen selection fields, and
every later append recomputes and compares before extending (`supplement.py:361-387`).

#### 2.2 `Analysis.research_inputs` — frozen copy for the published report

Written when the Analysis is saved, so a later Run, retry or Document change
cannot alter what a published report records as its actual inputs
(`models.py:340-347` comment). Report/API reads should prefer this copy over the
live Run ledger.

#### 2.3 `EvidencePackage` stays exactly as it is

No new columns, no hash column, no list columns. The package is therefore
immutable by construction: nothing in the new design ever writes to it after
`select_evidence()`. The rewrite detector becomes "does the package still hash
to the value the ledger pinned?", which is only evaluated when an append
happens.

Trade-offs of this shape, versus the package-column shape recorded in
`design.md:39-40`:

| Concern | Run ledger (implemented) | Package columns (design.md) |
| --- | --- | --- |
| Matches `docs/execution.md:133` "Task Run / Analysis" | yes | no (package is neither) |
| Retry | must copy the ledger to the new Run (still missing) | deep copy of the package carries it |
| Hash visibility in the package payload | needs computing or reporting from the ledger | a stored column |
| DB-level shape check | `jsonb_typeof = 'array'` on both tables | same check possible on one table |
| Immutability story | package never written after selection; ledger is append-only by code + row lock | same, plus a self-describing hash column |

Both satisfy PRD AC-3 ("重试保留初始包及追加输入"). The implemented shape is the
better fit for the owning docs, but it moves one obligation: `retry_run` must
copy `research_inputs` (and the pinned hash) to the new Run, or a retry loses
the supplemented inputs and the hash pin.

#### 2.4 Rejected alternative: child table

A separate `research_inputs` table keyed by `run_id` with `sequence`, unique
`input_key` and identity FKs would give DB-level uniqueness and cross-Run
queryability. It is unnecessary now that the owner docs put the ledger on
Run/Analysis: Run ↔ ledger is 1:1, retry copies one row's JSONB, and there is no
cross-Run query requirement. The cost of JSONB: uniqueness, "exactly one
identity", ordering and append-only behaviour are enforced in code
(`append_research_entries` + `merged_research_refs`), not by constraints.

### 3. Validation rules

Hash inputs (`INITIAL_SNAPSHOT_FIELDS`, `research/service.py:57-72`):
`objective`, `scope`, `as_of`, `selection_policy_version`, `selection_budget`,
`queries`, `selected_chunks`, `evidence_groups`, `coverage`,
`candidates_discovered`, `candidates_screened`, `selected_count`,
`unexamined_count`, `retrieval_truncated`, `change_scan`. Excluded by design:
`id`, `run_id`, `created_at` (the docstring at `:693-701` explains that a retry
copies the same selection content into a new Run and must hash identically).

Canonical form (`:676-716`): normalize `datetime`/`UUID`/mapping keys, then
`json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False,
allow_nan=False)` → SHA-256 hex — the same pattern as `snapshot_hash()`
(`execution/config.py:852-856`) and `tools/config.py:809-817` (which also set
`allow_nan=False`).

Rules for an append:

1. Ownership fence first: the TaskRun must still be `running` and owned by this
   `process_token`, checked while holding the row lock
   (`supplement.py:348-361`); otherwise `RUN_NOT_OWNED`.
2. Pin or verify the snapshot: no `selection_snapshot` entry → insert one with
   the current hash; entry present and hash differs →
   `EVIDENCE_PACKAGE_REWRITTEN` and no write.
3. Append only: never assign to `selected_chunks`, `evidence_groups`,
   `coverage`, counts, `selection_budget`, `queries` or `change_scan`.
4. Every appended Chunk must resolve to a persisted Document version and parse
   batch; reuse `validate_selected_versions()` (called from `_agent_evidence()`
   in `execution/service.py`; rejection asserted in
   `test_research.py:600-608`). The report read path degrades missing rows to
   `chunk_unavailable` / `chunk_not_in_current_parse_batch` gaps
   (`api.py:2037-2140`, `:1931-1960`) instead of failing, so an invalid append
   would otherwise become a permanently broken citation.
5. Deduplicate by `input_id` and by `(document_id, document_version, chunk_id)`;
   the same Tool result, or a Document already in the initial selection, must
   not be appended twice (design.md: identical `(tool_id, 规范化参数)` is not
   called twice; `GAP_TOOL_CALL_DUPLICATE` already exists).
6. Bounds: rounds ≤ 2, plan requests ≤ 4, plus the per-record caps in
   `supplement.py:69-78`. `merged_research_refs` raises on `chunks == []`,
   non-string ids or non-int positions (`:738-780`), so a malformed record
   breaks the Agent step at read time — validate at write time.
7. Ledger entries must be safe to persist and to return: no credentials,
   endpoint query strings, or raw response bodies (AGENTS.md rule 6;
   `.trellis/spec/backend/api-guidelines.md` "响应只输出已持久化的安全字段").
8. Keep real source times with explicit labels: `published_at` / `collected_at`
   plus `published_after_as_of` / `collected_after_as_of`
   (`docs/execution.md:145`, `docs/research.md:117-120`), and
   `source_time_unavailable` when the Tool could not report them.

### 4. Hash design: one fixed defect, three open points

1. **`run_id` exclusion — fixed.** The earlier package-column draft hashed
   `run_id`, which would have made a verbatim retry copy fail verification. The
   current `INITIAL_SNAPSHOT_FIELDS` omits it and the docstring states the
   reason. Keep it that way.
2. **Timezone-dependent rendering — open.** `_snapshot_value`
   (`research/service.py:676-687`) formats `datetime.isoformat()` without
   normalizing, and `as_of` is `DateTime(timezone=True)` (`models.py:289`).
   PostgreSQL renders `timestamptz` in the session `TimeZone`; a `+08:00` render
   where the pin was computed with `+00:00` changes the hash with no rewrite at
   all, which would abort an otherwise valid append as
   `EVIDENCE_PACKAGE_REWRITTEN`. Normalize with `astimezone(UTC)` before
   formatting. (`change_scan` watermarks are integers — `changes.py:166-181` —
   so they are safe.)
3. **Write-time vs read-time fidelity — unverified.** The hash may be computed
   once from an in-Session ORM object and later from a re-read row. Hashed
   content includes floats (`"similarity": hit.similarity`,
   `research/service.py:649-651`); psycopg3 JSONB round-trips floats stably, but
   only a real PostgreSQL round-trip on the test server can prove it.
4. **Version marker — recommended.** Add `{"snapshot_version": 1, ...}` (or a
   `snapshot_policy_version` key) to the hashed payload so a later change to the
   field set or normalization cannot silently reinterpret a pinned hash. Note
   the pinned entry only exists once an append has happened, so a Run that never
   supplements has no pin — which is fine, because nothing is appended.

Scope of the guarantee: it detects rewrite by this application, not by a
privileged DB writer; the PRD asks only that the initial package not be
overwritten by the research flow.

### 5. Transaction behaviour

Current, verified facts:

- `select_evidence` commits the package alone with no window
  (`research/service.py:532-541`); with a window the caller commits package +
  scan checkpoint together (`_run_evidence_selection()` in
  `execution/service.py`).
- The final Agent `TaskStepResult` is flushed without committing;
  `_finish_research_run()` commits the final step, Analysis, Report, Run success
  and cursor advance in one transaction, and the failure path in the run loop
  rolls that unit back.
- `append_research_entries` (`supplement.py:334-387`) does its own lock →
  verify → extend → `session.commit()`, so an append is durable independently
  of the final research transaction — exactly what AC-3 needs.
- `ExecutionContext` now carries an independent `session_factory` for Tool
  ingestion and Document stages, with the comment that the Run's own Session
  must never be shared with work that commits on its own
  (`execution/service.py:119-130`).

Required ordering for one appended input (must not change): authorize → call
Tool with no DB lock and no ambient transaction → `ingest_document()` for the
bytes actually received → parse/classify/facts for that Document only → lock the
TaskRun row → verify ownership + pinned hash → append (+ pin) → commit → hand
the merged refs to the Agent.

Three gaps to close:

1. `_analysis_and_report()` must set `Analysis.research_inputs` from the Run
   ledger inside the final transaction (it does not yet), otherwise the report
   has no frozen input record.
2. `Analysis.coverage` still copies the initial `package.coverage`. Supplemented
   gaps must be merged there,
   because `render_report` renders `analysis.coverage["gaps"]`
   (`research/service.py:966-973`); otherwise an AC-3 report shows an empty
   Coverage section.
3. `retry_run()` must copy `research_inputs` (ledger + pinned hash) from the
   source Run to the new Run in the same retry transaction (nothing copies it
   today), so a retry both keeps and can extend the supplemented inputs.

### 6. API / report compatibility

All existing keys keep their names and meaning; new fields are additive, and the
frontend decodes with `.passthrough()` for report detail
(`frontend/src/api/schemas.ts:694-710`), evidence entries (`:637-667`) and run
detail (`:381-388`), so additive keys break nothing.

1. `_package_payload` (`api.py:1897-1914`): unchanged, or add
   `initial_snapshot_hash` (computed from the package) and `research_input_count`
   / `research_gap_count` from the ledger. Run detail builds its own compact
   package payload (`api.py:711-724`) and can add the same counts.
2. Report detail (`api.py:1596-1639`): build `evidence` from
   `merged_research_refs(package, analysis.research_inputs)` — reading the
   Analysis copy, so a published report is immune to later Run changes — and
   fall back to `run.research_inputs` only for a legacy/absent copy. Keep
   `evidence`, `cited_chunk_ids`, `coverage_gaps` and `latest_collection_states`
   as they are; `cited_chunk_ids` needs no change because it filters the
   resolved entries via `_cited_keys(analysis)` (`:1916-1929`). Add a top-level
   `research_inputs` list (tool id, title, url, document ids, timing labels,
   purpose) so a supplement that produced no citable Chunk stays visible.
3. `/api/reports/{report_id}/evidence/{chunk_id}` (`:1642-1707`): search the
   merged view; keep `404` for a ref in neither the initial selection nor the
   ledger. `.trellis/spec/backend/api-guidelines.md` states the rule as "片段不在
   该 Run 的 `selected_chunks` 内一律 `404`"; that sentence must be widened
   through the `update-spec` workflow, not by editing code comments.
4. `_gaps` (`api.py:1931-1960`): `coverage_gaps` is `package.coverage.gaps` ∪
   read-time observations, and the spec fixes the observed word list
   (`document_not_stored`, `chunk_unavailable`, `partial_source_content`,
   `snapshot_storage_unconfigured`, `chunk_not_in_current_parse_batch`,
   `chunk_not_in_current_index`, `run_not_succeeded`). Do **not** silently add
   Tool vocabulary there; surface ledger gaps through the new `research_inputs`
   / `research_gaps` field and through `analysis.coverage.gaps` (which drives
   the Report Markdown).
5. Report Markdown keeps its current shape; a supplemental-inputs section is
   optional and must never imply coverage the Run did not have.

### 7. AC-3 decision: no evidence + failed supplementation ends as a succeeded report with gaps

**Recommendation: succeeded Run, succeeded final Agent step, Analysis with
`findings=[]` and non-empty `open_questions`/coverage gaps, Report saved, and
the cursor advanced only when the final transaction succeeds and the change
scan completed.**

#### 7.1 Why this matches the current schema and step semantics

- The schema does not require findings: `Analysis.findings` / `open_questions`
  are JSON arrays with array-type checks only (`models.py:326-334`).
- `validate_analysis_content` (`research/service.py:859-914`) accepts
  `findings: []`, requires only that each *present* finding cite an allowed ref
  (`:889-899`), and does not require `open_questions` to be non-empty.
- `render_report` already renders the zero-finding case ("No sufficiently
  supported findings were identified.") plus a Coverage section
  (`research/service.py:944-973`).
- `test_incremental_evidence.py:780-850` already drives the exact state
  (zero selected evidence, `findings=[]`, `evidence_refs=[]`, succeeded step,
  Report, advanced cursor), so this is not a new capability — only a gate the
  normal `research_agent` path does not yet allow.
- `docs/execution.md` window rule 4 ties Run success to the final Agent step
  plus Analysis/Report/package integrity, not to finding count, and keeps the
  scan checkpoint when a later step fails. `docs/validation.md:34` requires
  supplements to be saved and cited and gaps to be stated;
  `docs/research.md:123` already permits "明确说明覆盖不足的报告".
- PRD AC-3 ("工具失败/处理超时如实留下缺口并有界结束") asks for a bounded end
  with the truth recorded. Failing the Run discards evidence that was actually
  acquired and blocks the retry path AC-3 also requires.

#### 7.2 The remaining gate (and the one already removed)

The Python gate is gone: `_agent_evidence()` (`execution/service.py:1960-2005`)
now returns the merged view without requiring a non-empty evidence list, and no
`EVIDENCE_UNAVAILABLE` raise remains in the module. The remaining gate is
configuration: `config/tasks.yaml:141` sets `evidence.minItems: 1`, and the
context is validated against `agent.input_schema` (call site
`execution/service.py` `_run_agent`; `minItems` enforced at
`providers.py:742`), so an empty evidence array still raises
`AGENT_INPUT_SCHEMA_INVALID`. Relax to `minItems: 0` for the research-writing
Agent and state the empty-evidence semantics in the task/agent description.

`config/tasks.yaml:55-72` also ships `unavailable_evidence_failure`, whose
objective is to demonstrate failure without evidence. Under the new semantics it
must demonstrate a gap-aware success instead, or be removed.

#### 7.3 Gap vs failure classification

Gap, recorded, Run still succeeds: `tool_timeout`, `tool_transport_failed`,
`tool_call_failed`, `tool_result_invalid`, response-size/depth limits,
`tool_budget_exhausted`, `tool_content_unavailable`, `tool_plan_unavailable`,
the `supplement_*` processing codes, `source_time_unavailable`, and
`no_selected_evidence` (already in `package.coverage.gaps`). All of these codes
already exist in `supplement.py:82-103`, which is a strong signal the intended
semantics are "gap, not failure".

Step/Run failure, never downgraded to a gap: `EVIDENCE_PACKAGE_REWRITTEN`, an
Agent proposing an unauthorized Tool (`GAP_TOOL_NOT_AUTHORIZED` is recorded for
the model's proposal; a *trusted Task* referencing a missing/disabled Tool is a
configuration failure), Tool configuration load failure, Tool snapshot mismatch
against the Run snapshot, invalid Agent output after bounded retries, invalid
citations, Report persistence failure, ownership loss, and the existing
`FINAL_RESEARCH_STEP_NOT_SUCCEEDED` rule
(`test_execution.py:1253-1307` must keep passing unchanged).

Two boundary cases worth an explicit owning-doc decision:

- `GAP_TOOL_ARGUMENTS_INVALID` for the model's own proposal belongs to the
  bounded-retry path as invalid Agent output, not to the gap list.
- A missing credential/environment for an authorized Tool is runtime
  unavailability recorded as a gap; the model did not expand its authority.

### 8. Existing tests: what actually changes

Verified construction sites and their status under the implemented design:

| Site | Constructs | Needs change? |
| --- | --- | --- |
| `backend/tests/test_execution.py:718` | `EvidencePackage(...)` in `_evidence_selection_result()` (used by the empty-evidence, skipped-step and retry tests at `:1213`, `:1276`, `:1495`) | No hash needed (package unchanged); extend for ledger-based assertions |
| `backend/tests/test_execution.py:1826` | `EvidencePackage(...)` in the no-overwrite-success test | No |
| `backend/tests/test_report_api.py:268` | `EvidencePackage(...)` in `persist_run()` | No; extend the fixture so one case carries `Analysis.research_inputs` + ledger gaps |
| `backend/tests/test_console_api.py:1613` | zero-evidence `EvidencePackage(...)`/`Analysis` fixture | No; `Analysis.research_inputs` has an ORM `default=list`, so the fixture still inserts |
| `backend/tests/test_scheduler.py:1070` | `EvidencePackage(...)` in the retry-reuse test | No; add ledger copy assertions |
| `backend/tests/ac3_drill.py:535-560`, `:739-786` | reads package/step/retry state | Extend to ledger + Analysis copies |

No `conftest.py` exists under `backend/tests/`; helpers are per file
(`_evidence_selection_result`, `persist_run`, `make_*`).

Behavioural tests to add or change:

- `test_execution.py:1185-1250` `test_empty_evidence_fails_without_overwriting_success`
  — currently asserts Run `failed`, step `EVIDENCE_UNAVAILABLE`, no cursor. Flip
  to succeeded + empty findings + gaps when the Task accepts supplementing/no
  evidence; keep the "last success not overwritten" property for a Run that
  genuinely fails (e.g. invalid model output).
- `test_execution.py:1253-1307` skipped final step — must stay green unchanged.
- `test_execution.py:1485-1576` retry window reuse — add: ledger and pinned hash
  copied to the retry Run, source Run unchanged, appended input still citable.
- `test_research.py:610-655` empty package persistence — add: `evidence_package_hash`
  is stable across a re-read, and `merged_research_refs(package, None)` equals
  the initial refs.
- `test_report_api.py:220-360` / `:375-447` — add one merged-evidence case
  (initial + appended Chunk), one appended-Chunk single-evidence route case, one
  "appended ref no longer resolvable" gap case, and one case proving
  `coverage_gaps` does not gain new vocabulary.
- `test_console_api.py:1613-1660` — assert the new report fields if exposed.
- `test_scheduler.py:1059-1143` — retry copies the ledger/pin and re-points the
  step to the new package.
- `test_incremental_evidence.py:790-850` — reuse as the AC-3 precedent proof.

Structural verification required by
`.trellis/spec/backend/database-guidelines.md`: `alembic upgrade head` +
`alembic check` on a fresh database **on the test server**, because the two new
columns and their named array constraints must match between `models.py` and
`0001_initial.py`.

### 9. Files found

- `backend/src/stocks/models.py:183-241, 326-347` — `TaskRun.research_inputs`,
  `Analysis.research_inputs`, named array checks.
- `backend/alembic/versions/0001_initial.py` — mirrors both columns/constraints.
- `backend/src/stocks/research/service.py:57-79, 676-827` — frozen fields,
  hash, merged view, gap extraction, pin entry.
- `backend/src/stocks/research/supplement.py:69-103, 149-200, 330-420` — bounds,
  gap vocabulary, `append_research_entries`, `execute_plan`.
- `backend/src/stocks/execution/service.py` — `ExecutionContext` seams,
  `retry_run`, `_run_evidence_selection`, `_run_agent`, `_agent_evidence`,
  `_supplement_rounds`, `_finish_research_run`, `_analysis_and_report` (line
  numbers shift while the file is edited).
- `backend/src/stocks/api.py:711-724, 1596-1707, 1897-1960, 2037-2140`.
- `backend/src/stocks/providers.py:742` — `minItems` enforcement.
- `backend/src/stocks/execution/config.py:485-509, 852-856`.
- `backend/src/stocks/tools/{__init__,config,client,errors}.py`;
  `config/tools.yaml`; `config/tasks.yaml:27-72, 113-200`.
- `frontend/src/api/schemas.ts:381-388, 637-710`.
- Tests: `test_execution.py`, `test_report_api.py`, `test_console_api.py`,
  `test_scheduler.py`, `test_research.py`, `test_incremental_evidence.py`,
  `test_tools.py`, `ac3_drill.py`.
- Owning docs: `docs/execution.md:120-160`, `docs/research.md:105-175`,
  `docs/validation.md:30-40`, `docs/interface.md:45-64`.

### 10. Related specs

- `.trellis/spec/backend/database-guidelines.md` — model/schema parity, named
  constraints, structural-change verification, no incremental migrations.
- `.trellis/spec/backend/api-guidelines.md` — read-by-reference rule, the
  "chunk must belong to this Run's package, else 404" sentence that the ledger
  widens, the fixed `coverage_gaps` word list, safe-field responses.
- `.trellis/spec/backend/testing-guidelines.md` — test server only.
- `.trellis/spec/guides/agent-execution-guide.md` — bounded diagnosis/hand-back,
  referenced by AGENTS.md rule 10.

### 11. External references

- MCP Streamable HTTP, protocol revision `2025-11-25`
  (`backend/src/stocks/tools/config.py:24`) — fixed by the task decision record;
  no external source was needed for this topic.

## Caveats / Not Found

- **Concurrent editing.** `research/service.py`, `models.py`,
  `research/supplement.py`, `tools/client.py` and `execution/service.py` all
  changed while this research ran; an earlier version of this note described a
  package-column design with `research_inputs`, `research_gaps` and
  `initial_snapshot_hash` on `evidence_packages`, which was replaced mid-session
  by the Run-ledger design documented here. Re-read the files before editing.
- **`design.md` is now out of date** (`:39-40` still describes the
  EvidencePackage columns and a `research_inputs`/`research_gaps`/
  `initial_snapshot_hash` triple). That file is a task artifact owned by the main
  session, not by this research.
- **Not implemented yet** (verified absent at the end of this session):
  `Analysis.research_inputs` population, `retry_run` ledger copy, any
  API/report change, and the `config/tasks.yaml` `minItems` relaxation. The
  merged read view (`_agent_evidence`) and the supplement rounds were wired into
  `_run_agent` while this note was being written, so the empty-evidence gate is
  now only a configuration gate; AC-3 is still unreachable end to end until
  `minItems` changes and the Analysis/report path reads the ledger.
- **`as_of` timezone normalization is missing** in `_snapshot_value`
  (`research/service.py:676-687`); it can produce a spurious
  `EVIDENCE_PACKAGE_REWRITTEN` on a valid append if the connection renders
  `timestamptz` in a non-UTC offset.
- **No version marker** in the hashed payload yet; changing
  `INITIAL_SNAPSHOT_FIELDS` or the normalization later would invalidate
  existing pins with no way to tell old from new.
- **Fact-only and historical-Analysis inputs have no record shape.**
  `docs/execution.md:133` lists "分类输入快照和历史 Analysis 引用", but
  `merged_research_refs` handles only Chunk-bearing `kind="input"` entries and
  the Agent context in `_run_agent` still contains no `previous_analysis_refs`.
- **`coverage_gaps` vocabulary.** Ledger gap codes cannot be added to
  `coverage_gaps` without an owning-doc/spec update; this note recommends
  exposing them through the ledger-derived fields instead. The decision belongs
  to the API spec owner.
- **Not verified by execution.** No tests, imports, linters or Alembic checks
  were run in this session; per AGENTS.md all validation runs on the test server
  `100.120.14.84`. Whether the hash survives a real PostgreSQL JSONB round-trip
  (floats, timestamps) is still unproven.
