# Development quickstart

Requires Docker Compose. An S3-compatible endpoint is needed to collect and persist article snapshots.

1. Copy `.env.example` to `.env`. Change `POSTGRES_PASSWORD` and update
   `DATABASE_URL` to use the same password. Configure `S3_ENDPOINT_URL`, non-empty
   `S3_BUCKET` and a paired `S3_ACCESS_KEY`/`S3_SECRET_KEY` when using snapshot
   storage. Set a unique `OWNER_PASSWORD` and a separate `OWNER_SESSION_SECRET` of at
   least 32 random characters (for example, generated with `openssl rand -hex 32`).
   Set `OWNER_COOKIE_SECURE=true` when the browser reaches the console through HTTPS.
   On the target ARM server, set `TARGET_PLATFORM=linux/arm64`.
2. Start PostgreSQL and initialize a fresh development database with the current
   schema: `docker compose up -d --wait postgres`, then
   `docker compose run --rm api alembic upgrade head` (bootstrap only; no historical
   data upgrade or backward compatibility).
3. Check that the initialized schema matches the current models:
   `docker compose run --rm api alembic check`.
4. Build and start the API, Worker and Web entry point:
   `docker compose up -d --build --wait api worker web`.
5. Verify the Web entry point and its same-origin API health route at
   `http://127.0.0.1:8080/` and `http://127.0.0.1:8080/health`. Change `WEB_PORT` or
   `WEB_BIND_ADDRESS` to select another local entry point. PostgreSQL, the API and the
   Worker publish no host ports; Nginx is the only Compose service with a published
   port.
6. Create an Owner session, then view stored records. The session Cookie is HttpOnly; retain the returned CSRF token for POST requests. The documents route is empty on a fresh database.

```bash
curl -c stocks-cookie.txt -s \
  -H 'Content-Type: application/json' \
  -d "{\"password\":\"$OWNER_PASSWORD\"}" \
  http://127.0.0.1:8080/api/session/login
curl -b stocks-cookie.txt http://127.0.0.1:8080/api/documents
```
7. Read a saved report and the originals it actually used, see [Read a report and its evidence](#read-a-report-and-its-evidence).

`api` and `worker` are separate processes from the same image and configuration. The Web
image builds the React client and serves it from Nginx; Node is not a production
process. Nginx forwards `/api/` and `/health` from the published Web port to the internal
API, so browsers use one origin for the console, session Cookie, CSRF checks, reports
and original downloads. The API reads console state and queues authorized recovery
actions; it never executes a Task Run. The Worker polls PostgreSQL for `queued` Runs and
evaluates due schedules, so a restart of either one does not lose queued work.
PostgreSQL, the API and the Worker have no published ports. The Web port is safe to
publish through the host's HTTPS or private reverse proxy after
`OWNER_COOKIE_SECURE=true` is set.

### Web console development

The Web client lives in `frontend/` and requires Node.js 20.19 or newer. For local Vite
development, publish the API only on a loopback address with a Compose override or use
an SSH tunnel; do not add an API host port to the production `compose.yaml`. With the
API reachable at `127.0.0.1:8000`, install the locked dependencies and start Vite from
the repository root:

```bash
cd frontend
npm ci --ignore-scripts
VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

Open `http://127.0.0.1:5173` and sign in with `OWNER_PASSWORD`. Vite proxies `/api` and
`/health` to the API, so development uses the same same-origin cookie and CSRF behavior
as production. `VITE_API_BASE_URL` must stay empty for this setup.

Run the Web checks from `frontend/` on the isolated test server:

```bash
npm run typecheck
npm run test
npm run build
```

The client renders report bodies and evidence as text, loads original bytes through the
protected API, and keeps the session CSRF token in memory. It stores no cookie, token,
report, evidence, or original content in browser storage. Run IDs, report and document
selection, collection/search filters, and the active Runs tab are held in the URL so a
refresh preserves the current reading context.

The current implementation provides Source/Document/Chunk models, fresh-database initialization, a versioned Document repository, configured RSS/Atom discovery, safe HTTP article acquisition, a manual collection CLI, a unified Document processing backlog with manual parse/index commands, pgvector indexing and semantic/exact search CLI, a document-list API, a generic S3 snapshot helper, an HTTP model client, a configured Task runner, a persistent Task scheduler, and an independent Worker with separate Run and document processing slots. The runner persists an Evidence Package with actual Chunk/Document versions, validates structured findings against that snapshot, and saves an Analysis plus a readable Report; when an Agent is granted tools, the same step performs a bounded, Repository-first supplemental research round before its final call. A deny-by-default Tool catalog (`config/tools.yaml`), a remote MCP Streamable HTTP client with budgets and safe error codes, and the append-only `research_inputs` ledger are implemented. The repository checks `(source_id, dedupe_key)` and SHA-256 before writing storage, reuses an immutable Document version and updates a separate collection state for identical bytes, and creates the next immutable Document version for changed bytes. The read-only report and evidence API is implemented; see the next section. Lightweight classification through a configurable Decision Model, generic Fact storage with on-demand extraction, and structured API field mapping are implemented; reliable change watermarks and the responsive, protected Web console are also implemented.

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
stored as empty successful chunks. JSON (including `+json`), XML (including `+xml`) and PDF
originals are parsed by the same stage since V2-02: JSON values become
`path = value` chunks located by JSON Pointer, XML element text and attributes become
chunks located by element path, and PDF text is chunked per page with its page number and
in-page character ranges. Numbers and units stay as the source wrote them; a PDF without a
text layer, an encrypted or damaged file, or a response beyond the value/character/page
limits fails its parse attempt with a stable error code instead of an empty body. OCR and
PDF table reconstruction remain future work. RSS article fetches accept `application/pdf`
since V2-02, so a feed entry that links to a filing PDF stores those exact bytes and the
same stage parses them. PDF originals are served as opaque downloads, never inline.
The protected Web console exposes this backlog through its Runs page, with stage
filtering and the same confirmation rules as the CLI.
The separate Task trigger, scheduler, Worker and unified processing backlog are
implemented; see [Tasks, scheduling and the Worker](#tasks-scheduling-and-the-worker).

## Lightweight classification

The `classify` stage reads the current successful parse batch under a character budget.
Official TypeSafe Jev returns the finite category, tags and `should_extract`; a required
`classification_entities` generation role extracts company names/tickers and source
quotes from the same input. Configure the real server environment separately:

```dotenv
CLASSIFICATION_API_BASE_URL=https://api.typesafe.ai/v1
CLASSIFICATION_API_KEY=<server-only-real-key>
CLASSIFICATION_MODEL_NAME=jev-latest
```

Jev uses Bearer authentication and native `POST /systemone` with `model`, `state` and
`questions`. It does not expose Chat Completions or generate unknown entity text.
Configure both roles in `config/models.yaml` (the supplied configuration routes entity
extraction to the existing research model and its `MODEL_*` credentials):

```yaml
agents:
  classification:
    protocol: typesafe
    noul_threshold: 0.5
    routing:
      primary: classification
      fallbacks: []
      required_capabilities: [decision]
      timeout_seconds: 30
      retries: 1
  classification_entities:
    protocol: openai_compatible
    routing:
      primary: research
      fallbacks: []
      required_capabilities: [chat, structured_output]
      timeout_seconds: 60
      retries: 1
```

`config/taxonomy.yaml` owns the vocabulary and `version`. Document/event types become
`choice` questions; each multi-label candidate and `should_extract` uses `noul`, a
0..1 truth probability. `noul_threshold` must be explicitly set between zero and one;
answers at or above it are selected. Missing answers, unknown candidates, invalid
probabilities/types/model identities or missing usage fail validation. Entities require
verbatim source quotes and are not restricted to a Watchlist; the generator cannot
change Jev's finite decisions. Both steps must succeed. Entity generation is an
explicit role, never a fallback for a failed Jev request.

`CLASSIFICATION_MAX_CHARACTERS` (default 6000, bounded to 200..40000) caps the current
batch excerpt both steps may read. The merged result is stored in
`documents.classification` on the same Document version, including per-role provider,
requested/actual model identity, usage, attempts, Jev probabilities/threshold and
entity evidence. There is no classification history table; the `classify` row in
`document_processing_states` is the processing status. Missing Jev environment values
fail only classification route loading, leaving unrelated research and embedding
routes usable. Compose passes these variables to API and Worker. Setting them requires
recreating the affected backend containers through the normal deployment procedure;
no real provider call is implied by configuration alone.

An invalid decision or entity result, a timeout, or a missing/broken route is a recorded
failure with a safe error code. It is never stored as `should_extract=false`, and the
previously stored classification, original Document and index stay intact. A failed
entity step preserves the successful Jev call's provenance in the stage error.
`should_extract=false` marks `facts` as `NOT_APPLICABLE`; an explicit reclassification
returning `true` makes that skipped stage runnable again. Run classification through
the normal stage path on the designated test server when verifying this change:

```bash
docker compose run --rm api python -m stocks.processing status --stage classify
docker compose run --rm api python -m stocks.processing reprocess \
  --document-id <document-uuid> --stage classify --execute
```

## Manual company research: from collection to citations

`config/taxonomy.yaml` defines global classification candidates; `config/tasks.yaml`
defines each research question. Edit the former to change label meanings, and the
latter to change the company or subject being researched. Every label requires
`id`, `name` and `description`; `aliases` is an optional array of non-empty strings.
Only ids are valid classification outputs. See the exact validation and fingerprint
contract in [Data design](data.md#自动分类标签与关联边界).

The supplied manual `nvidia_research` Task uses NVIDIA/NVDA, semiconductor/hardware
scope and the existing `evidence_first` / `research_writer` flow. Its first explicit
string query names NVIDIA/NVDA; the other two ask about chip demand and export-control
background without naming the company. The existing selector assigns direct/background
retrieval roles from those query strings and the scoped company/symbols. Scope is
research intent, not an exact label filter, and a query's role does not prove a source
mentions NVIDIA. Neither this Task nor its scope supplies stock candidates to the
classifier. Add another Task to reuse the saved repository without reclassifying it
against an aggregate list of watched companies.

For validation, run the following only on `100.120.14.84` in the current task's isolated
deployment. Configure enabled Sources, storage and model routes first, including a real
contact email where required. The default SEC/Fed feeds may contain no NVIDIA company
material; add an appropriate supported Source if company coverage is required. Missing
coverage must remain visible in the report.

1. Queue `collect_enabled_sources` from the Tasks page, or run
   `docker compose run --rm api python -m stocks.execution trigger --task collect_enabled_sources`.
   The Worker executes the Run. Inspect its per-Source/item result with
   `stocks.execution show --run-id <collection-run-id>` through the same Compose command.
   Collection success alone does not mean classification or indexing is ready.
2. Inspect each resulting Document with
   `docker compose run --rm api python -m stocks.processing status --document-id <document-uuid>`.
   Wait for parse, index and classify completion; inspect facts separately. A successful
   `should_extract=false` classification makes facts `NOT_APPLICABLE`; pending, failed
   or dependency skips do not prove facts were extracted. With an authenticated API
   session, inspect `GET /api/documents/<document-uuid>`: `document.classification`
   contains saved labels/entities, and
   `document.classification.reason_or_evidence_ref.entity_evidence` contains Jev entity
   quotes. These fields are not currently displayed in the Web evidence detail. A
   company without a printed ticker keeps `symbols: []`; pure industry material keeps
   both entity arrays empty.
3. If label meanings changed, explicitly reclassify the selected completed Documents:
   `docker compose run --rm api python -m stocks.processing reprocess --document-id <document-uuid> --stage classify --execute`.
   This records the new semantic fingerprint; editing YAML alone does not rerun them.
4. Queue the manual example with
   `docker compose run --rm api python -m stocks.execution trigger --task nvidia_research`.
   Inspect `stocks.execution show --run-id <research-run-id>` for the Evidence Package,
   selected inputs, query roles, coverage and safe errors. A queued Run still needs
   the Worker. Continued incremental scans are visible in `change_scan_completed`;
   use `stocks.execution cursor --task nvidia_research` to inspect its checkpoint.
5. Open the resulting report in the Reports page, follow a finding's saved citation to
   its Document version and Chunk, then open the stored original. Check that company
   findings cite company evidence and that industry/policy context is described as
   background or inference. Query roles alone cannot establish that distinction.
   Reports must say when company evidence is missing instead of inventing it. Keep the
   collection Run, Document IDs, research Run and report IDs together for traceability.

This procedure describes the implemented flow; it is not evidence that a particular
external source, model or full batch has been exercised. Record controlled fixtures,
real source acquisition and real model calls separately when validating it.

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
curl -s -b stocks-cookie.txt 'http://127.0.0.1:8080/api/facts?source_id=bls_cpi&limit=20'
curl -s -b stocks-cookie.txt "http://127.0.0.1:8080/api/documents/<document-uuid>/facts"
```

Repeating a stage for the same content version does not duplicate Facts: identity is
`(document_id, fact_key)`. A revised value arrives as a new Document version and
therefore a new Fact, so an older report keeps citing the value it actually used.

## Manual embedding indexing and search

Chat and embedding are separate providers, so a chat-only API can be paired with a
different embedding API. Set `MODEL_API_BASE_URL` / `MODEL_API_KEY` / `MODEL_NAME` for the
chat vendor and `EMBEDDING_API_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL_NAME`
for the embedding vendor; `config/models.yaml` points each model at its own provider, and
both must expose the OpenAI-compatible operation with a `/v1` base URL. The embedding
provider must serve `/embeddings`. The current vector space is fixed at 1,024 dimensions,
matching the HNSW cosine index in the fresh database schema. Any configured embedding
fallback must use the same model name and dimensions.

The configured `dimensions` is also sent in the request body, so a provider whose default
vector is larger (for example Ark's `doubao-embedding-text-240715`, which defaults to 2,048)
can return the size this deployment stores. `dimensions` is required and fixed at 1,024, so
it is always sent; it is also what validates the returned vector length. A provider that
rejects the field is not supported without a code change, and a provider that silently
ignores it fails the dimension check rather than storing a mismatched vector.

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
  V1-06 merges this Run's incremental Documents and Facts with historical semantic
  evidence into one set of `evidence_groups`; a Document reached through both paths is
  one chain, and its Facts travel with the readable Chunk the Agent cites. Coverage
  distinguishes `candidates_discovered`, `candidates_screened`, `selected_count`
  (Chunks), `evidence_group_count` (chains), `filtered_out_count` (screened but not
  chosen) and `unexamined_count` (not scanned; `null` when the bounded pool is unknown).
  V2-03 adds `origin_group_count` / `merged_origin_group_count` and the
  `origin_groups[]` / `metric_comparisons[]` detail: Documents that declared the same
  canonical link, article URL or absolute feed id count once as an independent
  original, every copy keeps its own citation and original bytes, a Document whose
  source declared nothing is reported as unknown (`single_document`), and same-metric
  values are listed side by side with `value_conflict` / `basis_mismatch` /
  `period_mismatch` flags instead of being averaged or marked verified. The stored
  Report ends with a deterministic `## Sources and Conflicts` section built from the
  saved Evidence Package, not from model text. Reprint merging is limited to what the
  sources themselves declared; title or vector similarity never merges anything.
  V2-04 adds `GET /api/reports/{report_id}/comparison?other_report_id=...`: it compares
  the two saved snapshots side by side (findings, cited Document versions, same-metric
  values, coverage gaps and Documents published or collected after the earlier `as_of`),
  orders them by `created_at` with `ordering.swapped` telling the caller whether the
  requested report is the earlier side, and never re-runs research or rewrites the older
  report. Self-comparison returns 400, an unknown report 404, and a report whose Run did
  not succeed 409.
  It does not collect, parse,
  or index Documents, and it does not call external Tools unless its Agent declares an
  authorized `tools` list. The Agent input is untrusted JSON data. A research Agent may
  name only ids that are `enabled` in `config/tools.yaml`; an unconfigured, disabled,
  duplicate or out-of-range id rejects the Task configuration before the Run is created,
  and an empty list (the default) keeps the V1 behavior with no Tool configuration read
  and no external call.
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
`model_inference`, and must cite at least one persisted Chunk that is either selected by
this Run's Evidence Package or appended to its `research_inputs` by an authorized
supplement. A supplemental result with only Facts or only a search summary cannot support
a finding; it stays a gap or an open question. Invalid citations retry through the
configured model route and are never saved silently.

`empty_evidence_skip` filters on a deliberately absent source, so its configured
condition remains unmet and the Agent step is persisted as `skipped`.
`empty_evidence_report` uses the same absent source and demonstrates that a Run with no
usable saved evidence still succeeds: the Agent may not invent findings, so the Analysis
carries `open_questions` and the coverage gaps instead. A failed Run keeps its Evidence
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

### Authorized Tools and remote MCP

`config/tools.yaml` is a second, independent catalog: Tasks and Agents grant tool ids,
and this file decides which of those ids actually exist and are enabled. It ships
deny-by-default with `tools: []`, so an Agent that grants an id nobody configured is
rejected by the Task configuration loader instead of running without the tool. Every
entry is a remote MCP Streamable HTTP endpoint (protocol `2025-11-25`); stdio is not
implemented. Configured fields:

```yaml
tools:
  - id: research_search
    enabled: false              # deny-by-default; a granted but disabled tool fails the Run
    endpoint: https://mcp.example.com/mcp   # public HTTPS, no query/fragment
    remote_name: search_documents           # name the MCP tools/list reports (default: id)
    source_id: tool_research_search         # configured Source the retrieved original is registered under
    bearer_token_env: STOCKS_RESEARCH_SEARCH_TOKEN   # environment variable name only
    timeout_seconds: 30
    input_schema: {type: object, additionalProperties: false, required: [query],
                   properties: {query: {type: string, minLength: 1, maxLength: 500}}}
    budgets: {max_calls: 2, max_tokens: 16000, max_request_bytes: 100000, max_result_bytes: 1000000}
    result_mapping: {fields: {title: [result, title], url: [result, url], content: [result, text]}}
```

The Agent grants ids through its own `tools` list in `config/tasks.yaml` (at most 20
unique ids). Resolution is the intersection of the enabled catalog, the Agent grant and
the schema-validated request; the model never sees or chooses endpoints or credentials.
Credentials are read from the environment variable named by `bearer_token_env` only at
call time, so a missing variable fails the call with `TOOL_CREDENTIAL_MISSING` before any
request is sent, and endpoint plaintext, headers and bodies never enter Run snapshots,
logs, ModelCall rows or report output. The Run snapshots the credential-free tool
identity at creation; if the live catalog or an Agent grant changes before execution, the
step fails with `TOOL_AUTHORIZATION_SNAPSHOT_INVALID` instead of silently using the new
configuration.

When an Agent has tools, the research step first makes one bounded Tool-planning call
(≤2 rounds, ≤4 requests per round). A plan only ever performs Repository-first lookups:
the model requests a tool by id with schema-validated arguments, Stocks saves the
returned original through `stocks.repository.ingest_document()`, runs only the
parse/classify/facts stages that source needs, and appends what it adopted to the Run's
append-only `research_inputs`. Duplicate queries, exhausted budgets, timeouts, transport
failures, invalid results and results without citable original content stop or skip the
round and are recorded as explicit `tool_*` / `supplement_*` gaps; the Run still finishes
with the evidence it has. There is no CLI or API to list tool calls today; inspect the
catalog as a config file and use `tests/test_tools.py` (unit tests for authorization,
wire sequence, budgets, SSE, redirect/DNS safety, redaction) and
`tests/test_research_supplement.py` (bounded supplement loop and ledger) as the
verification entry points. Both must run on the test server; a real provider integration
is unverified until a real endpoint and credentials are configured.

## Read a report and its evidence

The report and evidence endpoints are read-only and reuse the rows the runner already
saved; they never collect, parse, index, or call a model, and they never follow a
`document_collection_states` pointer to the newest version. Every response describes the
Document and Chunk version the report actually cited, with the current collection pointer
reported separately so a newer version stays visible without rewriting an older report.

```bash
curl -s -b stocks-cookie.txt http://127.0.0.1:8080/api/reports | jq '.items[0]'
curl -s -b stocks-cookie.txt "http://127.0.0.1:8080/api/reports?task_id=saved_evidence_brief" | jq '.items[].report_id'
curl -s -b stocks-cookie.txt http://127.0.0.1:8080/api/reports/<report-id> | jq '{title: .report.title, content: .report.content, gaps: .coverage_gaps}'
curl -s -b stocks-cookie.txt http://127.0.0.1:8080/api/reports/<report-id> | jq '.evidence[] | {chunk_id, document_id, document_version, text, original: .original.endpoint}'
curl -s -b stocks-cookie.txt http://127.0.0.1:8080/api/reports/<report-id>/evidence/<chunk-id> | jq '.evidence'
curl -s -b stocks-cookie.txt http://127.0.0.1:8080/api/documents/<document-id> | jq '{document: .document.title, versions: [.versions[].version]}'
curl -s -b stocks-cookie.txt http://127.0.0.1:8080/api/documents/<document-id>/original | head
curl -sOJ -b stocks-cookie.txt "http://127.0.0.1:8080/api/documents/<document-id>/original?download=true"
```

`/api/reports/{report_id}` returns the report body, the Run snapshot (status, objective,
scope, model routing, safe error), the saved Analysis (summary, findings, open questions,
evidence refs), the Evidence Package (recorded queries, budget, selection policy version,
coverage counters and its recorded `coverage`), each selected Chunk with its text and
locator, the ids the findings actually cite, the Run's `research_inputs`
(`{count, entries, gaps}`: how many inputs the Run adopted, one entry per adopted
supplemental Document version with its Tool identity and time relations, and the
recorded `tool_*` / `supplement_*` gap codes; the stored ledger also begins with the
`selection_snapshot` boundary entry that pins the Evidence Package hash), and the known
gaps. Supplemental entries carry the time labels
(`published_at` / `source_updated_at` / `collected_at` / `available_at` / observation
period) and are flagged when the material was published or collected after `as_of`; the
original bytes of a supplemental Document are read through the same
`/api/documents/{document_id}/original` endpoint as any other version. The known gaps
combine the codes Evidence Selection recorded, the `tool_*` / `supplement_*` codes the
Run recorded while supplementing, and what reading the evidence shows now, so "material
was missing" is never rewritten as "the fact does not exist". There is no redirect or
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

The report response also carries `usage`: the deterministic summary of that Run's stored
`model_calls` (`call_count`, `succeeded_calls`, `failed_calls`, `fallback_calls`, token
totals, `duration_ms`, `cost_usd`, `cost_known_calls`, `cost_unknown_calls` and a bounded
per-model breakdown). `cost_usd` only sums amounts the provider itself returned in
`usage.cost` / `usage.cost_usd`; an unreported cost stays `null`, is counted as unknown and
is never priced from a local table or shown as `0`. `/api/runs/{run_id}` returns the same
`usage` summary next to its `steps` and `model_calls`, where each call now names the
`provider` that served it and its own `cost_usd`. `/api/overview` adds a `research`
section: `latest` is the coverage of the newest successful report's Evidence Package
(candidate discovery/screening/selection counts, evidence and independent origin group
counts, `unexamined_count`, `retrieval_truncated`, adopted `research_input_count` and the
recorded gaps) and stays `null` until a report exists, while `usage` aggregates the stored
model calls of the recent Runs with their `run_count`. Unknown candidate counts and
unknown costs are returned as `null`, never as `0`.

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

Every private API route, including report evidence and original downloads, requires the
Owner's signed session Cookie. Login is public and the health check remains public;
mutating requests require the `X-CSRF-Token` returned by login or `/api/session`. The
session expires according to `OWNER_SESSION_TTL_SECONDS`, and a missing Owner password or
signing secret makes private routes fail closed with `OWNER_AUTH_UNAVAILABLE`. Use HTTPS
and `OWNER_COOKIE_SECURE=true` for cross-device access. Tailscale or equivalent private
network routing to S3 must be independently configured.
