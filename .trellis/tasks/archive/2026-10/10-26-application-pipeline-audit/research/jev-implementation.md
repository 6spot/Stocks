# Jev implementation handoff

## Implemented scope
- `providers.py` adds the `typesafe` protocol, `decision` capability, strict native identity/usage parsing and `ModelClient.typesafe_decision`. The existing structured HTTP attempt loop now serves native `/systemone` and chat, retaining finite retries, timeout selection, ordered same-protocol fallback and safe errors. Research chat behavior remains available through the same public method. Embedding remains OpenAI-compatible.
- `classification/jev.py` derives `doc_type` and optional `event_type` choice questions and independent noul questions for every taxonomy multi-label and `should_extract`. Every expected answer is required; unknown/missing IDs, fields, labels, wrong types, nonfinite/out-of-range probabilities and invalid choice distributions are rejected. The explicit route threshold uses `>=`; noul is never cast with `bool`.
- `decision.py` loads a required `classification_entities` chat role for native classification. Both roles read the same bounded input. Entity generation cannot supply finite labels and must provide verbatim source quotes for all returned companies/tickers. Existing taxonomy validation is reused; neither Watchlist filtering nor guessed ticker lookup is introduced.
- Classification stores both roles' provider/protocol/model identities, usage, attempts, Jev answers/threshold, and entity evidence in current JSONB. If the entity step fails after Jev succeeds, the stage error includes Jev provenance; prior successful classification stays intact. No database schema or migrations change.
- `config/models.yaml` replaces the unused deployment `fast` alias with dedicated `classification`; configures `providers.typesafe`, native classification and explicit entity extraction via research. `.env.example` and both backend Compose services carry the dedicated `CLASSIFICATION_*` variables. Missing classification variables are resolved only on classification use, leaving unrelated route loads available.
- Relevant sections in `docs/data.md`, `docs/execution.md`, `docs/quickstart.md` and taxonomy guidance are updated.

## Verification status
No tests, compilation, lint/type/AST checks, API calls or integrations were executed by this subagent, locally or remotely. Changes have had source review only. Parent owns all remote verification and the remaining manual pipeline audit. No commits, containers or background processes were created by this subagent.

## Proposed minimum remote selection
Run on `100.120.14.84` in the current isolated task directory, with its test database configured and initialized by the parent:

```bash
cd <isolated-task-directory>/backend
.venv/bin/python -m pytest -q tests/test_classification_jev.py tests/test_classification.py tests/test_providers.py
.venv/bin/python -m pytest -q tests/test_execution.py -k 'provider or model_configuration or route_snapshot or unavailable_attempt_usage'
.venv/bin/python -m pytest -q tests/test_embeddings.py -k 'embedding_configuration or chat_and_embedding'
```

The classification stage fixtures require the isolated `TEST_DATABASE_URL`; absence will skip those tests and does not count as stage verification. New tests use MockTransport, so they do not prove a real Jev call. Broaden only for an observed relevant failure or the parent's pipeline audit findings.

## Remaining risks / acceptance limits
- Owner must supply a real server-side Jev key; official endpoint/model access, quota and actual response behavior remain unverified. Follow the supplied official protocol; do not relax validation or invent SDK/chat compatibility to mask a live mismatch. The requested alias remains configurable (`jev-latest` currently resolves to `jev-1.14.0` per parent verification); the response's actual identity is recorded rather than pinned in code.
- Native choice enforces the official 1..255 criteria limit before HTTP, including the no-event criterion (at most 254 event labels). Boundary cases are added to the focused test file. No invented question-count limit or tokenizer approximation was introduced; actual model context/body limits still apply.
- Native classification now requires one successful Jev call plus one successful entity generation call, each with its configured finite attempt budget. This adds dependency/latency to classification and deliberately does not turn either role's failure into empty entities or `should_extract=false`.
- Choice probabilities must cover all supplied criteria, be finite in 0..1, and sum to one within numeric tolerance. Confidence is validated as a probability without assuming it equals the chosen probability. The `jev-latest` requested alias may legitimately return a dated actual model identity; both are recorded.
- Entity quote validation checks literal source presence and value inclusion, while semantic company/ticker identification remains the configured generation model's responsibility. Existing entity count/shape limits still apply. No security master or investment heuristics were added.
- The shared structured-call loop changed structurally, which is why focused existing provider/research-route tests are included above. No manual collection→processing→research→report success is claimed here.
