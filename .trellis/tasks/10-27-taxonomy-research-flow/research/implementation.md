# Implementation handoff — taxonomy semantics and manual company research

## Delivery

Implementation completed for the dispatched backend/configuration/documentation scope. Runtime verification belongs to the parent session and has not been run by this agent. No credentials were read or changed; no local tests, lint, AST/import/compile checks, remote commands, database operations, commits, resets, reverts, archive operations or persistent processes were started.

Prior uncommitted Jev/provider work was preserved using exact edits in shared files. No old task artifacts were edited. No Repository filters, MP-01/MP-02 fixes, stock master, task-keyword aggregation or schema changes were introduced. No additional agents were launched. The available tool set has no todo tool; the stages were tracked through semantic progress reports and this handoff.

## Files changed by this implementation

- `backend/src/stocks/taxonomy.py`: required `Label.id/name/description`, optional aliases tuple; canonical mapping-only YAML parser; rejects missing/unknown fields, invalid types/blank or padded text, oversized text/alias lists, case-insensitive alias duplicates and existing invalid/duplicate IDs. Limits: name/alias 120 chars, description 2000, 32 aliases. Reusable `Label.candidate_payload()`, `Label.candidate_text()` and `Taxonomy.candidate_payload()` own candidate semantics.
- `backend/src/stocks/classification/jev.py`: choice criterion values and per-label noul instructions use that shared candidate rendering; choice keys/output IDs unchanged. Existing native protocol, threshold, answer validation and entity quote boundary retained.
- `backend/src/stocks/decision.py`: only the full OpenAI classification system message gains the shared candidate payload. Native Jev's separate entity-generation messages/schema/state receive no candidate payload or stock list.
- `backend/src/stocks/classification/service.py`: taxonomy fingerprint now hashes stable JSON with version, global instructions and complete ordered candidate semantics. Existing route fingerprint composition and persisted fields retained.
- `config/taxonomy.yaml`: canonical names/descriptions throughout; aliases and contextual descriptions for every industry/region; shared instructions distinguish semantic hints from keyword matches. Version is `2026-10-27`; stable label IDs unchanged.
- `config/tasks.yaml`: manual enabled `nvidia_research`, existing `research_writer` / `evidence_first`, NVIDIA/NVDA scope, three explicit semantic query strings (company, semiconductor background, policy background). Uses existing `list[str]` schema; selector derives direct/background roles. No new query format or role override API.
- `backend/tests/test_classification.py`: canonicalized all existing valid taxonomy fixtures; added invalid shape/semantics/aliases, ID-only output, complete shipped config, semantic/global/ordering fingerprint tests, and full OpenAI request semantics assertion.
- `backend/tests/test_classification_jev.py`: canonicalized `Label` constructor fixture; expanded native request test across all five sections and asserted candidate hints/stock names absent from entity request. Existing entity/probability/provenance failure tests retained.
- `backend/tests/test_execution.py`: shipped NVIDIA config loading and existing direct/background role inference test.
- `docs/data.md`: single owning configuration contract, limits, semantics, shared prompts, ID outputs, full fingerprint and explicit reprocess behavior; research scope/query-role boundaries.
- `docs/quickstart.md`: editable manual company example and collection → per-Document processing → optional reclassification → research → saved citation/original review procedure. States coverage limits of the default Sources and distinguishes runtime evidence types.

No `docs/execution.md` change was necessary because the Task/query schema is unchanged.

## Exact affected tests for parent validation

Only run on `100.120.14.84` in this task's isolated directory. Suggested focused scope:

```bash
cd <isolated-task-dir>/backend
.venv/bin/python -m pytest -q tests/test_classification.py tests/test_classification_jev.py tests/test_execution.py::test_config_loads_manual_nvidia_research_with_company_and_background_queries
```

Use this task's isolated `TEST_DATABASE_URL` for stage tests; absence of the variable will skip the existing PostgreSQL fixtures and is not stage verification. The parent owns required environment/tool preparation, optional syntax/lint/type checks and all runtime validation.

New or strengthened specific tests:

- `tests/test_classification.py::test_default_taxonomy_loads_full_candidate_semantics`
- `tests/test_classification.py::test_taxonomy_normalizes_a_valid_decision` (canonical fixture)
- `tests/test_classification.py::test_taxonomy_rejects_a_duplicate_or_malformed_label_id` (valid surrounding semantics so ID rejection is still tested)
- `tests/test_classification.py::test_taxonomy_rejects_noncanonical_label_semantics`
- `tests/test_classification.py::test_names_and_aliases_are_not_output_ids`
- `tests/test_classification.py::test_taxonomy_fingerprint_covers_candidate_semantics_and_order`
- `tests/test_classification.py::test_taxonomy_fingerprint_preserves_alias_order`
- `tests/test_classification.py::test_taxonomy_fingerprint_covers_global_contract`
- `tests/test_classification.py::test_one_call_returns_tags_and_the_extraction_decision` (now checks actual request semantics)
- `tests/test_classification_jev.py::test_native_request_probability_threshold_and_explicit_entity_merge` (all semantic sections and entity hint isolation)
- `tests/test_classification_jev.py::test_official_choice_criteria_limit_includes_the_no_event_option` (canonical constructor fixture)
- `tests/test_execution.py::test_config_loads_manual_nvidia_research_with_company_and_background_queries`

The preexisting classification/jev files contain the quote/entity and processing persistence failure regressions and are relevant as whole focused files. Optionally run `tests/test_article_output.py` if desired: its existing schema fixture also loads shipped `config/tasks.yaml`. No need for a default full-suite run.

## Remaining verification and limits

- Static source/fixture searches and diff review only were performed; no tests or lint passed claims are made.
- Parent still needs remote focused checks and a bounded real Jev sample after the changed candidate prompt. Prior task's Jev results do not verify this new prompt.
- Parent still owns the same-batch workflow with company+code, company-without-code and industry-only inputs through classification, applicable facts, evidence selection, report and saved citation resolution. Controlled fixtures, real Source acquisition and real external model calls must be recorded separately.
- Default SEC/Fed Sources do not promise NVIDIA company material. Missing direct company evidence must remain a coverage gap; background selection is still useful.
- Scope is not an exact label filter. The unchanged selector derives query roles from query strings/company/symbol scope. A direct query can retrieve a nonmention; role never proves original mention.
- Configuration edits change fingerprints but do not automatically reclassify completed Documents. Explicit existing reprocess is required when desired.
- No runtime/deployment resources were created by this agent. Parent's concurrent process/resource state was not inspected.
