# Development quickstart

Requires Docker Compose, an S3-compatible endpoint accessible from the API container, and an actual RSS/Atom URL.

1. Copy .env.example to .env. Change POSTGRES_PASSWORD and update DATABASE_URL to use the same password. Configure S3_ENDPOINT_URL, S3_BUCKET and S3 credentials.
2. Run: docker compose up -d --build postgres api
3. Migrate: docker compose run --rm api alembic upgrade head
4. Verify: http://127.0.0.1:8000/health
5. Set a real HTTPS RSS URL and enabled: true in config/sources.yaml.
6. Collect once: docker compose run --rm api python -m stocks.cli collect --all
7. View stored records: http://127.0.0.1:8000/api/documents

The collector snapshots the original feed into S3 and inserts deduplicated feed entries into PostgreSQL. It does not retrieve full linked articles or perform AI analysis. The HTTP model client is implemented but not yet wired into an Agent or Task runner.

Local unit tests: cd backend && pip install -e '.[dev]' && pytest

This early API has no application-level authentication. The sample binds it only to 127.0.0.1; do not expose it publicly until access control is added. Tailscale or equivalent private network routing to S3 must be independently configured.
