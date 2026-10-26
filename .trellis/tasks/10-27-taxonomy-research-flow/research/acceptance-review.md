# Bounded acceptance evidence follow-up

Result: quickstart finding resolved; saved smoke evidence is internally consistent within its stated limits. One acceptance-document wording mismatch remains for the parent to reconcile. No product changes are requested.

## Confirmed correction

`docs/quickstart.md` step 2 now directs the Owner to authenticated `GET /api/documents/<document-uuid>`, identifies `document.classification` and `document.classification.reason_or_evidence_ref.entity_evidence`, and explicitly says these fields are absent from the current Web evidence detail. This resolves the actionable issue in `research/check.md`. The nested entity-evidence path agrees with the saved live classification payload.

## Remaining documentation finding

- **P2 — Reconcile the verification README's pending claims before final acceptance.** `verification/README.md`, under `待完成`, still says the three-document collection/processing/research/citation acceptance and changed-prompt real Jev verification are in progress. The saved controlled/live logs report success, and `verification/acceptance-summary.json` plus `verification/taxonomy-flow-20261026170028-3be571/manifest.json` already record the completed live-only-Jev run. Update the README to identify both successful runs and their boundaries; retain cleanup or final-review items as pending only while they actually remain pending. This is a stale acceptance-status statement, not a missing implementation or grounds to repeat runtime checks.

## Evidence confirmed

- `verification/taxonomy-tests.log` records **128 passed in 3.00s**, consistent with the README's focused test command. This review read saved results; it did not rerun or independently certify the remote execution environment.
- `taxonomy-flow-controlled.log` records a successful three-document controlled run (`taxonomy-flow-20261026170017-7451ac`, zero real Jev calls). `taxonomy-flow-live.log` records the subsequent successful live-only-Jev run (`taxonomy-flow-20261026170028-3be571`, three real Jev calls). Both retain `external_success=false`.
- The live manifest identifies real task PostgreSQL/pgvector and S3 put/get, controlled source responses, and only Jev native live. The summary records 36 questions per call; classification excerpts consistently identify actual model `jev-1.14.0` and controlled `classification_entities` generation. No saved wording reviewed claims that sources, entities, Fact generation, research generation or embeddings were live.
- The explicit company+symbol document has `Northstar Silicon` / `NSIL`, `should_extract=true`, and one completed Fact. The company-name-only document has the company and empty symbols, with facts skipped as `NOT_APPLICABLE`. The industry document has neither company nor symbol, also with facts `NOT_APPLICABLE`. All three carry the same saved taxonomy fingerprint. Entity outputs remain controlled, so these are pipeline/entity-boundary checks rather than live entity-quality evidence.
- `report.md` and `controlled-research-response.json` cite all three document IDs. The report labels materials fictional, distinguishes company evidence from industry context, and explicitly limits fixed embeddings to pipeline verification rather than semantic ranking quality. Targeted citation-proof matches show three `original_hash_verified: true` and three `download_verified: true` entries; no original was fetched or rehashed during this review.
- The report retains `no_background_selected`, and the acceptance summary explicitly records that retained gap. This is consistent with the supplied incremental-selection explanation: the same documents entered incremental groups, and direct/background query roles do not guarantee a document mentions the scoped company. PRD/design/research and quickstart already state that Scope is research intent rather than a newly implemented exact tag filter. These artifacts support same-batch traceability and citation plumbing, not a live semantic relevance-quality claim or a promise of new scope filtering. Do not describe the retained gap as cleared.

## Scope and handoff

Read the task's curated check context, PRD/design/implementation plan, prior review, quickstart diff, and bounded saved acceptance artifacts. Applied the Trellis check guidance within the dispatch's narrower evidence/docs scope. Did not dump the large report-detail/research-input/citation-proof artifacts; citation-proof inspection used bounded field searches only.

This review ran no tests, lint, type checks, AST/compile checks, SSH, database/API calls, credential access or product edits. This note is the sole file written. No process or remote resource was created; parent runtime/cleanup state was not inspected, and the live manifest still records retained task resources for the parent. Parent retains acceptance-document finalization and cleanup ownership. No additional product issue was found in this bounded follow-up.

## Parent finalization after review

The parent subsequently replaced README's stale `待完成` block with both successful smoke runs, actual model/attempt/token counts, explicit controlled/live boundaries, the retained `no_background_selected` limitation and completed cleanup. The cleanup receipt confirms both task containers, the anonymous PostgreSQL volume and entire remote task directory removed; private credentials were deleted with that directory and SSH forwarding closed. This resolves the remaining documentation finding. These are parent finalization records after the independent review, not a claim that the reviewer reran checks or inspected cleanup. Application code stayed at the version verified by 128 focused tests; no repeated runtime checks were needed for these documentation corrections.
