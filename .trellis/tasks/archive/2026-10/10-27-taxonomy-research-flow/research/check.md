# Bounded taxonomy implementation review

Result: one actionable documentation issue; no new must-fix code issue found in the reviewed taxonomy changes.

## Finding

- **P2 — Point the classification inspection step to the implemented API.** `docs/quickstart.md:296-297` instructs the Owner to review saved classification and evidence quotes “in the console”. The current `frontend/src/components/document-details.tsx` renders processing status, parse/index state, chunks and Facts but does not render `document.classification` or its entity quotes. The API already includes classification in its Document projection (`backend/src/stocks/api.py:2235`), reachable through authenticated `GET /api/documents/{document_id}`. **Minimal fix:** replace that instruction with “Inspect the authenticated `GET /api/documents/<document-uuid>` response: `document.classification` contains labels/entities, and `document.classification.reason_or_evidence_ref.entity_evidence` contains the saved entity quotes.” Reuse the existing cookie/API instructions; no UI implementation is needed for this task.

## Reviewed contracts

- Loader requires mapping candidates with non-empty id/name/description, optional string-array aliases, text limits, no surrounding whitespace, no unknown fields, no case-insensitive duplicate aliases within a candidate, and no duplicate IDs within a section. Removed string/ID-only inputs have rejection coverage.
- Shared Label/Taxonomy candidate serialization carries id/name/description/aliases to Jev choice/noul and full-generation classification. Output enums and normalization continue to accept canonical IDs only. Independent entity generation receives no label candidate payload or Task watchlist.
- Taxonomy fingerprint includes version/instructions/all candidate semantic fields and preserves candidate/alias order. Processor fingerprint includes the route snapshot and taxonomy fingerprint. Fixtures cover semantic changes, ordering, stable reload, request payloads, canonical output rejection, absence of entity candidate hints, and NVIDIA configuration/query roles.
- Default taxonomy and NVIDIA Task use the intended contract. New data/quickstart text explicitly states Scope is not an exact taxonomy filter and direct/background query roles do not establish original company mentions. No Repository scope filters were added.

## Verification boundaries

This review inspected source, diff, task artifacts and curated context only. No tests, lint, type checks, AST/compile checks, database operations, external requests or credential access were performed. The parent's report of 128 remote passing tests and fresh database upgrade/check was supplied context, not independently rerun or certified here. The previous task's real 36-question Jev call does not certify the changed taxonomy prompts. The three-document smoke harness had not executed at dispatch; this review does not claim it passed.

No implementation files or existing uncommitted changes were modified. This review note is the sole file written. No processes or remote resources were started by this review; other agents' process/resource state was not inspected. Parent retains runtime validation, task-document updates, commit and archive ownership.
