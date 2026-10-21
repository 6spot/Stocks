# Development quickstart

Requires Docker Compose. An S3-compatible endpoint is needed to collect and persist article snapshots.

1. Copy .env.example to .env. Change POSTGRES_PASSWORD and update DATABASE_URL to use the same password. Configure S3_ENDPOINT_URL, non-empty S3_BUCKET and a paired S3_ACCESS_KEY/S3_SECRET_KEY when using snapshot storage.
2. Run: `docker compose up -d --build postgres api worker`
3. Initialize a fresh development database with the current schema: `docker compose run --rm api alembic upgrade head` (bootstrap only; no historical data upgrade or backward compatibility).
4. Check that the initialized schema matches the current models: `docker compose run --rm api alembic check`
5. Verify: http://127.0.0.1:8000/health
6. View stored records: http://127.0.0.1:8000/api/documents (empty on a fresh database).
7. Read a saved report and the originals it actually used, see [Read a report and its evidence](#read-a-report-and-its-evidence).

`api` and `worker` are separate processes from the same image and configuration. The API only reads and never executes a Task Run; the Worker polls PostgreSQL for `queued` Runs and evaluates due schedules, so a restart of either one does not lose queued work. The Worker has no published port.

The current implementation provides Source/Document/Chunk models, fresh-database initialization, a versioned Document repository, configured RSS/Atom discovery, safe HTTP article acquisition, a manual collection CLI, a unified Document processing backlog with manual parse/index commands, pgvector indexing and semantic/exact search CLI, a document-list API, a generic S3 snapshot helper, an HTTP model client, a configured Task runner, a persistent Task scheduler, and an independent Worker with separate Run and document processing slots. The runner persists an Evidence Package with actual Chunk/Document versions, validates structured findings against that snapshot, and saves an Analysis plus a readable Report. The repository checks `(source_id, dedupe_key)` and SHA-256 before writing storage, reuses an immutable Document version and updates a separate collection state for identical bytes, and creates the next immutable Document version for changed bytes. The read-only report and evidence API is implemented; see the next section. Lightweight classification through a configurable Decision Model, generic Fact storage with on-demand extraction, and structured API field mapping are implemented; reliable change watermarks and the Web console remain to be implemented.

The snapshot helper stores the exact bytes supplied by its caller, computes their SHA-256 hash, and uses the supplied content type; it does not assume XML or a particular collection channel. `stocks.repository.ingest_document()` remains the only Document write entry point.

## Manual source collection

The initial `config/sources.yaml` configures the official SEC Press Releases RSS source. Before requesting SEC content, replace `owner@example.com` in `user_agent` with the Owner's real contact email; SEC rejects an automated client that does not declare contact information. Review the other source identity and request bounds at the same time. From the repository root, with PostgreSQL, S3, and `.env` available:

```bash
docker compose run --rm api python -m stocks.collection --config config/sources.yaml --source sec_press_releases
```

The command prints one JSON summary with discovered, ingested, reused, skipped, and failed counts plus per-item status. It processes at most the configured `max_items` feed entries per run, with no watchlist or investment-relevance filter. A successful HTTP response is accepted only for RSS/Atom feed types and HTML, XHTML, or plain-text article pages. Feed and article requests require HTTPS, reject non-default ports, URL credentials, and non-public DNS answers, revalidate every redirect, and enforce total timeout, redirect count, compressed and decompressed response-size limits. Article responses are allowed by their media type, while the stored content type keeps the declared `charset` so the later parser can strictly decode the original bytes. A failed or empty article response still registers the acquired Feed title and summary as `partial`; it never registers the feed itself as article evidence, and every later run retries the article even when the source gives no time signal. A feed entry whose article link cannot be parsed is recorded as that entry's own failure and the remaining entries continue. Items whose stored content is complete are skipped without an article request unless a changed `updated` or `pubDate` signal triggers a recheck, and the article byte hash determines whether a new version is created.

The standalone `stocks.collection` CLI is still available for a one-off manual collection. It does not create Task/Run records, a collection cursor, chunks, embeddings, classifications, or Facts; a scheduled collection Task does create a Run record. Neither includes a browser renderer for JavaScript-only article pages. A real SEC run still requires configured PostgreSQL and S3 services; the unit tests use fixed HTTP responses and injected storage and do not contact SEC or S3.

## Document processing backlog, manual parsing and indexing

Documents enter a unified background-processing backlog automatically: the
`document_processing_states` rows for `parse`, `classify`, `facts` and `index` are
created in the same transaction as the Document content version, and the Worker's
independent document slots claim them. `WORKER_MAX_CONCURRENT_DOCUMENTS` (default `1`,
bounded to 1..16) caps how many stages run at once; it is separate from
`WORKER_MAX_CONCURRENT_RUNS`, so a parse/index backlog never starves collection or
research and a long research Run never blocks processing.

Query the backlog and this deployment's capability:

```bash
docker compose run --rm api python -m stocks.processing status --limit 20
docker compose run --rm api python -m stocks.processing status \
  --stage index --status failed
docker compose run --rm api python -m stocks.processing status \
  --document-id <document-uuid>
```

`status` prints one JSON object with the stage rows (document, stage, status, attempts,
wait/skip reason, safe error, processor version, input batch) plus counts, the
`available_stages` this deployment can run, and the `unavailable_stages`. A stage whose
processor is not registered is reported as `pending` with
`wait_reason: PROCESSOR_NOT_AVAILABLE` and `processor_available: false`; it is never
claimed, never consumes an attempt and is never reported as completed or skipped. This
release registers `parse`, `classify`, `facts` and `index`.

Recover or explicitly reprocess work:

```bash
# Reopen failed/unfinished stages for the Worker (does not execute them here).
docker compose run --rm api python -m stocks.processing retry \
  --document-id <document-uuid> --stage parse --stage index

# Release a stage whose owning process was killed, then run it now.
docker compose run --rm api python -m stocks.processing retry \
  --document-id <document-uuid> --stage index --confirm-stopped --execute

# Explicitly run a completed stage again (for example a new embedding model).
docker compose run --rm api python -m stocks.processing reprocess \
  --document-id <document-uuid> --stage index --execute
```

`retry` refuses a `completed` stage and requires `--confirm-stopped` to release a
`running` one; the Worker never takes over a stage another process may still own.
`reprocess` reopens a completed stage. Both reopen through the same claim path, so a
manual command cannot bypass stage ownership or write a result outside the stage
transaction.

A manual parse of up to 20 actionable Documents (or explicit IDs) is still available and
now runs through the same parse stage. The default chunk budget is 1,200 decoded
characters with 150 characters of overlap; configure `PARSE_CHUNK_SIZE` and
`PARSE_CHUNK_OVERLAP`, where overlap must be smaller than size:

```bash
docker compose run --rm api python -m stocks.processing parse --limit 20
docker compose run --rm api python -m stocks.processing parse \
  --document-id <document-uuid> --include-text
docker compose run --rm api python -m stocks.processing parse \
  --document-id <document-uuid> --reprocess
```

Supported inputs are HTML, XHTML, and plain text with a declared or UTF-8 compatible
encoding. HTML parsing prefers `article` or `main`, removes navigation, script, form and
similar page noise, preserves headings, paragraphs, lists, preformatted text and
table rows, and records source character ranges plus element paths in each persistent
chunk. Plain text retains paragraph character ranges. HTML entities and nested inline
content retain a source range for each visible character; table separator positions are
marked separately from source characters. Each Chunk belongs to an immutable
parse batch; the document parse state distinguishes the latest attempt from the current
successful batch.

An ordinary repeated run reuses the result for the same Document, processor version and
configuration; a previous failed or `unsupported` attempt is retried for real instead of
being returned again. `--reprocess` creates a new batch. If that attempt fails, the current
successful batch and its Chunks remain readable and unchanged; a later success becomes
the current batch without deleting history. Unsupported formats and documents with no
extractable evidence text are recorded as `unsupported` or `failed`; they are never
stored as empty successful chunks. PDF/JSON/XML parsing and OCR remain future work, and the
Web console for reading the backlog is not implemented yet.
The separate Task trigger, scheduler, Worker and unified processing backlog are
implemented; see [Tasks, scheduling and the Worker](#tasks-scheduling-and-the-worker).

## Lightweight classification

The `classify` stage reads the current successful parse batch under a character budget
and makes one Decision Model call that returns the controlled category, tags and
`should_extract`. Configure the route in `config/models.yaml`:

```yaml
agents:
  classification:
    protocol: openai_compatible   # the only adapter implemented today
    routing:
      primary: fast
      fallbacks: []
      required_capabilities: [chat, structured_output]
      timeout_seconds: 30
      retries: 1
```

The vocabulary, its `version` and the derived output schema live in
`config/taxonomy.yaml`; a category, topic, industry or region outside that file is
rejected as a contract violation. `CLASSIFICATION_MAX_CHARACTERS` (default 6000, bounded
to 200..40000) caps how much of the current batch one call may read. The result is stored
in `documents.classification` on the same Document version — there is no classification
history table, and the `classify` row in `document_processing_states` is the processing
status.

A Decision Model answer outside the vocabulary, an invalid structure, a timeout, or a
missing/broken route is a recorded failure with a safe error code. It is never stored as
`should_extract=false`, and the previously stored classification, the original Document
and its index stay intact. `should_extract=false` marks the `facts` stage `NOT_APPLICABLE`;
an explicit reclassification that returns `true` makes that skipped stage runnable again.
Run a classification through the normal stage path:

```bash
docker compose run --rm api python -m stocks.processing status --stage classify
docker compose run --rm api python -m stocks.processing reprocess \
  --document-id <document-uuid> --stage classify --execute
```

## Facts and structured API sources

`facts` stores reusable structured statements from either origin: text a
Generation Model extracted, or values mapped directly from a raw API response. Both
live in one `facts` table; `origin_method` says which one produced a row and
type-specific detail goes into `attributes`. Every Fact cites the Document version it
came from plus either a `chunk_id` (text) or a JSON `pointer` (mapped), so a reader can
always resolve it back to stored bytes.

Configure the generation route for text extraction in `config/models.yaml`:

```yaml
agents:
  fact_extraction:
    protocol: openai_compatible
    routing:
      primary: research
      fallbacks: []
      required_capabilities: [chat, structured_output]
      timeout_seconds: 60
      retries: 1
```

Text extraction runs only when the stored classification says `should_extract: true`;
`false` marks the stage `NOT_APPLICABLE`, and an undecided verdict leaves it waiting as
`CLASSIFICATION_PENDING` without consuming an attempt. `FACT_EXTRACTION_MAX_CHARACTERS`
(default 12000, bounded to 200..80000) caps how much of the current parse batch one
call may read.

A structured API source declares a mapper instead of a feed, in `config/sources.yaml`:

```yaml
sources:
  - source_id: bls_cpi
    name: BLS CPI
    connector: http_api
    publisher: U.S. Bureau of Labor Statistics
    trust_tier: T1
    api_url: https://api.example.gov/cpi
    query: {series: CPIAUCSL}
    timeout_seconds: 30
    max_response_bytes: 1000000
    max_redirects: 3
    max_items: 10
    user_agent: StocksIntelligence/0.1 (owner@example.com)
    mapper:
      id: cpi-series
      fields:
        - kind: numeric
          pointer: /series/observations/0/value
          fact_type: macro_indicator
          metric_id: cpi_yoy
          unit: percent
          period_start_pointer: /series/observations/0/period
          available_at_pointer: /released_at
```

Collection makes one bounded request and stores the raw response as a Document
version; mapping happens in the `facts` stage, so a numeric payload needs neither a
text classifier nor a vector. A `numeric` field must declare its `unit` — a value whose
unit is unknown is never stored as if it were comparable. A field that is absent from
the response simply produces no Fact, and a response matching none of the configured
fields fails with `MAPPING_NO_FIELDS_MATCHED` instead of an empty success.

Run and read the stage:

```bash
docker compose run --rm api python -m stocks.processing facts --limit 20
docker compose run --rm api python -m stocks.processing facts \
  --document-id <document-uuid> --sources-config config/sources.yaml
docker compose run --rm api python -m stocks.processing reprocess \
  --document-id <document-uuid> --stage facts --execute

# Read-only views: these never start extraction.
docker compose run --rm api python -m stocks.processing facts --source-id bls_cpi
curl -s 'http://127.0.0.1:8000/api/facts?source_id=bls_cpi&limit=20'
curl -s "http://127.0.0.1:8000/api/documents/<document-uuid>/facts"
```

Repeating a stage for the same content version does not duplicate Facts: identity is
`(document_id, fact_key)`. A revised value arrives as a new Document version and
therefore a new Fact, so an older report keeps citing the value it actually used.

## Manual embedding indexing and search

Configure `EMBEDDING_MODEL_NAME` alongside the existing model provider variables. The
selected provider must expose the OpenAI-compatible `/embeddings` operation. The current
vector space is fixed at 1,536 dimensions, matching the HNSW cosine index in the fresh
database schema. Any configured embedding fallback must use the same model name and
dimensions.

After parsing, index up to 20 actionable Documents or select explicit IDs:

```bash
docker compose run --rm api python -m stocks.indexing --limit 20
docker compose run --rm api python -m stocks.indexing \
  --document-id <document-uuid>
docker compose run --rm api python -m stocks.indexing \
  --document-id <document-uuid> --reindex
```

Both commands call the unified stage service. An unchanged current batch reuses its
vectors, and `--reindex` regenerates them without reading the original S3 snapshot or
parsing again. Only an eligible index stage reads `config/models.yaml`; a missing or
broken embedding route fails that one attempt and leaves the original Document and its
Chunks intact, and it never stops parsing or a collection Run. `/api/documents` shows the
index state, including indexed and missing Chunk counts.

Semantic search generates one query vector and reads the existing pgvector index.
Exact search accepts short stock-code or metric identifiers and does not call a model:

```bash
docker compose run --rm api python -m stocks.search \
  --query "recent inflation and policy changes" --source-id sec_press_releases
docker compose run --rm api python -m stocks.search \
  --exact NVDA --published-from 2026-10-01T00:00:00Z
```

Use `--chunk-id <chunk-uuid> --radius 1` to read the current Chunk and its stored
neighbors. Context reads only the current successful parse batch; it does not fetch or
parse the S3 original.

The output includes persistent Chunk and Document references, version and source
metadata, the applied filters, and whether the result hit the requested limit. Semantic
search also reports the current Chunk index coverage; exact search is vector-independent
and therefore reports `coverage: null`. An embedding failure leaves the original
Document and Chunks intact and reports a visible gap. The CLI does not read S3, parse
Documents, or create document vectors while searching.

All tests and runtime verification must run on the designated test server
`100.120.14.84` in the isolated directory for the current task. The Owner's machine is
only for editing and packaging code: do not run pytest, PostgreSQL/pgvector, Alembic,
Docker, API smoke tests, external integrations, or deployment checks there, even when
equivalent tools happen to be installed. Run only checks relevant to the change on the
test server, for example `.venv/bin/python -m pytest -q tests/test_s3.py`; use `-k` when
needed. Follow AGENTS.md and the backend testing specification for environment reuse,
container cleanup, and when broader checks are warranted. If the test server is not
reachable, report the checks as unverified and do not fall back to the local machine.

## Tasks, scheduling and the Worker

`config/tasks.yaml` defines Tasks and Agents, `config/models.yaml` defines the model
routing, and `config/sources.yaml` defines Sources. A Task is either:

* `type: research` — exactly one `select_evidence` step followed by a final
  `research_agent` step. It reads one bounded page of committed change records from the
  Run's fixed `(start,end]` window, records the raw continuation in
  `TaskRun.change_scan_progress`, persists the selected Evidence Package, validates every
  finding against the recorded Document/Chunk versions, and saves an Analysis plus a
  readable Report. `change_page_limit` (default `100`, range `1..1000`) bounds each Run's
  raw change page. A normal later Run resumes the newest valid unfinished checkpoint for
  the same Task and Scope; a retry copies its source Run's original window, display times,
  checkpoint and Evidence Package. The raw continuation executes the frozen
  `query_snapshot` (`source_ids`, `page_limit`, and related scan conditions), so changing
  the live Task query cannot mutate an in-progress window; semantic background queries
  can still use current settings. The final Agent Step, Analysis, Report, successful
  cursor advance and Run `succeeded` state commit together. An incomplete raw window can
  still produce a successful report, but `change_scan_completed` stays `false`, the
  checkpoint remains for a later Run, and the cursor does not move; a skipped final Agent,
  missing/invalid report, unfinished scan or ownership loss cannot advance the cursor.
  This scans changes and exposes their selected/scanned state; it does not yet merge
  incremental Document/Fact evidence with historical evidence under V1-06. It does not
  collect, parse, index, or call external Tools; the Agent input is untrusted JSON data
  and the configured `tools` list must be empty.
* `type: collection` — a single `collect_sources` step plus `sources: [id, ...]`. It
  reuses `stocks.collection.service.collect_source`, `config/sources.yaml` and the
  existing Repository idempotency, and never resolves a chat or embedding route, so a
  broken research model configuration cannot stop collection. Per-article fetch or
  ingest failures are counted in the step output and retried by the next scheduled run;
  a Source that cannot be collected at all makes the Run fail with
  `COLLECTION_SOURCE_FAILED` while still reporting every Source summary in the output.

A Task `trigger` is either `manual` or a schedule:

```yaml
trigger: {kind: schedule, every: 15m}                                  # fixed interval
trigger: {kind: schedule, cron: "0 8,20 * * *", timezone: America/New_York}
```

`every` accepts `s`/`m`/`h`/`d` durations between 60s and 30d and keeps a fixed anchor,
so a Task does not drift by its own Run duration. `cron` requires a known IANA
`timezone`; a wall-clock time skipped by a daylight-saving jump does not fire, and a
repeated wall-clock time fires once at its first occurrence. The two forms are mutually
exclusive. There is no schedule on a Source — the collection Task owns the cadence.

### Enqueue, inspect, end and retry

Triggering only queues a Run; a Worker executes it. Every command prints one JSON object
and keeps the same ownership rules:

```bash
# Queue a Run for one Task (manual trigger). Repeated triggering returns the
# existing queued/running Run and does not resolve model credentials.
docker compose run --rm api python -m stocks.execution trigger --task saved_evidence_brief

# Inspect Runs; show one Run with its ordered steps, safe errors and model calls.
docker compose run --rm api python -m stocks.execution runs --task saved_evidence_brief
docker compose run --rm api python -m stocks.execution runs --status failed --limit 5
docker compose run --rm api python -m stocks.execution show --run-id <run-id>

# End a Run whose process has stopped, then retry it.
docker compose run --rm api python -m stocks.execution end \
  --run-id <run-id> --confirm-stopped
docker compose run --rm api python -m stocks.execution retry --run-id <run-id>
```

`trigger` exits `0` when the Run is queued or an existing active Run is returned, and
`2` on configuration or database failures with the safe error on stderr. `end` refuses
to run without `--confirm-stopped`: the Owner has to confirm that the old process is
really gone, because the Worker never takes over or reaps a `running` Run on its own.
Ending sets `status: cancelled` with `error.code: RUN_ENDED_BY_OWNER`, clears the
execution owner, and keeps every stored StepResult, Evidence Package and error. `retry`
is allowed only for a `failed` or `cancelled` Run; it creates a new Run that copies the
ended Run's configuration, model routing, prompt snapshot, original change window,
window display times and raw scan checkpoint plus the succeeded steps before the failure
point, re-creates an Evidence Package owned by the new Run, and records
`retry_of_run_id`. It does not bind a fresh endpoint. The final `research_agent` step is
always re-run, so the retry writes its own Analysis and Report. The original Run, its
failed step, ModelCalls, Analysis/Report and error stay intact, so a retry never pretends
the earlier attempt did not happen.

Ending and retrying both respect the per-Task single active Run index: a retry is
rejected while the Task still has a queued or running Run.

### Worker

The Compose `worker` service runs the Worker continuously from the same image:

```bash
docker compose up -d worker
docker compose logs -f worker
```

It performs one scheduler round and then claims queued Runs. A scheduler round
registers newly configured scheduled Tasks with `next_due_at` in the future, fires at
most one Run per due Task, and advances `next_due_at` in the same Task-serialized
transaction as the enqueue. A Task that already has an active Run skips its due instant
without reading model configuration or credentials and still advances; a Worker that was
down for several intervals collapses them into one Run and advances to the next future
instant; disabled, removed or switched-to-manual Tasks lose their persisted schedule.

Claiming uses `FOR UPDATE SKIP LOCKED`, so several Workers pick different Runs instead
of duplicating one. Within one Worker, `WORKER_MAX_CONCURRENT_RUNS` (default `2`, bounded
to 1..16) caps how many Runs execute at the same time. The execution slots are persistent:
a slot that just executed a Run claims the next one immediately instead of waiting for the
slowest Run or for the polling interval, while a slot that finds an empty queue waits for
`--poll-seconds` (or until the scheduler queues a Run) rather than re-polling PostgreSQL
continuously, and repeated unexpected failures back off exponentially (bounded at the poll
interval plus 60 seconds). One long research call therefore cannot hold a due collection
Run in the queue, and idle scheduling costs one claim per interval. Set the limit to `1`
to keep collection and research strictly serialized. Each Run uses its own database
Session, and a failure in one Task is recorded on that Run without stopping the round.

The same Worker also keeps `WORKER_MAX_CONCURRENT_DOCUMENTS` (default `1`, bounded to
1..16) document processing slots. Each slot claims at most one ready Document stage at a
time and immediately looks for the next one, using the same bounded wait and backoff as a
Run slot. Run slots, document slots and the scheduler round each get their own thread in
the Worker's thread pool, so a saturated Run queue or a parse backlog cannot starve the
other, and an idle queue is not polled in a hot loop. A failed stage is recorded on that
Document and never terminates the slot. Only registered stages are claimed, so
`classify`/`facts` staying unregistered costs no queries beyond the capability check.

For a single cycle (tests, smoke checks):

```bash
docker compose run --rm worker python -m stocks.worker --once
docker compose run --rm worker python -m stocks.worker --rounds 1 --poll-seconds 1
```

`--once` prints the scheduler outcomes, the executed Run summary and a `processing`
summary for at most one Document stage. `--max-concurrent-documents` overrides
`WORKER_MAX_CONCURRENT_DOCUMENTS` for one invocation. The exit code is `1` when that
cycle failed: a Run ended unsuccessfully, a scheduling outcome failed, or the Document
stage it attempted ended `failed`. A failed Document stage is still recorded on its own
Document and never stops the slot, but the exit code has to make it observable so a
deployment or acceptance check can tell an empty backlog from a real failure. A
continuously running Worker evaluates the due schedules once per `--poll-seconds`
independently of its execution slots, and a stopping slot still takes one last claim,
so `--rounds N` also executes the Run its final round queued. A Worker that is killed
mid-Run leaves the Run `running` with its last persisted steps and errors, and a killed
document stage stays `running` with its already committed parse batches or vector
batches; there is no automatic crash detection or takeover.
Confirm the process stopped, then `end`/`retry` the Run and
`stocks.processing retry --confirm-stopped` the stage.

The research `select_evidence` step may set up to eight `queries`, a `limit_per_query`, a
`max_tokens` budget, `change_page_limit`, and up to 20 unique `source_ids`. Its raw change
query applies those filters before `LIMIT` and stores the last raw
`(watermark, change_id)`, not a grouped candidate watermark, so filtered pages and repeated
Document changes cannot skip records as the window continues. With no explicit `queries`,
the runner derives direct company/symbol and separate background topic queries from
`run.scope`; it does not require every scope dimension in one AND filter. Use
`stocks.execution show --run-id <run-id>` to inspect the fixed window and
`change_scan_completed`; use `stocks.execution cursor --task <task-id>` to inspect the
committed per-Task/Scope watermark without changing it.
`candidates_discovered` and `unexamined_count` stay `null` when bounded queries cannot
determine them. Every finding is marked `fact`, `source_opinion`, or
`model_inference`, and must cite at least one selected Chunk. Invalid citations retry
through the configured model route and are never saved silently.

`empty_evidence_skip` filters on a deliberately absent source, so its configured
condition remains unmet and the Agent step is persisted as `skipped`.
`unavailable_evidence_failure` uses the same absent source and demonstrates a persisted
failed Run when the Agent has no usable saved evidence. A failed Run keeps its Evidence
Package, never overwrites an earlier successful Run, and never replaces an earlier
Analysis or Report.

Research and Evidence Selection embedding calls use their configured routes in
`config/models.yaml`, with request timeouts and safe error classes; the research route
adds bounded retries and strict JSON-Schema output. Rate limits, service/network
failures, timeouts, schema-invalid responses, and references outside the selected
evidence may retry or fall back on the research route; each candidate uses its own
configured request timeout. Authentication, invalid requests, and missing model
capabilities stop before another target is called.
Run snapshots and ModelCall rows omit API keys, Authorization headers, endpoint
plaintext, and model response bodies.

## Read a report and its evidence

The report and evidence endpoints are read-only and reuse the rows the runner already
saved; they never collect, parse, index, or call a model, and they never follow a
`document_collection_states` pointer to the newest version. Every response describes the
Document and Chunk version the report actually cited, with the current collection pointer
reported separately so a newer version stays visible without rewriting an older report.

```bash
curl -s http://127.0.0.1:8000/api/reports | jq '.[0]'
curl -s "http://127.0.0.1:8000/api/reports?task_id=saved_evidence_brief" | jq '.[].report_id'
curl -s http://127.0.0.1:8000/api/reports/<report-id> | jq '{title: .report.title, content: .report.content, gaps: .coverage_gaps}'
curl -s http://127.0.0.1:8000/api/reports/<report-id> | jq '.evidence[] | {chunk_id, document_id, document_version, text, original: .original.endpoint}'
curl -s http://127.0.0.1:8000/api/reports/<report-id>/evidence/<chunk-id> | jq '.evidence'
curl -s http://127.0.0.1:8000/api/documents/<document-id> | jq '{document: .document.title, versions: [.versions[].version]}'
curl -s http://127.0.0.1:8000/api/documents/<document-id>/original | head
curl -sOJ "http://127.0.0.1:8000/api/documents/<document-id>/original?download=true"
```

`/api/reports/{report_id}` returns the report body, the Run snapshot (status, objective,
scope, model routing, safe error), the saved Analysis (summary, findings, open questions,
evidence refs), the Evidence Package (recorded queries, budget, selection policy version,
coverage counters and its recorded `coverage`), each selected Chunk with its text and
locator, the ids the findings actually cite, and the known gaps. There is no redirect or
aliasing to "the current version": every entry names the Document and Chunk version that
report used. An unknown report, unknown document, or a chunk outside that package returns
`404`; a broken Analysis/Report/Evidence Package chain returns `409`. A saved reference
whose Document or Chunk row is gone is not turned into a `404` or an empty body: it stays
`200` with `chunk_available: false` and the matching gap. `coverage_gaps` keeps the reasons
Evidence Selection recorded and adds what reading the evidence shows now
(`chunk_unavailable`, `document_not_stored`, `partial_source_content`,
`snapshot_storage_unconfigured`, `chunk_not_in_current_parse_batch`,
`chunk_not_in_current_index`, `run_not_succeeded`); missing material is reported as a gap,
never rewritten as "the fact does not exist".

`/api/documents/{document_id}/original` returns the stored bytes for that one version
unchanged. `X-Content-Sha256` is the hash of the bytes actually returned, and
`X-Content-Hash-Match` reports whether it still equals the Document's recorded
`content_hash`; a mismatch is served as-is with the header set to `false` and a warning
logged, because rewriting stored evidence to hide a mismatch would destroy the audit trail.
HTML and XHTML are served as `text/plain`,
anything else as `application/octet-stream`, both with `X-Content-Type-Options: nosniff`
and `Content-Security-Policy: default-src 'none'`, so a collected page cannot run scripts
in the reader's browser. `?download=true` adds an attachment `Content-Disposition`.
Without configured S3 credentials the endpoint answers `503`
(`SNAPSHOT_STORAGE_UNAVAILABLE`) instead of pretending the original is empty, and the
report keeps showing the stored chunk text and hash.

This early API has no application-level authentication. The sample binds it only to 127.0.0.1; do not expose it publicly until access control is added. Tailscale or equivalent private network routing to S3 must be independently configured.
