# Development quickstart

Requires Docker Compose. An S3-compatible endpoint is needed to use the snapshot storage helper.

1. Copy .env.example to .env. Change POSTGRES_PASSWORD and update DATABASE_URL to use the same password. Configure S3_ENDPOINT_URL, S3_BUCKET and S3 credentials when using snapshot storage.
2. Run: docker compose up -d --build postgres api
3. Initialize a fresh development database with the current schema: docker compose run --rm api alembic upgrade head (bootstrap only; no historical data upgrade or backward compatibility).
4. Check that the initialized schema matches the current models: docker compose run --rm api alembic check
5. Verify: http://127.0.0.1:8000/health
6. View stored records: http://127.0.0.1:8000/api/documents (empty on a fresh database).

The current implementation provides Source/Document/Chunk models, fresh-database initialization, a minimal versioned Document repository, a document-list API, a generic S3 snapshot helper, and an HTTP model client. The repository checks `(source_id, dedupe_key)` and SHA-256 before writing storage, reuses an existing Document for identical bytes, and creates the next immutable Document version for changed bytes. The previous RSS-only collector, collection CLI, and source configuration have been removed: they stored feed summaries as Documents and linked them to a whole-feed snapshot instead of acquiring each article's original content. No collection command is currently available. Unified collection, background parsing/chunking, classification, Fact extraction, vector indexing/search, the Task/Agent runner, reports, and the Web console remain to be implemented.

The snapshot helper stores the exact bytes supplied by its caller, computes their SHA-256 hash, and uses the supplied content type; it does not assume XML or a particular collection channel. `stocks.repository.ingest_document()` is the current Python entry point for registering a Source and a versioned raw Document; it is not yet exposed as an HTTP upload endpoint or collection command.

Install local development dependencies: cd backend && pip install -e '.[dev]'. Run only tests relevant to the change, for example: python -m pytest -q tests/test_s3.py; use -k to narrow the selection when needed. Follow AGENTS.md for when broader checks are warranted; do not run the full suite by default.

This early API has no application-level authentication. The sample binds it only to 127.0.0.1; do not expose it publicly until access control is added. Tailscale or equivalent private network routing to S3 must be independently configured.
