# Development quickstart

Requires Docker Compose. An S3-compatible endpoint is needed to collect and persist article snapshots.

1. Copy .env.example to .env. Change POSTGRES_PASSWORD and update DATABASE_URL to use the same password. Configure S3_ENDPOINT_URL, non-empty S3_BUCKET and a paired S3_ACCESS_KEY/S3_SECRET_KEY when using snapshot storage.
2. Run: `docker compose up -d --build postgres api`
3. Initialize a fresh development database with the current schema: `docker compose run --rm api alembic upgrade head` (bootstrap only; no historical data upgrade or backward compatibility).
4. Check that the initialized schema matches the current models: `docker compose run --rm api alembic check`
5. Verify: http://127.0.0.1:8000/health
6. View stored records: http://127.0.0.1:8000/api/documents (empty on a fresh database).
7. Read a saved report and the originals it actually used, see [Read a report and its evidence](#read-a-report-and-its-evidence).

The current implementation provides Source/Document/Chunk models, fresh-database initialization, a versioned Document repository, configured RSS/Atom discovery, safe HTTP article acquisition, a manual collection CLI, a manual HTML/text parsing CLI, manual pgvector indexing and semantic/exact search CLI, a document-list API, a generic S3 snapshot helper, an HTTP model client, and a manual configured research Task runner. The runner persists an Evidence Package with actual Chunk/Document versions, validates structured findings against that snapshot, and saves an Analysis plus a readable Report. The repository checks `(source_id, dedupe_key)` and SHA-256 before writing storage, reuses an immutable Document version and updates a separate collection state for identical bytes, and creates the next immutable Document version for changed bytes. The read-only report and evidence API is implemented; see the next section. Classification, Fact extraction, scheduled execution, and the Web console remain to be implemented.

The snapshot helper stores the exact bytes supplied by its caller, computes their SHA-256 hash, and uses the supplied content type; it does not assume XML or a particular collection channel. `stocks.repository.ingest_document()` remains the only Document write entry point.

## Manual source collection

The initial `config/sources.yaml` configures the official SEC Press Releases RSS source. Before requesting SEC content, replace `owner@example.com` in `user_agent` with the Owner's real contact email; SEC rejects an automated client that does not declare contact information. Review the other source identity and request bounds at the same time. From the repository root, with PostgreSQL, S3, and `.env` available:

```bash
docker compose run --rm api python -m stocks.collection --config config/sources.yaml --source sec_press_releases
```

The command prints one JSON summary with discovered, ingested, reused, skipped, and failed counts plus per-item status. It processes at most the configured `max_items` feed entries per run, with no watchlist or investment-relevance filter. A successful HTTP response is accepted only for RSS/Atom feed types and HTML, XHTML, or plain-text article pages. Feed and article requests require HTTPS, reject non-default ports, URL credentials, and non-public DNS answers, revalidate every redirect, and enforce total timeout, redirect count, compressed and decompressed response-size limits. Article responses are allowed by their media type, while the stored content type keeps the declared `charset` so the later parser can strictly decode the original bytes. A failed or empty article response still registers the acquired Feed title and summary as `partial`; it never registers the feed itself as article evidence, and every later run retries the article even when the source gives no time signal. A feed entry whose article link cannot be parsed is recorded as that entry's own failure and the remaining entries continue. Items whose stored content is complete are skipped without an article request unless a changed `updated` or `pubDate` signal triggers a recheck, and the article byte hash determines whether a new version is created.

The collector is manual and does not create Task/Run records, a collection cursor, chunks, embeddings, classifications, or Facts. It does not include a browser renderer for JavaScript-only article pages. A real SEC run still requires configured PostgreSQL and S3 services; the unit tests use fixed HTTP responses and injected storage and do not contact SEC or S3.

## Manual document parsing

After collection, parse up to 20 latest Documents or select specific IDs. The default
chunk budget is 1,200 decoded characters with 150 characters of overlap; configure
`PARSE_CHUNK_SIZE` and `PARSE_CHUNK_OVERLAP`, where overlap must be smaller than size:

```bash
docker compose run --rm api python -m stocks.processing --limit 20
docker compose run --rm api python -m stocks.processing \
  --document-id <document-uuid> --include-text
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
configuration. `--reprocess` creates a new batch. If that attempt fails, the current
successful batch and its Chunks remain readable and unchanged; a later success becomes
the current batch without deleting history. Unsupported formats and documents with no
extractable evidence text are recorded as `unsupported` or `failed`; they are never
stored as empty successful chunks. PDF/JSON/XML parsing, OCR, classification, Facts,
scheduled execution, and the Web console remain future work.
The separate manual Task runner is implemented; see
[Manual research Task](#manual-research-task).

## Manual embedding indexing and search

Configure `EMBEDDING_MODEL_NAME` alongside the existing model provider variables. The
selected provider must expose the OpenAI-compatible `/embeddings` operation. The current
vector space is fixed at 1,536 dimensions, matching the HNSW cosine index in the fresh
database schema. Any configured embedding fallback must use the same model name and
dimensions.

After parsing, index up to 20 latest Documents or select explicit IDs:

```bash
docker compose run --rm api python -m stocks.indexing --limit 20
docker compose run --rm api python -m stocks.indexing \
  --document-id <document-uuid>
```

An unchanged current batch reuses its vectors. `--reindex` regenerates them without
reading the original S3 snapshot or parsing it again. Displays the index state in
`/api/documents`, including indexed and missing Chunk counts.

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

## Manual research Task

`config/tasks.yaml` defines the controlled step/Agent registry and
`config/models.yaml` defines the research model routing. The current runner supports
ordered `select_evidence` steps and a final `research_agent` step. It reads the current
saved Chunks, persists the selected Evidence Package, validates each finding against
the recorded Document/Chunk versions, and saves an Analysis plus a readable Report. It
does not collect, parse, index, or call external Tools. The Agent input is sent as
untrusted JSON data and the configured `tools` list must be empty.

After PostgreSQL and the intended embedding index are ready:

```bash
docker compose run --rm api python -m stocks.execution \
  --config config/tasks.yaml \
  --models-config config/models.yaml \
  --task saved_evidence_brief
```

The command prints one JSON object containing `run_id`, `status`, `created`,
`executed`, the objective/scope snapshots, ordered step results, and safe model-call
metadata. Exit code `0` means the newly executed Run succeeded, or that an existing
queued/running Run was returned; `1` means a newly executed Run failed; `2` means
configuration or execution initialization failed and the safe error is printed to
stderr. Repeated triggering while a Run is queued or running returns that Run and exits
`0` without taking ownership. A new Run becomes possible only after the previous Run
has ended.

Each `select_evidence` step may set up to eight `queries`, a `limit_per_query`, a
`max_tokens` budget, and up to 20 unique `source_ids`. With no explicit `queries`, the
runner derives direct company/symbol and separate background topic queries from
`run.scope`; it does not require every scope dimension in one AND filter.
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
