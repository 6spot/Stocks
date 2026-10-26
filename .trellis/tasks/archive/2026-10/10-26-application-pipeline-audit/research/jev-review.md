# Jev integration: bounded read-only review

Scope: one static pass of the current Jev integration in `backend/src/stocks/providers.py`, `backend/src/stocks/decision.py`, `backend/src/stocks/classification/service.py`, `backend/src/stocks/classification/jev.py`, `config/models.yaml`, `config/taxonomy.yaml`, `compose.yaml`, and `.env.example`. Read task PRD/design/implementation plan, curated guides, relevant domain contracts, and the current focused test file. Other agents may still be updating tests/docs; this review does not gate on their completion.

## Actionable correctness/security findings

No confirmed actionable correctness or security defect found in the requested scope. This is a static-review conclusion, not a claim that the official API has been exercised successfully.

The inspected implementation:

- Uses native `POST <base>/systemone` with configured `/v1` base, Bearer authentication, and explicit `{model,state,questions}`; it does not treat Jev as Chat Completions.
- Builds finite `choice` criteria from taxonomy identities and uses numeric `noul` probabilities for independent labels and `should_extract`; booleans, nonfinite numbers, and out-of-range values fail validation. The threshold is explicit configuration.
- Requires the exact question/answer identity set, the expected primitive type, valid chosen criteria, and valid probability values.
- Runs native and generation calls through the shared bounded attempt/fallback/error machinery. Invalid decisions/entities are validated within that machinery. A failed Jev call does not trigger entity generation as a fallback.
- Uses the separate `classification_entities` generation role for open company/ticker extraction. It retains explicitly mentioned unknown tickers without introducing watchlist or security-master restrictions, and checks that every returned entity has matching evidence in the supplied title/excerpt.
- Persists both calls' requested/reported identities, attempts and usage, plus Jev answers/threshold and entity evidence. Entity-stage failure retains the earlier successful Jev call in the stage error, and stage failure preserves an existing successful classification.
- Does not manufacture an empty classification from a missing key, missing response answers, invalid entity result, or call failure. A legitimate document with no companies/tickers can still succeed with empty entity arrays.
- Passes dedicated classification environment variables into API and Worker. Research and embedding loaders resolve their selected targets rather than eagerly resolving Jev configuration.

## Assumptions requiring actual official API verification

These are verification items, not confirmed defects. The main session owns remote validation and credentials.

1. **Question/state encoding:** confirm the official service accepts the submitted object-shaped `state`, `instructions` within both primitives, dictionary-shaped `criteria`, the generated question identifiers containing dots, and the `__none__` no-event criterion. Relevant code: `classification/jev.py:13`, `decision.py:293`, `providers.py:956`.
2. **Exact answer representation:** `normalize_answers` requires a noul answer to contain exactly `type` and numeric `noul`, and a choice answer to contain exactly `type`, `choice`, `probabilities`, and `confidence`. Confirm that the official response uses those exact fields without additional per-answer metadata and that choice probabilities contain every supplied criterion. Relevant code: `classification/jev.py:57`.
3. **Choice probability precision:** confirm official returned probability maps sum to one within absolute tolerance `1e-5` (in addition to Python's default relative tolerance). A sparse or more heavily rounded official map would be rejected under the current contract. Relevant code: `classification/jev.py:79`.
4. **Usage shape:** confirm successful responses always include nonnegative integer `usage.input_tokens` and `usage.output_tokens`, alongside a nonempty reported `model`. `_parse_typesafe_payload` intentionally rejects missing/differently named usage rather than recording a partial success. Relevant code: `providers.py:791`.
5. **Live configuration:** confirm the owner-selected key has System One access and that `https://api.typesafe.ai/v1` plus the configured model alias (example `jev-latest`) is accepted. No credential or model availability has been verified by this review.

## Verification and changes

Static source/diff reading only. No tests, AST/compile/lint checks, local runtime validation, SSH, HTTP/API calls, or deployment actions were performed. Existing focused tests were read as coverage evidence only; their results are not asserted here.

Only this review note was written. No product files were edited, no processes were started, and no runtime resources were created or retained.
