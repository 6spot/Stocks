from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.indexing.service import embed_texts
from stocks.models import (
    Analysis,
    Document,
    DocumentChunk,
    DocumentParseState,
    EvidencePackage,
)
from stocks.providers import (
    EmbeddingResponse,
    EmbeddingTargets,
    ModelClient,
    _validate_json_schema,
)
from stocks.repository import SearchFilters, SearchHit
from stocks.search.service import semantic_search
from stocks.settings import Settings

EVIDENCE_SELECTION_POLICY_VERSION = "v0.1"
MAX_EVIDENCE_TOKENS = 64_000
MAX_QUERIES = 8
MAX_QUERY_CHARACTERS = 4_000
MAX_SOURCE_IDS = 20


@dataclass(frozen=True)
class EvidenceSelectionResult:
    package: EvidencePackage
    refs: tuple[dict, ...]


def select_evidence(
    session: Session,
    config: Settings,
    targets: EmbeddingTargets,
    client: ModelClient,
    *,
    run_id: UUID,
    objective: str,
    scope: dict,
    as_of: datetime,
    queries: tuple[str, ...] | list[str],
    limit_per_query: int,
    source_ids: tuple[str, ...] | list[str] = (),
    max_tokens: int = MAX_EVIDENCE_TOKENS,
    embed: Callable[
        [ModelClient, EmbeddingTargets, list[str]], EmbeddingResponse
    ] = embed_texts,
) -> EvidenceSelectionResult:
    normalized_objective = objective.strip()
    if not normalized_objective:
        raise ValueError("Evidence objective must not be empty")
    if len(normalized_objective) > 4_000:
        raise ValueError("Evidence objective exceeds 4000 characters")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("Evidence scope must be a non-empty mapping")
    if as_of.utcoffset() is None:
        raise ValueError("Evidence as_of must include a timezone")
    if not 1 <= limit_per_query <= 50:
        raise ValueError("limit_per_query must be between 1 and 50")
    if not 1 <= max_tokens <= MAX_EVIDENCE_TOKENS:
        raise ValueError(
            f"Evidence budget max_tokens must be between 1 and {MAX_EVIDENCE_TOKENS}"
        )
    query_roles, query_texts = _normalize_queries(
        objective,
        scope,
        queries,
    )
    normalized_sources = tuple(source_ids)
    if len(normalized_sources) > MAX_SOURCE_IDS:
        raise ValueError(f"source_ids supports at most {MAX_SOURCE_IDS} values")
    if (
        len(set(normalized_sources)) != len(normalized_sources)
        or any(not item.strip() or len(item) > 120 for item in normalized_sources)
    ):
        raise ValueError(
            "source_ids must be unique non-empty values of at most 120 characters"
        )

    filters = SearchFilters(source_ids=normalized_sources)
    query_records: list[dict] = []
    selected: list[dict] = []
    selected_tokens = 0
    seen_chunks: set[UUID] = set()
    seen_tokens: set[str] = set()
    discovered_chunks: set[UUID] = set()
    examined_chunks: set[UUID] = set()
    retrieval_truncated = False
    query_truncated = False
    coverage_snapshots: list[dict] = []

    for query, role in zip(query_texts, query_roles, strict=True):
        result = semantic_search(
            session,
            config,
            targets,
            client,
            query,
            filters=filters,
            limit=limit_per_query,
            embed=embed,
        )
        selected_in_query = 0
        truncated = result.truncated
        query_truncated = query_truncated or truncated
        retrieval_truncated = retrieval_truncated or truncated
        for hit in result.hits:
            discovered_chunks.add(hit.chunk_id)
        gap_reasons: list[str] = []
        if result.coverage is None:
            gap_reasons.append("coverage_unavailable")
        else:
            coverage_snapshots.append(
                {
                    "current_documents": result.coverage.current_documents,
                    "indexed_documents": result.coverage.indexed_documents,
                    "failed_documents": result.coverage.failed_documents,
                    "current_chunks": result.coverage.current_chunks,
                    "indexed_chunks": result.coverage.indexed_chunks,
                    "unindexed_chunks": result.coverage.unindexed_chunks,
                }
            )
            if result.coverage.failed_documents:
                gap_reasons.append("embedding_failures")
            if result.coverage.unindexed_chunks:
                gap_reasons.append("unindexed_chunks")
        if truncated:
            gap_reasons.append("retrieval_truncated")
        for hit_index, hit in enumerate(result.hits, start=1):
            examined_chunks.add(hit.chunk_id)
            if hit.chunk_id in seen_chunks:
                continue
            token_identity = _token_identity(hit)
            if token_identity in seen_tokens:
                continue
            tokens = estimate_tokens(hit.text)
            if selected_tokens + tokens > max_tokens:
                gap_reasons.append("token_budget")
                retrieval_truncated = True
                break
            ref = _selected_chunk_ref(
                hit,
                role=role,
                rank=hit_index,
                targets=targets,
            )
            ref["estimated_tokens"] = tokens
            selected.append(ref)
            selected_tokens += tokens
            seen_chunks.add(hit.chunk_id)
            seen_tokens.add(token_identity)
            selected_in_query += 1
        query_records.append(
            {
                "query": query,
                "role": role,
                "source_ids": list(normalized_sources),
                "limit": limit_per_query,
                "result_count": len(result.hits),
                "selected_count": selected_in_query,
                "truncated": truncated,
                "gap_reasons": list(dict.fromkeys(gap_reasons)),
            }
        )

    # A query that reached its limit hides the rest of the candidate pool, so
    # those totals stay unknown instead of being reported as what was returned.
    # Budget-driven stops are different: the returned set is fully known, so
    # exactly which chunks were not examined is recorded.
    candidates_discovered = None if query_truncated else len(discovered_chunks)
    candidates_screened = len(examined_chunks)
    unexamined_total = (
        None if query_truncated else len(discovered_chunks - examined_chunks)
    )
    coverage = {
        "current_query_only": True,
        "candidates_discovered": candidates_discovered,
        "candidates_screened": candidates_screened,
        "selected_count": len(selected),
        "unexamined_count": unexamined_total,
        "retrieval_truncated": retrieval_truncated,
        "gaps": _coverage_gaps(
            query_records,
            selected=selected,
            config=config,
            targets=targets,
        ),
        "index": coverage_snapshots,
        "direct_match_count": sum(
            item["role"] == "direct" for item in selected
        ),
        "background_match_count": sum(
            item["role"] == "background" for item in selected
        ),
    }
    package = EvidencePackage(
        run_id=run_id,
        objective=normalized_objective,
        scope=deepcopy(scope),
        as_of=as_of,
        selection_policy_version=EVIDENCE_SELECTION_POLICY_VERSION,
        selection_budget={
            "max_tokens": max_tokens,
            "limit_per_query": limit_per_query,
            "estimated_selected_tokens": selected_tokens,
        },
        queries=query_records,
        selected_chunks=selected,
        coverage=coverage,
        candidates_discovered=coverage["candidates_discovered"],
        candidates_screened=coverage["candidates_screened"],
        selected_count=coverage["selected_count"],
        unexamined_count=coverage["unexamined_count"],
        retrieval_truncated=coverage["retrieval_truncated"],
        created_at=as_of,
    )
    session.add(package)
    try:
        session.flush()
        package_id = package.id
        session.commit()
    except Exception:
        session.rollback()
        raise
    persisted = session.get(EvidencePackage, package_id)
    if persisted is None:
        raise RuntimeError("Evidence Package could not be read after persistence")
    return EvidenceSelectionResult(
        package=persisted,
        refs=tuple(durable_evidence_ref(item) for item in selected),
    )


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _normalize_queries(
    objective: str,
    scope: dict,
    queries: tuple[str, ...] | list[str],
) -> tuple[list[str], list[str]]:
    if len(queries) > MAX_QUERIES:
        raise ValueError(f"Evidence selection supports at most {MAX_QUERIES} queries")
    query_texts = [item.strip() for item in queries if item.strip()]
    if any(len(item) > MAX_QUERY_CHARACTERS for item in query_texts):
        raise ValueError(
            f"Evidence query exceeds {MAX_QUERY_CHARACTERS} characters"
        )
    records = [
        {"query": query, "role": _query_role(query, scope)} for query in query_texts
    ]
    if not records:
        derived_roles: list[tuple[str, str]] = []
        for key in ("companies", "symbols", "regions", "industries"):
            values = scope.get(key)
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, str) and value.strip():
                        derived_roles.append(
                            (f"{objective} {value.strip()}", "direct")
                        )
        topics = scope.get("topics")
        if isinstance(topics, list):
            for value in topics:
                if isinstance(value, str) and value.strip():
                    derived_roles.append(
                        (f"{objective} {value.strip()}", "background")
                    )
        if not derived_roles:
            derived_roles.append((objective, "direct"))
        records = [
            {"query": query, "role": role}
            for query, role in derived_roles[:MAX_QUERIES]
        ]
    return (
        [item["role"] for item in records],
        [item["query"] for item in records],
    )


def _query_role(query: str, scope: dict) -> Literal["direct", "background"]:
    """Separate queries that name a scoped subject from background queries.

    A scope without companies or symbols (a market, region or industry review)
    has no separately nameable subject, so its queries are all direct.
    """
    tokens = [
        value.strip()
        for key in ("companies", "symbols")
        for value in (scope.get(key) or [])
        if isinstance(value, str) and value.strip()
    ]
    if not tokens:
        return "direct"
    for token in tokens:
        if re.search(
            rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])",
            query,
            flags=re.IGNORECASE,
        ):
            return "direct"
    return "background"


def _selected_chunk_ref(
    hit: SearchHit,
    *,
    role: str,
    rank: int,
    targets: EmbeddingTargets,
) -> dict:
    return {
        "chunk_id": str(hit.chunk_id),
        "document_id": str(hit.document_id),
        "parse_batch_id": str(hit.parse_batch_id),
        "document_version": hit.version,
        "source_id": hit.source_id,
        "position": hit.position,
        "role": role,
        "dimensions": targets.dimensions,
        "model_name": targets.model_name,
        "reason": (
            "direct_scope_match"
            if role == "direct"
            else "background_scope_match"
        ),
        "similarity": hit.similarity,
        "query_rank": rank,
        "title": hit.title,
        "published_at": (
            hit.published_at.isoformat() if hit.published_at else None
        ),
        "source_updated_at": (
            hit.source_updated_at.isoformat()
            if hit.source_updated_at
            else None
        ),
        "collected_at": hit.collected_at.isoformat(),
        "completeness": hit.completeness,
    }


def durable_evidence_ref(item: dict) -> dict:
    """Project one selected Chunk to the persisted reference used by Runs."""
    fields = (
        "chunk_id",
        "document_id",
        "parse_batch_id",
        "document_version",
        "source_id",
        "position",
    )
    return {field: deepcopy(item[field]) for field in fields}


def _token_identity(hit: SearchHit) -> str:
    return hashlib.sha256(
        f"{hit.source_id}\0{hit.title}\0{hit.text}".encode()
    ).hexdigest()


def _coverage_gaps(
    queries: list[dict],
    *,
    selected: list[dict],
    config: Settings,
    targets: EmbeddingTargets,
) -> list[str]:
    # Every reason recorded per query stays visible at package level; the extra
    # checks below describe the combined result, not an individual query.
    gaps: list[str] = [
        reason for item in queries for reason in item["gap_reasons"]
    ]
    if not queries:
        gaps.append("no_queries")
    if not selected:
        gaps.append("no_selected_evidence")
    for role in ("direct", "background"):
        queries_for_role = [item for item in queries if item["role"] == role]
        if not queries_for_role:
            continue
        if not any(item["result_count"] for item in queries_for_role):
            gaps.append(f"no_{role}_matches")
        elif not any(item["role"] == role for item in selected):
            gaps.append(f"no_{role}_selected")
    if any(item["completeness"] == "partial" for item in selected):
        gaps.append("partial_source_content")
    if getattr(config, "embedding_max_query_characters", 0) <= 0:
        gaps.append("invalid_query_limit")
    if not targets.aliases:
        gaps.append("embedding_route_unavailable")
    return list(dict.fromkeys(gaps))


def validate_selected_versions(
    session: Session,
    selected: list[dict],
) -> dict[tuple[str, str], dict]:
    """Return allowable references after checking current Document/Chunk identity."""
    allowed: dict[tuple[str, str], dict] = {}
    for item in selected:
        try:
            chunk_id = UUID(item["chunk_id"])
            document_id = UUID(item["document_id"])
            parse_batch_id = UUID(item["parse_batch_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Evidence Package contains an invalid reference") from exc
        row = session.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .join(
                DocumentParseState,
                DocumentParseState.document_id == Document.id,
            )
            .where(
                DocumentChunk.id == chunk_id,
                DocumentChunk.document_id == document_id,
                DocumentChunk.parse_batch_id == parse_batch_id,
                DocumentParseState.current_batch_id == parse_batch_id,
                Document.version == item.get("document_version"),
            )
        ).first()
        if row is None:
            raise ValueError(
                "Evidence Package reference does not match a saved Document "
                "and Chunk version"
            )
        key = (str(chunk_id), str(document_id))
        allowed[key] = {
            "chunk_id": str(chunk_id),
            "document_id": str(document_id),
            "parse_batch_id": str(parse_batch_id),
            "document_version": int(item["document_version"]),
            "source_id": str(row[1].source_id),
            "position": int(row[0].position),
            "title": row[1].title,
            "text": row[0].text,
            "published_at": (
                row[1].published_at.isoformat()
                if row[1].published_at
                else None
            ),
            "collected_at": row[1].collected_at.isoformat(),
            "completeness": row[1].completeness,
            "locator": deepcopy(row[0].locator),
        }
    return allowed


def parse_agent_output(
    content: str,
    schema: dict,
    allowed_refs: dict[tuple[str, str], dict],
) -> tuple[dict, list[dict]]:
    """Validate model output shape and every citation against the saved package."""
    try:
        output = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Research output is not valid JSON") from exc
    if not isinstance(output, dict):
        raise ValueError("Research output must be an object")
    _validate_json_schema(output, schema)
    raw_refs = output.get("evidence_refs", [])
    if not isinstance(raw_refs, list):
        raise ValueError("Research output evidence_refs must be a list")
    refs: list[dict] = []
    seen_refs: set[tuple[str, str]] = set()
    for ref in raw_refs:
        if not isinstance(ref, dict):
            raise ValueError("Research output contains an invalid evidence reference")
        key = (ref.get("chunk_id"), ref.get("document_id"))
        if key not in allowed_refs:
            raise ValueError(
                "Research output references evidence outside the selected input"
            )
        if key not in seen_refs:
            refs.append(durable_evidence_ref(allowed_refs[key]))
            seen_refs.add(key)
    findings = output.get("findings")
    if not isinstance(findings, list):
        raise ValueError("Research output findings must be a list")
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("Research output findings must be objects")
        finding_refs = finding.get("evidence_refs")
        if not isinstance(finding_refs, list) or not finding_refs:
            raise ValueError(
                "Each Research output finding must cite selected evidence"
            )
        for ref in finding_refs:
            if not isinstance(ref, dict):
                raise ValueError(
                    "Research output contains an invalid finding evidence reference"
                )
            key = (ref.get("chunk_id"), ref.get("document_id"))
            if key not in allowed_refs:
                raise ValueError(
                    "Research output finding references evidence outside "
                    "the selected input"
                )
    if not refs:
        for finding in findings:
            for ref in finding["evidence_refs"]:
                key = (ref.get("chunk_id"), ref.get("document_id"))
                if key in allowed_refs and key not in seen_refs:
                    refs.append(durable_evidence_ref(allowed_refs[key]))
                    seen_refs.add(key)
    return output, refs


def validate_analysis_content(
    output: dict,
    allowed_refs: dict[tuple[str, str], dict],
) -> tuple[list[dict], list[str]]:
    """Return persisted findings and questions, or reject an invalid Analysis."""
    raw_findings = output.get("findings")
    raw_questions = output.get("open_questions")
    if not isinstance(raw_findings, list) or not isinstance(raw_questions, list):
        raise ValueError("Research output has an invalid Analysis shape")
    normalized_findings: list[dict] = []
    for finding in raw_findings:
        if not isinstance(finding, dict):
            raise ValueError("Research output findings must be objects")
        kind = finding.get("kind")
        statement = finding.get("statement")
        rationale = finding.get("rationale")
        uncertainty = finding.get("uncertainty")
        raw_refs = finding.get("evidence_refs")
        if (
            kind not in {"fact", "source_opinion", "model_inference"}
            or not isinstance(statement, str)
            or not statement.strip()
            or not isinstance(rationale, str)
            or not rationale.strip()
            or not isinstance(uncertainty, str)
            or not uncertainty.strip()
            or not isinstance(raw_refs, list)
            or not raw_refs
        ):
            raise ValueError("Research output contains an invalid finding")
        normalized_refs: list[dict] = []
        for ref in raw_refs:
            if not isinstance(ref, dict):
                raise ValueError("Research output has an invalid finding reference")
            key = (ref.get("chunk_id"), ref.get("document_id"))
            if key not in allowed_refs:
                raise ValueError(
                    "Research output finding references evidence outside "
                    "the selected input"
                )
            normalized_refs.append(durable_evidence_ref(allowed_refs[key]))
        normalized_findings.append(
            {
                "kind": kind,
                "statement": statement,
                "rationale": rationale,
                "uncertainty": uncertainty,
                "evidence_refs": normalized_refs,
            }
        )
    if any(
        not isinstance(question, str) or not question.strip()
        for question in raw_questions
    ):
        raise ValueError("Research output open_questions must be non-empty strings")
    return normalized_findings, list(raw_questions)


def render_report(
    *,
    title: str,
    objective: str,
    scope: dict,
    as_of: datetime,
    analysis: Analysis,
) -> str:
    """Render the readable Report deterministically from the saved Analysis."""
    lines = [
        f"# {title}",
        "",
        f"Objective: {objective}",
        f"Scope: {json.dumps(scope, ensure_ascii=False, sort_keys=True)}",
        f"As of: {as_of.isoformat()}",
        "",
        "## Summary",
        "",
        analysis.summary,
        "",
        "## Findings",
        "",
    ]
    if not analysis.findings:
        lines.extend(["No sufficiently supported findings were identified.", ""])
    for index, finding in enumerate(analysis.findings, start=1):
        lines.extend(
            [
                f"### Finding {index}: {finding['kind']}",
                "",
                finding["statement"],
                "",
                f"Why it matters: {finding['rationale']}",
                "",
                f"Uncertainty: {finding['uncertainty']}",
                "",
                "Evidence: "
                + ", ".join(
                    f"{ref['document_id']}#{ref['chunk_id']}"
                    for ref in finding["evidence_refs"]
                ),
                "",
            ]
        )
    lines.extend(["## Open Questions", ""])
    if analysis.open_questions:
        lines.extend(f"- {question}" for question in analysis.open_questions)
    else:
        lines.append("None identified from the selected evidence.")
    gaps = analysis.coverage.get("gaps", [])
    lines.extend(["", "## Coverage", ""])
    if gaps:
        lines.extend(f"- {gap}" for gap in gaps)
    else:
        lines.append(
            "No known gaps within the queries recorded by this Evidence Package."
        )
    return "\n".join(lines).rstrip() + "\n"
