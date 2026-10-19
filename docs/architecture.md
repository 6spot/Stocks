# Architecture boundaries

## Implemented in this first slice

FastAPI health and document-list routes; RSS/Atom collection with bounded HTTPS downloads and no redirects; PostgreSQL schema and pgvector extension with document chunks and evidence tables; S3-compatible raw RSS snapshots; idempotent document inserts; generic compatible HTTP model client and basic ordered fallback; Docker Compose for API and DB; one-off collection CLI.

## Not yet implemented

Periodic Worker scheduling, a general Task engine, research Agents and tool permission enforcement, source-specific HTTP API or Web connectors, remote MCP clients, vector generation and ANN indexes, reports, full-article extraction, and the React frontend. These are design intentions, not current capabilities.

## Definitions

- Source: publisher or origin of information, and source-level trust tier.
- Connector: transport such as RSS, HTTP API or Web. Specific upstreams are configurations and optional parsers, not connector protocols.
- Tool: explicit callable operation, potentially exposed through remote MCP.
- Agent: configurable role, prompt, model and tools. Not every Task needs an Agent.
- Task: scheduled or manually invoked work; may be collection-only.
- Repository: data access contract for documents, evidence and searches.

No external model service or proprietary proxy is assumed in the core architecture.

## Trust and safety

Source credibility is not claim truth. Preserve original publisher and evidence; reposts are not independent confirmations. All externally retrieved text remains untrusted data, even when originally published by an official source. It must not grant tool permissions or override Agent instructions.

## Storage

PostgreSQL + pgvector stores metadata, searchable records and vector-related columns; remote S3 stores raw snapshots. The database is not placed on an S3 mount. Embedding model and dimension are stored alongside each vector; index design is deferred until a concrete embedding setup exists.
