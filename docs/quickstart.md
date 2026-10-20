# Development quickstart

Requires Docker Compose. An S3-compatible endpoint is needed to collect and persist article snapshots.

1. Copy .env.example to .env. Change POSTGRES_PASSWORD and update DATABASE_URL to use the same password. Configure S3_ENDPOINT_URL, S3_BUCKET and S3 credentials when using snapshot storage.
2. Run: `docker compose up -d --build postgres api`
3. Initialize a fresh development database with the current schema: `docker compose run --rm api alembic upgrade head` (bootstrap only; no historical data upgrade or backward compatibility).
4. Check that the initialized schema matches the current models: `docker compose run --rm api alembic check`
5. Verify: http://127.0.0.1:8000/health
6. View stored records: http://127.0.0.1:8000/api/documents (empty on a fresh database).

The current implementation provides Source/Document/Chunk models, fresh-database initialization, a versioned Document repository, configured RSS/Atom discovery, safe HTTP article acquisition, a manual collection CLI, a document-list API, a generic S3 snapshot helper, and an HTTP model client. The repository checks `(source_id, dedupe_key)` and SHA-256 before writing storage, reuses an immutable Document version and updates a separate collection state for identical bytes, and creates the next immutable Document version for changed bytes. Background parsing/chunking, classification, Fact extraction, vector indexing/search, the Task/Agent runner, reports, and the Web console remain to be implemented.

The snapshot helper stores the exact bytes supplied by its caller, computes their SHA-256 hash, and uses the supplied content type; it does not assume XML or a particular collection channel. `stocks.repository.ingest_document()` remains the only Document write entry point.

## Manual source collection

The initial `config/sources.yaml` configures the official SEC Press Releases RSS source. Before requesting SEC content, replace `owner@example.com` in `user_agent` with the Owner's real contact email; SEC rejects an automated client that does not declare contact information. Review the other source identity and request bounds at the same time. From the repository root, with PostgreSQL, S3, and `.env` available:

```bash
docker compose run --rm api python -m stocks.collection --config config/sources.yaml --source sec_press_releases
```

The command prints one JSON summary with discovered, ingested, reused, skipped, and failed counts plus per-item status. It processes at most the configured `max_items` feed entries per run, with no watchlist or investment-relevance filter. A successful HTTP response is accepted only for RSS/Atom feed types and HTML, XHTML, or plain-text article pages. Feed and article requests require HTTPS, reject non-default ports, URL credentials, and non-public DNS answers, revalidate every redirect, and enforce total timeout, redirect count, compressed and decompressed response-size limits. A failed or empty article response still registers the acquired Feed title and summary as `partial`; it never registers the feed itself as article evidence. Unchanged items are skipped without an article request, while a changed `updated` or `pubDate` signal triggers a recheck and the article byte hash determines whether a new version is created.

The collector is manual and does not create Task/Run records, a collection cursor, chunks, embeddings, classifications, or Facts. It does not include a browser renderer for JavaScript-only article pages. A real SEC run still requires configured PostgreSQL and S3 services; the unit tests use fixed HTTP responses and injected storage and do not contact SEC or S3.

Install local development dependencies: cd backend && pip install -e '.[dev]'. Run only tests relevant to the change, for example: python -m pytest -q tests/test_s3.py; use -k to narrow the selection when needed. Follow AGENTS.md for when broader checks are warranted; do not run the full suite by default.

This early API has no application-level authentication. The sample binds it only to 127.0.0.1; do not expose it publicly until access control is added. Tailscale or equivalent private network routing to S3 must be independently configured.
