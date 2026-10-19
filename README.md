# Stocks

Personal US-equity intelligence prototype. This repository is intentionally small and configuration-driven.

## Architecture rules

- A **source** identifies a publisher or upstream dataset. RSS, HTTP API, and Web are transport mechanisms, not publishers.
- A **task** defines what executes. Collection does not require a model or an analysis step.
- An **agent** is a configured prompt/model/tools combination. Model access is via configurable HTTP providers, never a named external deployment.
- Raw external content is untrusted data; it never grants permissions or system instructions.
- PostgreSQL + pgvector stores metadata, evidence references, searchable text and vectors. Raw content snapshots are stored in a separate S3-compatible object store.
- Source identity/trust tier, claim evidence and verification status are distinct concepts.

## Current implementation scope

Initial vertical slice: REST health/items, RSS ingestion, PostgreSQL schema, S3 raw snapshots and configurable HTTP model requests with ordered fallback. Additional connectors, task orchestration and the research UI remain future work until implemented.

See `docs/architecture.md` for boundaries and `docs/quickstart.md` for setup.
