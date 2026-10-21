# Research: Repository-first supplemental research in the Run execution pipeline

- Query: How can V2-01 minimally integrate Repository-first supplemental research into the existing execution pipeline — session/transaction boundaries for synchronous processing of newly ingested Documents; running parse/classify/facts/index for one Document without waiting for the global backlog; the initial Evidence Package snapshot plus `research_inputs`; final allowed-reference validation; API/report reading; and retry copy semantics?
- Scope: internal
- Date: 2026-10-22

## Findings

### 0. Verified current state (corrections to earlier assumptions)

The working tree is further along than the PRD-era baseline. These points change the plan:

1. `backend/src/stocks/tools/` is no longer just config + errors. `McpClient` (`tools/client.py:907`) is a working Streamable-HTTP client that already enforces authorization, argument schema, byte/depth/SSE budgets and the output schema, and returns `ToolCallResult` (`tools/client.py:54-61`, fields `tool_id`, `result`, `value`, `is_error`, `response_bytes`, `content_type`). Do **not** design a second Tool executor; the missing piece is Run integration, not transport.
2. `AgentConfig.tools` is still a hard lock: `tools: list[str] = Field(default_factory=list, max_length=0)` (`execution/config.py:476-485`). The id/uniqueness validator (`execution/config.py:500-508`) already exists, so opening the field is a one-line change plus tests.
3. `Settings.tools_config_path` already exists (`settings.py:37`, default `config/tools.yaml`) and `config/tools.yaml` exists with `tools: []` (deny by default).
4. `build_stage_registry()` (`processing/processors.py:253-309`) now wires **all four** stages: parse, index, classify (`load_classify_processor`, `classification/service.py:439`) and facts (`load_facts_processor`, `extraction/service.py:918`), each through a lazy `*_factory`. The docstring in `processing/runner.py:1-7` ("Registering a real Fact extractor belongs to a later task") is stale.
5. `AgentConfig.tools` entries would be snapshotted nowhere today: `_model_snapshot()` (`execution/service.py:227-247`) writes `{"agents": {...}, "embedding": ...}` only.
6. `run_stage_now()` (`processing/runner.py:381-414`) already accepts a `document_id` filter and is documented as the normal claim/ownership/result path for explicit commands. It is the correct primitive for "process this Document now"; `run_next_document_stage()` (`runner.py:548`) is the global-backlog variant and must not be used for supplemental content.

### 1. Pipeline map (files and seams)

| Path | Relevant description |
| --- | --- |
| `backend/src/stocks/execution/service.py` | Run step loop, `ExecutionContext`, Agent step, Analysis/Report, ownership fences, retry copy. |
| `backend/src/stocks/worker.py` | `WorkerContext` (`:67`) and `execute_next_run()` (`:247-277`) build the `ExecutionContext`; no tool path is passed today. |
| `backend/src/stocks/repository.py` | `ingest_document()` (`:151-243`), `current_document_hits()` (`:723-760`), `SearchHit` (`:117-133`), `read_document_bytes()` (`:338`). |
| `backend/src/stocks/facts.py` | `facts_for_documents()` (`:315-345`), `fact_to_dict()` (`:348`). |
| `backend/src/stocks/processing/runner.py` | `run_stage_now()` (`:381`), `execute_claimed_stage()` (`:150`), `_fresh_stage_state()` (`:299-315`, `populate_existing=True`), `DOCUMENT_STAGE_ORDER` (`:378`). |
| `backend/src/stocks/processing/stages.py` | `STAGE_DEPENDENCIES` (`:49-54`), wait reasons (`:58-62`), `claim_stage()` (`:440-537`), `_ready_conditions()` (`:372-395`), `_claimable()` (`:540-551`), `apply_stage_outcome()` (`:591-670`), `finish_stage()` (`:908`), `reopen_stage()` (`:1146`), `recover_stage()` (`:1065`). |
| `backend/src/stocks/models.py` | `TaskRun` (`:110-240`), `EvidencePackage` (`:242-300`), `Analysis` (`:303-329`), `Report` (`:331`), `Document` (`:444`), `DocumentChunk` (`:520`), `DocumentParseBatch` (`:539`), `DocumentParseState` (`:577`), `DocumentProcessingState` (`:679`), `Fact` (`:765`). |
| `backend/src/stocks/research/service.py` | `durable_evidence_ref()` (`:641`), `validate_selected_versions()` (`:735`), `parse_agent_output()` (`:798`), `validate_analysis_content()` (`:859`), `render_report()` (`:917`). |
| `backend/src/stocks/api.py` | `run_detail()` (`:649`), `report_detail()` (`:1596`), `report_evidence()` (`:1642`), `_report_chain()` (`:1710`), `_selected_chunks()` (`:1888`), `_package_payload()` (`:1897`), `_cited_keys()` (`:1917`), `_gaps()` (`:1931`), `_resolve_evidence()` (`:2037`). |
| `backend/src/stocks/tools/*` | `ToolConfig`/`ToolBudgets`/`authorize_tools`/`load_tool_configuration` (`config.py:491-806`), `McpClient.call_tool()` (`client.py:907+`), error codes (`errors.py:29-48`). |
| `backend/alembic/versions/0001_initial.py` | Sole initial schema; any new column must be mirrored here (no incremental migration). |
| `backend/tests/test_execution.py`, `test_research.py`, `test_report_api.py`, `test_processing_retry.py`, `test_worker_processing.py`, `test_tools.py` | Existing fixtures and behaviors the change must not break. |
| `docs/execution.md:123-155`, `docs/research.md:110-150`, `docs/data.md:20-57,315-340`, `docs/validation.md:34` | Domain contracts: `research_inputs` ownership, unbounded-backlog rule, untrusted external content, acceptance intent. |

### 2. Persistence: immutable package plus an append-only Run ledger

**Ownership conflict to resolve first.** `docs/execution.md:133` states `research_inputs` belongs to **Task Run / Analysis** ("记录实际使用的 Document 内容版本、Chunk/Fact 引用…"). The task's own `design.md` instead puts `research_inputs`/`research_gaps`/`initial_snapshot_hash` on `EvidencePackage`. Both are workable, but the domain doc is the single source of truth for ownership, and putting the ledger on the Run keeps `EvidencePackage` a pure selection snapshot (`docs/research.md:118-123`: 不改写原始证据包快照). Recommendation: Run/Analysis placement; main session should update `design.md` accordingly.

Minimal model change (`backend/src/stocks/models.py`):

- `TaskRun.research_inputs: Mapped[list]` — `JSONB`, `nullable=False`, `default=list`, `server_default=text("'[]'::jsonb")`, with `CheckConstraint("jsonb_typeof(research_inputs) = 'array'", name="ck_task_runs_research_inputs_array")`.
- `TaskRun.tool_state: Mapped[dict | None]` — `JSONB(none_as_null=True)`, nullable, with a `jsonb_typeof IS NULL OR = 'object'` check. Holds call/gap/budget state and safe provenance only (tool id, normalized-argument hash, status, byte counts, timestamps) — never credentials, endpoints with tokens, or raw Tool payloads.
- `Analysis.research_inputs: Mapped[list]` — `JSONB`, `nullable=False`, array check. The final frozen ledger copied at artifact creation, so a later Run/ledger change cannot rewrite what a published report used.

Mirror all three columns and constraints in `backend/alembic/versions/0001_initial.py` (`task_runs` around `:99-110`, `analyses` around `:267`). Per `database-guidelines.md#结构变更`, a fresh-DB `alembic upgrade head` + `alembic check` on the test server is required after this change; no incremental migration.

Ledger entry shape (one entry per citable unit, normalized in `research/service.py`):

```
{
  "input_id": "<uuid4>",
  "origin": "initial" | "supplement",
  "evidence_package_id": "<uuid, initial only>",
  "document_id", "document_version", "source_id", "position"|None,
  "chunk_id"|None, "parse_batch_id"|None,
  "fact_ids": [...],
  "tool_provenance": {"tool_id", "call_index", "arguments_hash"} | None,
  "reason": "<adoption reason, supplement only>",
  "flags": {"published_after_as_of": bool, "collected_after_as_of": bool},
  "recorded_at": "<iso>"
}
```

Also store the initial-package integrity value on the initial entry: `evidence_package_hash` = SHA-256 over the package's immutable fields (`objective`, `scope`, `as_of`, `selection_policy_version`, `queries`, `selected_chunks`, `evidence_groups`, `coverage`, `change_scan`). This preserves design.md's `EVIDENCE_PACKAGE_REWRITTEN` guard without mutating the package row.

### 3. Entry point: where the Run calls supplemental processing

Inside the step loop of `execute_claimed_run()` (`execution/service.py:617-940`):

- The Agent branch is `output, evidence_refs, usage = _run_agent(...)` at `:784-793`, followed by `_analysis_and_report(...)` at `:795`.
- `_ensure_run_still_owned(session, run.id, process_token)` is already called once per step at `:727`.
- `_persist_step()` (`:1443-1484`) asserts ownership at entry and (for research) defers the final commit by passing `commit=False` at `:864-868`; `_finish_research_run()` (`:1232`) commits the final step + Analysis + Report + cursor together.

Plan: keep the existing step count (design.md explicitly keeps one `research_agent` step and no new orchestration). Insert supplemental work **inside `_run_agent()`**, between building `allowed_refs` and the final model call (`service.py:1920-2056`), so the Agent's single final call sees initial + adopted evidence. `ExecutionContext` (`:92-98`) must carry the new dependencies; extend it with:

- `session_factory` (for the independent ingest/stage/read/append transactions),
- `processing_registry` (lazy `StageRegistry`, reuse `WorkerContext.processing_registry()` semantics — do not build it eagerly, it must not read model credentials for Runs that never supplement),
- `tools_config_path: str | None = None`,
- optional `tool_transport` / `tool_environment` test seams (mirroring `model_transport`).

`worker.execute_next_run()` (`worker.py:247-277`) constructs `ExecutionContext`; add `session_factory=context.session_factory`, `processing_registry=context.processing_registry()`, `tools_config_path=context.settings.tools_config_path`, `tool_transport=context.tool_transport`. `WorkerContext` needs a `tools_config_path`/`tool_transport` field with a `config/tools.yaml` default.

### 4. Session and transaction boundaries (the critical part)

Rules derived from the code and from `.trellis/spec/backend/worker-guidelines.md` contracts 1, 5 and 7c:

1. **Never share the Run Session with stage or Tool work.** `run_stage_now(session_factory, ...)` (`runner.py:381-414`) opens two Sessions of its own: one to `claim_stage`, one to `execute_claimed_stage`. `McpClient`/`ingest_document` likewise must run with a fresh Session. The Agent's long-lived Session must not be passed in.
2. **Ingest first, in its own Session, and only after it commits.** `ingest_document()` (`repository.py:151-243`) writes the S3 object, the immutable Document version, the four `DocumentProcessingState` rows (`create_stage_states`), and the change record, then commits internally (`:222-240`). Stages and the ledger append must happen strictly after that commit; there is no cross-S3/PostgreSQL transaction (`database-guidelines.md`), so if the object is stored and the DB write fails, the object stays unreachable and must never be treated as evidence.
3. **One fresh Session per stage, re-reading authoritative rows.** `expire_on_commit=False` (`db.py:6`) means cached entities are not authorization (`worker-guidelines.md` 7c). After each `run_stage_now()` call, read `DocumentProcessingState` with `select(...).execution_options(populate_existing=True)` — as `runner._fresh_stage_state()` (`runner.py:299-315`) already does — before deciding whether the stage is usable. `get_stage_state()` (`stages.py:289`) alone can return a stale identity-mapped row.
4. **The Run Session must roll back before independent commits and re-read afterwards.** In `_run_agent` (or a helper it calls), call `session.rollback()` before invoking supplemental work so the long-lived Session does not pin an open snapshot/lock while external HTTP + S3 + several other transactions run; then `_ensure_run_still_owned(session, run.id, process_token)` and re-read `run`/`research_inputs` with `populate_existing=True`. The authoritative write fences remain `_assert_run_ownership` (inside `_persist_step`, `:1458`) and `_finish_run`/`_finish_research_run`.
5. **Append is one short row-locked transaction.** `append_research_inputs()` should `select(TaskRun).where(id=..., status='running', process_token=...).with_for_update()` — mirroring `_assert_run_ownership` (`:1387-1415`) — then merge entries into `research_inputs`, update `tool_state`, and commit. A Run ended by the Owner must never gain a new ledger entry.
6. **No stage is ever stolen.** Supplemental processing must not call `retry_stage`, `reopen_stage`, `reprocess_requested`, or `recover_stage` (`stages.py:950/1011/1065/1146`). A stage that is `running` under another token (Worker slot or another Run) is a recorded gap, not work to take over.

### 5. Targeted processing without touching the global backlog

Per Document, in this order: `parse → index → classify → facts`, i.e. `DOCUMENT_STAGE_ORDER` (`runner.py:378`), each via `run_stage_now(session_factory, settings, registry, document.id, stage, owner_token=...)`.

Semantics that make each stage's result interpretable:

- Dependencies (`stages.py:49-54`): `classify` and `index` require a **completed** `parse`; `facts` has no hard dependency.
- Terminal-good: `completed` and `skipped` — both mean "no further automatic work on this stage for this Run"; `skipped` still yields Facts for structured content (`DIRECT_SKIP_APPLICABILITY`, `stages.py:71-76`).
- `run_stage_now()` returns `None` when the stage is not claimable. Re-read the state to classify the `None`:
  - `pending` + `wait_reason = PROCESSOR_NOT_AVAILABLE` (`stages.py:58`) → capability gap;
  - `pending` + `wait_reason = CLASSIFICATION_PENDING` (`stages.py:62`) → Facts waiting on the classifier's `should_extract` verdict, no attempt consumed;
  - `failed` with `next_attempt_at > now` → backoff; do not sleep or loop (`_claimable`, `stages.py:540-551`, `_ready_conditions`, `:372-395`);
  - `running` with a foreign `owner_token` → another executor owns it;
  - `pending` blocked by an unfinished dependency → dependency gap.
- Use a per-attempt unique owner token, e.g. `f"{process_token}:supplement:{uuid4()}"`, so a leaked `running` row is attributable and cannot collide with Worker document slots.
- `facts` may legitimately produce a **Fact-only** input for structured API responses (no Chunks). The Agent's evidence union must therefore support a Fact reference with `chunk_id = None` per `docs/research.md` ("纯结构化 API 响应…`ref` 为 `null`").

Recommended helper set in a new module `backend/src/stocks/research/supplement.py`:

```python
SUPPLEMENT_STAGES = (STAGE_PARSE, STAGE_INDEX, STAGE_CLASSIFY, STAGE_FACTS)

def process_supplemental_content(
    session_factory, config, registry, request, *, owner_token, now=None
) -> SupplementOutcome: ...          # ingest → stages → evidence read → gaps

def read_supplemental_evidence(
    session, document_id, *, per_document_limit=8, fact_limit=200
) -> tuple[list[dict], list[dict]]: ...   # (chunk_refs, fact_refs)

def append_research_inputs(
    session, run_id, *, process_token, entries, tool_state=None
) -> list[dict]: ...

def merged_research_inputs(
    session, run, package
) -> tuple[list[dict], list[dict]]: ...  # (references, gaps), verified against the package hash
```

`read_supplemental_evidence` uses only committed evidence:

- `current_document_hits(session, [document_id], per_document_limit=...)` (`repository.py:723-760`) — readable Chunks of the Document's **current successful** parse batch; each `SearchHit` (`repository.py:117-133`) already carries `chunk_id`, `parse_batch_id`, `position`, `version`, `collected_at`, `published_at`.
- `facts_for_documents(session, [document_id], limit=...)` (`facts.py:315-345`) — persisted Facts, including field-path Facts that have no Chunk.
- Never use a Tool/search snippet as citable text: only Chunks persisted by a successful parse produce a citable reference (`docs/research.md`; `test_report_api.py:539-601` already treats a missing Chunk as a gap, not as evidence).

### 6. Tool runtime integration (reuse, do not rebuild)

- Configuration: `load_tool_configuration(path)` / `ToolConfiguration.authorize(ids)` / `authorize_tools` (`tools/config.py:676-806`). `ToolConfig` already declares `source_id` (mandatory, `:555-600`) which is what makes a Tool result registrable; the search provider must never be recorded as the Publisher (`docs/execution.md:280-291`).
- Calls: `McpClient(configuration, authorized_tool_ids, ...)` + `client.call_tool(tool_id, arguments)` → `ToolCallResult` mapping is already applied via `result_mapping`. Wrap it in `with McpClient(...) as client:` so sockets close.
- Authorization snapshot: at Run creation, write `TaskRun.model_snapshot["tools"]` = per authorized tool `{id, remote_name, endpoint_hash, input_schema_hash, output_schema_hash, budgets, timeout_seconds}` (reuse the `_endpoint_hash` helper pattern used by `_snapshot_routing`, `service.py:1806+`). At execution time re-load `config/tools.yaml` and compare item by item; a mismatch fails the step (`TOOL_CONFIGURATION_CHANGED`) exactly like the `MODEL_ROUTE_SNAPSHOT_INVALID` comparison loop in `_run_agent` (`:1987-2011`).
- Credentials only via `bearer_token_env` → `os.environ` inside `McpClient`; never in config, snapshot, log, `research_inputs` or `tool_state`.
- Budgets: `McpClient` counts calls in memory only (`client.py:937-940`). Durable remaining budget must live in `TaskRun.tool_state` and be enforced before each call; a retry then cannot silently repeat spent calls (AC-3).
- Exactly-once is impossible: a timeout can happen after the remote executed. Record an `unknown` call state and treat the retry as a gap unless the provider offers idempotency keys.
- The current Agent makes exactly **one** model call (`service.py:2056-2078`). A Tool loop needs a deliberate second schema (a "supplemental plan" output) or an extra trusted step; do not overload the final analysis schema. The PRD/design intentionally allow a single structured plan call whose emptiness means "no external call".

### 7. Final allowed-reference validation

Today `_run_agent()` builds `allowed_refs = validate_selected_versions(session, package.selected_chunks)` (`service.py:1920-1927`), keyed `(chunk_id, document_id)`. `validate_selected_versions()` (`research/service.py:735-795`) joins `DocumentChunk → Document → DocumentParseState` and requires `DocumentParseState.current_batch_id == parse_batch_id`. Two tests pin that behavior: `test_validate_selected_versions_rejects_stale_document_version` and `…_rejects_superseded_parse_batch` (`test_research.py:568-607`).

That currency requirement is right for selection time and wrong for a stored report: a later reparse would invalidate an already-cited Chunk. Plan:

- Keep `validate_selected_versions()` for selection-time checks; add `validate_research_input_versions(session, references)` in `research/service.py` for the merged union. It must verify the **exact** Document version, Chunk id and parse-batch identity without requiring the batch to still be current, reject forged/cross-Document/version-mismatched ids, verify every `fact_id` belongs to the exact `document_id`, and, for text Facts, verify the referenced Chunk/batch when `evidence_ref` names one.
- Duplicate suppression by normalized identity: `("chunk", chunk_id, document_id)` and `("fact", fact_id, document_id)`; a Fact attached to an adopted Chunk keeps "one Document chain counts once" (`docs/research.md`, evidence-group rule).
- Citation schemas assume `chunk_id` everywhere: `parse_agent_output()` (`research/service.py:798-857`), `validate_analysis_content()` (`:859-915`), `render_report()` (`:917-975`, `f"{ref['document_id']}#{ref['chunk_id']}"`), and the trusted schemas in `config/tasks.yaml:150-201` and `backend/tests/test_execution.py:~188-224`. Fact-only references need a deliberate schema change (e.g. `oneOf`/optional `chunk_id` with required `document_id` plus `fact_id`), persisted refs that keep both ids, and a `render_report` branch that prints `document_id#fact:<fact_id>` when `chunk_id` is null. `durable_evidence_ref()` (`:641-651`) currently projects only Chunk fields and needs a Fact variant.
- Agent input shape: extend the `evidence` items produced at `service.py:1934-1945` (role/kinds/reasons/fact_refs/chunk_ids merging from `package.evidence_groups`) so supplemental items carry `origin`, `tool_provenance`, `flags`, and the same group context; keep the union empty-tolerant (`AC`/design.md 无证据语义) but keep "every finding cites evidence" in `validate_analysis_content`.
- Persist the final effective ledger onto `Analysis` in `_analysis_and_report()` (`service.py:2184-2259`) in the same flush that already builds `Analysis`/`Report`; `_validate_research_completion()` (`:1185-1230`) gains a durable-union check so a Run cannot be reported as succeeded while its ledger references unresolvable ids.

### 8. API and report reading

Current reads are package-only: `report_detail()` → `_report_chain()` → `_selected_chunks(package)` → `_resolve_evidence()` (`api.py:1596-1662`, `:1710-1727`, `:1888-1984`, `:2037+`); `report_evidence()` 404s for any Chunk outside the package (`test_report_api.py:539-601`). `_gaps()` (`:1931-1960`) merges recorded coverage gaps with observed read gaps. `run_detail()` (`:649-745`) exposes `evidence_package.selected_count/coverage` only.

Plan:

- Add one shared merged reader (the same `merged_research_inputs()` used by the Agent) that returns normalized refs for initial + adopted inputs plus tool/processing gaps. `Analysis.research_inputs` is the read source for a finished report, so a report cannot change when the Run ledger or a Document later changes.
- `report_detail()` keeps `evidence_package` as the untouched initial snapshot and adds a separate effective-evidence section with `origin` (`initial`/`research_input`) and `flags` (`published_after_as_of`, `collected_after_as_of`). `_cited_keys()` (`:1917`) must include Fact ids.
- `report_evidence()` must resolve supplemental Chunks held in `analysis.research_inputs` (not only `package.selected_chunks`), and a new Fact route (`GET /api/reports/{report_id}/evidence/facts/{fact_id}`) resolves Fact → Document → `snapshot_key` bytes; `_resolve_evidence()` currently assumes `chunk_id` (`:2037-2130`) and needs a Fact branch.
- `_gaps()` gains ledger/tool gaps (`tool_timeout`, `tool_call_failed`, `tool_result_invalid`, `tool_content_unavailable`, `supplement_processing_failed`, `supplement_processing_unavailable`) so a saved report shows why coverage was incomplete (`docs/validation.md:34`).
- `run_detail()` adds safe summaries of `research_inputs` and `tool_state` (ids, counts, statuses, gaps) — never endpoints, credentials or raw Tool bodies; reuse the existing safe-payload helpers (`_safe_external_payload`, `_safe_error`).

### 9. Retry copy semantics

`retry_run()` (`service.py:369-455`) copies config/prompt/model snapshots, then `_copy_completed_work()` (`:456-566`) copies window/progress, re-creates the package via `_copy_evidence_package()` (`:535-566`), rewrites `select_evidence` outputs and `change_scan_progress["selection"]["evidence_package_id"]` to the new package, and copies only the contiguous succeeded prefix excluding the `research_agent` step. Analysis/Report/ModelCall are never copied.

Minimal change to `_copy_completed_work()`:

- `target_run.research_inputs = deepcopy(source_run.research_inputs or [])` and `target_run.tool_state = deepcopy(source_run.tool_state)` — after `_copy_evidence_package()` returns, remap every initial entry's `evidence_package_id` to `new_package.id` (and, when the package hash is stored on the entry, keep it; the copied package's immutable fields are byte-identical, so the hash still validates).
- Preserve supplemental Document/Chunk/Fact identities, provenance and remaining call budget so retrying a failed final Agent does not silently repeat external calls (AC-3).
- Keep `_copy_evidence_package()` behavior unchanged (design.md's "deep-copy three new fields" is only needed if the ledger lives on the package; with the Run ledger it is a no-op).
- Do not copy `Analysis`, `Report`, or the final Agent result; the retried `research_agent` step re-runs and builds its own artifacts (`_analysis_and_report`), and the new ledger entries are appended to the copied list.

### 10. Concrete implementation order

1. `models.py`: add `TaskRun.research_inputs`, `TaskRun.tool_state`, `Analysis.research_inputs` (+ checks); mirror in `alembic/versions/0001_initial.py`.
2. `execution/config.py`: replace `max_length=0` with the real bound (e.g. `max_length=20`) and keep `validate_tools`; add an `AgentConfig.tools` snapshot helper; `config/tasks.yaml` agents stay `tools: []` until a real tool is configured.
3. `research/service.py`: `evidence_package_snapshot_hash()`, `validate_research_input_versions()`, `durable_research_ref()` (Fact-capable), Fact-tolerant `parse_agent_output`/`validate_analysis_content`/`render_report`.
4. `research/supplement.py`: the four helpers above, using `ingest_document`, `run_stage_now`, `current_document_hits`, `facts_for_documents`, `authorize_tools`, `McpClient`.
5. `execution/service.py`: extend `ExecutionContext`; `_run_evidence_selection()` initializes the ledger; `_run_agent()` calls the supplement loop before the model call and validates against the merged union; `_analysis_and_report()` persists the final ledger; `_validate_research_completion()` gains the durable-union check; `_copy_completed_work()` copies/remaps.
6. `worker.py`: pass `session_factory`, lazy `processing_registry`, `tools_config_path`, tool transport into `ExecutionContext`.
7. `api.py`: merged reader in `report_detail`/`report_evidence`, Fact route, gap/tool summaries, `run_detail` safe summaries.
8. Verify on the test server only (no local pytest/DB/Docker): structural `alembic upgrade head` + `check`, then the focused tests — design and fixture inventory for these already exist in `research/v2-01-test-strategy.md`.

### 11. Pitfalls (verified against code)

1. `expire_on_commit=False` (`db.py:6`): cached `DocumentProcessingState`/`TaskRun`/`EvidencePackage` rows are stale after any independent commit. Re-read with `execution_options(populate_existing=True)` (`runner.py:299-315`, `stages.py:563-590`).
2. `run_stage_now()` returning `None` is not a failure and not a "waiting" state by itself; the reason only exists on the re-read row (`_ready_conditions` at `stages.py:372-395` excludes both `CLASSIFICATION_PENDING` and backoff).
3. A `waiting` outcome rewrites the stage to `pending` + `wait_reason` and consumes no attempt (`apply_stage_outcome`, `stages.py:591-670`); do not convert it into a failed stage or a retry (`retry_stage`/`recover_stage` are Owner paths).
4. A `running` stage with a foreign `owner_token` must never be taken over; `claim_stage` uses `FOR UPDATE SKIP LOCKED` and would skip it anyway.
5. `skipped` depends on `applicability` facts (`DIRECT_SKIP_APPLICABILITY`, `stages.py:71-76`); a skip caused by `DEPENDENCY_SKIPPED` is a gap for the dependent stage, not proof that no Facts exist.
6. `facts` may return "waiting on classification" when the classifier failed or was unavailable; the Run must degrade to "no Facts", never to a fabricated one.
7. `ingest_document()` needs an existing `Source` row (`repository.py:158-160`) and rejects empty `content_type`/`title`/`dedupe_key`, and enforces completeness/fetch_error consistency (`:161-170`). Tool-derived `dedupe_key` must come from a trusted canonical identity (normalized URL), never from model text.
8. Text-only Tool output is stored as text (`content_type` reflects what was actually retrieved); never claim HTML/PDF was fetched (`docs/data.md:20-57`).
9. S3+PostgreSQL is not a distributed transaction: a failed insert after a successful `put_object` leaves an unreachable object that must not be treated as evidence (`database-guidelines.md`).
10. `MCP_PROTOCOL_VERSION` is pinned in `tools/config.py:23`; the wire protocol is a deliberate decision, already covered by `test_tools.py`.
11. `McpClient` budget state is in-memory (`client.py:941-944`, checked at `:1022`, incremented at `:1061`); without `tool_state`, a retry repeats spent calls and violates AC-3.
12. The Agent's allowed-reference key is `(chunk_id, document_id)` throughout (`parse_agent_output`, `validate_analysis_content`); a Fact-only reference has no `chunk_id`, so every consumer of that key (including `_cited_keys` in `api.py:1917`) needs a Fact-aware branch rather than a silent `None` key.
13. `render_report()` (`research/service.py:917-975`) and `Analysis.evidence_refs` reproduce `document_id#chunk_id`; Fact-only citations must render deterministically or saved reports will crash on read.
14. The final research step commits late (`_persist_step(..., commit=False)` at `service.py:864-868`); the ledger append must not assume that transaction is still open — it must be its own committed transaction, because `_run_agent` runs long before `_finish_research_run`.
15. `_ensure_run_still_owned()` is a lock-free pre-check only; the real fences are `_assert_run_ownership` inside `_persist_step` and `_finish_run`/`_finish_research_run`. A Run ended mid-supplement keeps its already-committed Document but must gain no ledger entry.
16. Do not let the model supply `source_id`, Publisher, URL, `dedupe_key`, content type or timestamps; trusted Tool configuration plus the ingest input are the only sources of those.
17. The Tool loop needs its own bound (rounds, `max_calls`, bytes, no-progress stop). One model call currently produces the final answer; a second Tool loop must be a deliberate protocol, not an implicit retry.

## External References

No network research was required (scope: internal). Protocol/version facts come from the repository: MCP Streamable HTTP only with `MCP_PROTOCOL_VERSION` pinned in `backend/src/stocks/tools/config.py:23`; remote tools are config-only per `docs/execution.md` ("Tools 与远程 MCP"); no vendor-specific framework is permitted by the PRD scope section.

## Related Specs

- `docs/execution.md:123-155` (`research_inputs` ownership, window/`as_of` semantics), `:275-295` (Tools/MCP boundary, publisher rule).
- `docs/research.md:110-150` (Repository-first, append-only inputs, bounded termination, output validation without `published_at <= as_of` gating).
- `docs/data.md:20-57` (unified ingest input, discovery vs. original), `:315-340` (untrusted external content, read-only tool grants).
- `docs/validation.md:34,40` (acceptance intent for authorized supplemental research).
- `.trellis/spec/backend/worker-guidelines.md` contracts 1, 5, 7c–7j, 8, 9.
- `.trellis/spec/backend/database-guidelines.md` (structure changes mirrored into `0001_initial.py`; S3/PostgreSQL non-transaction; deterministic idempotency keys).
- `.trellis/spec/backend/api-guidelines.md` (version-pinned evidence reads; never follow the latest version in evidence routes).
- `.trellis/spec/backend/testing-guidelines.md` (test-server-only execution).
- Task docs: `prd.md`, `design.md`, `implement.md`, `research/v2-01-test-strategy.md`.

## Caveats / Not Found

- No tests were run and no code was edited; every claim above is from reading the working tree on 2026-10-22. Local execution of pytest/DB/Docker is forbidden for this repository.
- `docs/execution.md` (append target) and the task's `design.md` (package-hosted `research_inputs`) disagree about where the ledger lives. The plan above follows the domain doc; the main session should update `design.md` before implementation so both agree.
- `backend/src/stocks/tools/` (including `McpClient`) and `backend/tests/test_tools.py` exist in the working tree but are untracked; the main session must confirm before implementation whether that code is committed on the base branch or still pending, because the plan reuses it as a dependency rather than re-creating it.
- `AgentConfig.tools` id validation (`execution/config.py:500-508`) and `config/tools.yaml` deny-by-default are present, but no Agent in `config/tasks.yaml` is authorized yet, and no `tools` section exists in `model_snapshot` — that snapshot format is a proposal here, not an existing contract.
- `research_inputs`, `tool_state`, tool-gap codes, `validate_research_input_versions()`, the Fact report route and the Fact-tolerant schemas do not exist anywhere in the code today; they are the deliverables, not current capabilities.
- `docs/research.md` references `repository.select_evidence` while the code exposes `research.service.select_evidence`; only the code path (`backend/src/stocks/research/service.py`) is authoritative for the integration.
- Extent of the "one model call per Agent step" limit was verified only for `_run_agent`; no alternate Agent loop exists elsewhere in `backend/src/stocks/execution/`.
