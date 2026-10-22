from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from stocks.indexing.service import embed_texts
from stocks.changes import (
    ChangeCursorError,
    ChangeWindow,
    ChangedCandidate,
    changed_candidates,
    search_changes,
)
from stocks.facts import facts_for_documents
from stocks.models import (
    Analysis,
    Document,
    DocumentChunk,
    DocumentParseBatch,
    EvidencePackage,
    Fact,
)
from stocks.repository import current_document_hits, document_origins
from stocks.research.evidence import (
    HISTORICAL_CONTEXT,
    NEW_INFORMATION,
    EvidenceCollector,
    group_origins,
    mark_conflicting_facts,
    metric_comparisons,
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

EVIDENCE_SELECTION_POLICY_VERSION = "v1.0"
# Version of the fields and normalization the Evidence Package snapshot hash
# covers. Changing either requires a new value, so an old ledger entry cannot be
# silently compared against a differently shaped snapshot.
EVIDENCE_SNAPSHOT_POLICY_VERSION = "v1"
MAX_EVIDENCE_TOKENS = 64_000
MAX_QUERIES = 8
MAX_QUERY_CHARACTERS = 4_000
MAX_SOURCE_IDS = 20
# Fields that make up the frozen selection snapshot of one Evidence Package.
# A later supplemental Tool input may append to the package, never rewrite these.
INITIAL_SNAPSHOT_FIELDS = (
    "objective",
    "scope",
    "as_of",
    "selection_policy_version",
    "selection_budget",
    "queries",
    "selected_chunks",
    "evidence_groups",
    "coverage",
    "candidates_discovered",
    "candidates_screened",
    "selected_count",
    "unexamined_count",
    "retrieval_truncated",
    "change_scan",
)
LEDGER_KIND_SELECTION = "selection_snapshot"
LEDGER_KIND_INPUT = "input"
LEDGER_KIND_GAP = "gap"
ORIGIN_INITIAL = "initial"
ORIGIN_RESEARCH_INPUT = "research_input"
SUPPLEMENT_ROLE = "external_supplement"


@dataclass(frozen=True)
class EvidenceSelectionResult:
    package: EvidencePackage
    refs: tuple[dict, ...]
    scan_page: dict


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
    change_window: ChangeWindow | None = None,
    scan_query_snapshot: dict | None = None,
    after_watermark: int | None = None,
    after_change_id: UUID | str | None = None,
    page_limit: int = 100,
    scanned_document_ids: tuple[str, ...] | list[str] = (),
    commit: bool = True,
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
    if change_window is not None:
        if not isinstance(scan_query_snapshot, dict) or not scan_query_snapshot:
            raise ValueError(
                "Incremental Evidence Selection requires a query snapshot"
            )
        snapshot_sources = scan_query_snapshot.get("source_ids")
        snapshot_page_limit = scan_query_snapshot.get("page_limit")
        if (
            not isinstance(snapshot_sources, list)
            or len(snapshot_sources) > MAX_SOURCE_IDS
            or len(set(snapshot_sources)) != len(snapshot_sources)
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > 120
                for item in snapshot_sources
            )
        ):
            raise ValueError("Frozen change scan source_ids are invalid")
        if (
            not isinstance(snapshot_page_limit, int)
            or isinstance(snapshot_page_limit, bool)
            or not 1 <= snapshot_page_limit <= 1000
        ):
            raise ValueError("change page limit must be between 1 and 1000")
        snapshot_queries = scan_query_snapshot.get("queries")
        snapshot_limit_per_query = scan_query_snapshot.get("limit_per_query")
        snapshot_max_tokens = scan_query_snapshot.get("max_tokens")
        if (
            not isinstance(snapshot_queries, list)
            or len(snapshot_queries) > MAX_QUERIES
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > MAX_QUERY_CHARACTERS
                for item in snapshot_queries
            )
            or not isinstance(snapshot_limit_per_query, int)
            or isinstance(snapshot_limit_per_query, bool)
            or not 1 <= snapshot_limit_per_query <= 50
            or not isinstance(snapshot_max_tokens, int)
            or isinstance(snapshot_max_tokens, bool)
            or not 1 <= snapshot_max_tokens <= MAX_EVIDENCE_TOKENS
        ):
            raise ValueError("Frozen change scan query snapshot is invalid")
        query_roles, query_texts = _normalize_queries(
            objective,
            scope,
            queries,
        )
        # The incremental raw scan is a frozen operation. Its continuation must
        # not change when the Task's live queries or filters change. Semantic
        # background selection remains current and is intentionally not used as
        # the raw scanner's condition source or checkpoint identity.
        normalized_scan_sources = tuple(snapshot_sources)
        scan_page_limit = snapshot_page_limit
    else:
        query_roles, query_texts = _normalize_queries(
            objective,
            scope,
            queries,
        )

    filters = SearchFilters(source_ids=normalized_sources)
    query_records: list[dict] = []
    selected: list[dict] = []
    selected_tokens = 0
    # A candidate is one distinct Document+Chunk. A Document reached by both
    # the incremental and semantic paths still contributes each of its Chunks
    # once; ``evidence_group_count`` is the smaller, merged chain count.
    discovered_candidates: set[tuple[UUID, UUID]] = set()
    examined_chunks: set[UUID] = set()
    examined_candidates: set[tuple[UUID, UUID]] = set()
    retrieval_truncated = False
    query_truncated = False
    coverage_snapshots: list[dict] = []
    change_page: dict
    accumulated_scanned_documents = {
        str(value) for value in scanned_document_ids
    }
    changed_documents: dict[UUID, ChangedCandidate] = {}
    if change_window is None:
        change_page = {
            "completed": True,
            "after_watermark": None,
            "after_change_id": None,
            "raw_changes_scanned": 0,
            "candidate_documents_scanned": 0,
            "scanned_document_ids": [],
            "source_ids": list(normalized_sources),
            "kinds": [],
        }
    else:
        try:
            raw_page = search_changes(
                session,
                change_window,
                source_ids=normalized_scan_sources,
                limit=scan_page_limit,
                after_watermark=after_watermark,
                after_change_id=after_change_id,
            )
        except ChangeCursorError as exc:
            raise ValueError(exc.message) from exc
        # Group this page by Document so one source becomes one evidence chain.
        # The continuation above still comes from ``raw_page``: a grouped
        # candidate watermark is only an ordering/display value.
        for candidate in changed_candidates(
            session,
            change_window,
            source_ids=normalized_scan_sources,
            limit=scan_page_limit,
            after_watermark=after_watermark,
            after_change_id=after_change_id,
        ):
            changed_documents[candidate.document_id] = candidate
        accumulated_scanned_documents.update(
            str(value) for value in changed_documents
        )
        change_page = {
            "completed": raw_page.completed,
            "after_watermark": raw_page.after_watermark,
            "after_change_id": (
                str(raw_page.after_change_id)
                if raw_page.after_change_id is not None
                else None
            ),
            "raw_changes_scanned": len(raw_page.records),
            "candidate_documents_scanned": len(changed_documents),
            "scanned_document_ids": sorted(accumulated_scanned_documents),
            "source_ids": list(raw_page.source_ids),
            "kinds": list(raw_page.kinds),
        }
        if not raw_page.completed:
            retrieval_truncated = True

    collector = EvidenceCollector()
    incremental_gap_reasons: set[str] = set()
    if changed_documents:
        changed_document_ids = tuple(changed_documents)
        changed_hits = current_document_hits(
            session,
            changed_document_ids,
            per_document_limit=max(1, min(limit_per_query, 8)),
        )
        hits_by_document: dict[UUID, list[SearchHit]] = {}
        for hit in changed_hits:
            hits_by_document.setdefault(hit.document_id, []).append(hit)
        facts_by_document: dict[UUID, list[Fact]] = {}
        for fact in facts_for_documents(session, changed_document_ids):
            facts_by_document.setdefault(fact.document_id, []).append(fact)
        documents = {
            document.id: document
            for document in session.scalars(
                select(Document).where(Document.id.in_(changed_document_ids))
            ).all()
        }
        for document_id, candidate in changed_documents.items():
            document = documents.get(document_id)
            if document is None:
                continue
            fact_refs = mark_conflicting_facts(
                _unique_facts_by_signature(facts_by_document.get(document_id, []))
            )
            hits = hits_by_document.get(document_id, [])
            kept = False
            for hit in hits:
                tokens = estimate_tokens(hit.text)
                if selected_tokens + tokens > max_tokens:
                    # The candidate was screened, but its text cannot enter this
                    # Run input under the stated budget. Record it as excluded
                    # material; the candidate pool itself stays known.
                    incremental_gap_reasons.add("token_budget")
                    examined_chunks.add(hit.chunk_id)
                    discovered_candidates.add((hit.document_id, hit.chunk_id))
                    examined_candidates.add((hit.document_id, hit.chunk_id))
                    break
                collector.add_hit(
                    hit,
                    role=NEW_INFORMATION,
                    reason=_change_reason(candidate.kinds),
                    facts=fact_refs,
                    kinds=candidate.kinds,
                    watermark=candidate.watermark,
                )
                selected_tokens += tokens
                examined_chunks.add(hit.chunk_id)
                discovered_candidates.add((hit.document_id, hit.chunk_id))
                examined_candidates.add((hit.document_id, hit.chunk_id))
                kept = True
            if not kept:
                # No readable Chunk (a structured API response) or no room left
                # for text: retain the structured evidence instead of dropping
                # newly available Facts from the Run entirely.
                collector.add_fact_only(
                    document_id=document.id,
                    source_id=document.source_id,
                    title=document.title,
                    document_version=document.version,
                    published_at=document.published_at,
                    collected_at=document.collected_at,
                    facts=fact_refs,
                    kinds=candidate.kinds,
                    watermark=candidate.watermark,
                )

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
            # A Chunk already discovered through the incremental path is the same
            # material, not a second candidate; counting it again would inflate
            # the candidate pool for one evidence chain.
            if not collector.has_chunk(hit.chunk_id):
                discovered_candidates.add((hit.document_id, hit.chunk_id))
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
            examined_candidates.add((hit.document_id, hit.chunk_id))
            if collector.has_chunk(hit.chunk_id):
                # The same Document + Chunk already entered through the
                # incremental path. Feeding it again would double count one
                # evidence chain, but the semantic match still belongs to the
                # group's provenance.
                collector.record_match(
                    hit,
                    role=role,
                    reason=_background_reason(role),
                )
                continue
            tokens = estimate_tokens(hit.text)
            if selected_tokens + tokens > max_tokens:
                gap_reasons.append("token_budget")
                retrieval_truncated = True
                break
            collector.add_hit(
                hit,
                role=HISTORICAL_CONTEXT,
                reason=_background_reason(role),
                match_role=role,
            )
            selected_tokens += tokens
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

    groups = collector.groups()
    # Origins are read once for every selected Document, then reused by the
    # groups, the origin clusters and the report, so one declaration cannot be
    # counted two different ways.
    origins = document_origins(session, [group.document_id for group in groups])
    for group in groups:
        group.origin = origins.get(group.document_id)
    origin_groups = group_origins(groups)
    comparisons = metric_comparisons(groups)
    origin_membership = {
        document_id: (item.origin_id, len(item.document_ids))
        for item in origin_groups
        for document_id in item.document_ids
    }
    for group in groups:
        for index, chunk in enumerate(group.chunks, start=1):
            ref = _selected_chunk_ref(
                chunk,
                role=group.match_role or (
                    "direct" if group.role == NEW_INFORMATION else "background"
                ),
                rank=index,
                targets=targets,
            )
            ref["evidence_role"] = group.role
            ref["estimated_tokens"] = estimate_tokens(chunk.text)
            ref["chunk_ids"] = group.chunk_ids
            ref["fact_ids"] = group.fact_ids
            membership = origin_membership.get(group.document_id)
            if membership is not None:
                ref["origin_id"] = membership[0]
            ref["reasons"] = list(dict.fromkeys(group.reasons))
            ref["kinds"] = list(group.kinds)
            selected.append(ref)
    origin_refs = [item.to_ref() for item in origin_groups]
    evidence_groups = []
    for group in groups:
        ref = group.to_ref()
        membership = origin_membership.get(group.document_id)
        if membership is not None:
            ref["origin_id"], ref["origin_group_size"] = membership
        evidence_groups.append(ref)
    # Counts describe what the queries actually did. ``candidates_discovered``
    # stays unknown as soon as a bounded query (or the raw change page) may hide
    # more material; an empty ``unexamined_count`` means the pool is unknown,
    # which is different from "everything was screened".
    # ``discovered_candidates`` is the union of incremental and semantic
    # candidates, so a Document reached by both paths contributes once.
    if (change_window is not None and not change_page["completed"]) or query_truncated:
        candidates_discovered = None
    else:
        candidates_discovered = len(discovered_candidates)
    candidates_screened = len(examined_candidates)
    unexamined_total = (
        None
        if candidates_discovered is None
        else max(0, candidates_discovered - candidates_screened)
    )
    filtered_out = max(0, candidates_screened - len(selected))
    coverage = {
        "current_query_only": False if change_window is not None else True,
        "change_scan": deepcopy(change_page),
        "candidates_discovered": candidates_discovered,
        "candidates_screened": candidates_screened,
        "selected_count": len(selected),
        "evidence_group_count": len(evidence_groups),
        # Documents that declared the same original count once. The report must
        # use these two counts, never the material count, as independent support.
        "origin_group_count": len(origin_groups),
        "merged_origin_group_count": sum(
            item["merged"] for item in origin_refs
        ),
        "origin_groups": origin_refs,
        "fact_count": collector.fact_count,
        "metric_comparisons": comparisons,
        "filtered_out_count": filtered_out,
        "unexamined_count": unexamined_total,
        "retrieval_truncated": retrieval_truncated,
        "gaps": list(
            dict.fromkeys(
                [
                    *_coverage_gaps(
                        query_records,
                        selected=selected,
                        groups=evidence_groups,
                        config=config,
                        targets=targets,
                    ),
                    *sorted(incremental_gap_reasons),
                ]
            )
        ),
        "index": coverage_snapshots,
        "new_information_group_count": sum(
            group["role"] == NEW_INFORMATION for group in evidence_groups
        ),
        "historical_context_group_count": sum(
            group["role"] == HISTORICAL_CONTEXT for group in evidence_groups
        ),
        "direct_match_count": sum(
            group["role"] == NEW_INFORMATION for group in evidence_groups
        ),
        "background_match_count": sum(
            group["role"] == HISTORICAL_CONTEXT for group in evidence_groups
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
            "change_page_limit": (
                scan_page_limit if change_window is not None else None
            ),
            "estimated_selected_tokens": selected_tokens,
        },
        queries=query_records,
        selected_chunks=selected,
        evidence_groups=evidence_groups,
        metric_comparisons=comparisons,
        coverage=coverage,
        candidates_discovered=coverage["candidates_discovered"],
        candidates_screened=coverage["candidates_screened"],
        selected_count=coverage["selected_count"],
        unexamined_count=coverage["unexamined_count"],
        retrieval_truncated=coverage["retrieval_truncated"],
        change_scan=(
            {
                "window": {
                    "change_watermark_start": change_window.start,
                    "change_watermark_end": change_window.end,
                },
                **deepcopy(change_page),
            }
            if change_window is not None
            else None
        ),
        created_at=as_of,
    )
    session.add(package)
    try:
        session.flush()
        package_id = package.id
        if commit:
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
        scan_page=change_page,
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


def _snapshot_value(value: object) -> object:
    """Normalize one JSONB value so the snapshot hash is stable."""

    if isinstance(value, datetime):
        # One instant must hash the same whether a session reports it in UTC or
        # in a local offset, and whether it was just created or read back.
        if value.utcoffset() is not None:
            return value.astimezone(UTC).isoformat()
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _snapshot_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_snapshot_value(item) for item in value]
    return value


def evidence_package_snapshot(package: EvidencePackage) -> dict:
    """The frozen selection snapshot an append must never rewrite.

    Run ownership and creation time are deliberately excluded: a retry copies
    the selection fields verbatim into a new Run, and the copied snapshot must
    still hash to the same value.
    """

    return {
        "snapshot_policy_version": EVIDENCE_SNAPSHOT_POLICY_VERSION,
        **{
            field: _snapshot_value(getattr(package, field))
            for field in INITIAL_SNAPSHOT_FIELDS
        },
    }


def evidence_package_hash(package: EvidencePackage) -> str:
    payload = json.dumps(
        evidence_package_snapshot(package),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def merged_research_refs(package: EvidencePackage, ledger: list | None) -> list[dict]:
    """Combine the initial selection with every adopted Tool input.

    The initial ``selected_chunks`` stay the first entries and keep their own
    identity; adopted inputs follow in ledger order. Agent citation validation,
    report detail and per-evidence lookup all read this one view, so a
    supplemental Document is never citable in one place and invisible in
    another.
    """

    selected = package.selected_chunks
    if not isinstance(selected, list):
        raise ValueError("Evidence Package selected chunks are invalid")
    refs: list[dict] = []
    for item in selected:
        if not isinstance(item, dict):
            raise ValueError("Evidence Package selected chunk is invalid")
        refs.append({**deepcopy(item), "origin": ORIGIN_INITIAL})
    if ledger is None:
        return refs
    if not isinstance(ledger, list):
        raise ValueError("Research input ledger is invalid")
    for record in ledger:
        if not isinstance(record, dict):
            raise ValueError("Research input ledger entry is invalid")
        if record.get("kind") != LEDGER_KIND_INPUT:
            continue
        document_id = record.get("document_id")
        parse_batch_id = record.get("parse_batch_id")
        source_id = record.get("source_id")
        document_version = record.get("document_version")
        input_id = record.get("input_id")
        chunks = record.get("chunks")
        if (
            not isinstance(document_id, str)
            or not isinstance(parse_batch_id, str)
            or not isinstance(source_id, str)
            or not isinstance(document_version, int)
            or isinstance(document_version, bool)
            or not isinstance(input_id, str)
            or not isinstance(chunks, list)
            or not chunks
        ):
            raise ValueError("Research input ledger entry is invalid")
        timing = record.get("timing")
        if timing is not None and not isinstance(timing, list):
            raise ValueError("Research input ledger timing is invalid")
        for chunk in chunks:
            if not isinstance(chunk, dict):
                raise ValueError("Research input ledger chunk is invalid")
            chunk_id = chunk.get("chunk_id")
            position = chunk.get("position")
            if (
                not isinstance(chunk_id, str)
                or not isinstance(position, int)
                or isinstance(position, bool)
            ):
                raise ValueError("Research input ledger chunk is invalid")
            refs.append(
                {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "parse_batch_id": parse_batch_id,
                    "document_version": document_version,
                    "source_id": source_id,
                    "position": position,
                    "role": SUPPLEMENT_ROLE,
                    "reasons": ["tool_supplement"],
                    "kinds": ["tool_retrieved"],
                    "chunk_ids": deepcopy(chunk.get("chunk_ids", [chunk_id])),
                    "fact_ids": deepcopy(chunk.get("fact_ids", [])),
                    "origin": ORIGIN_RESEARCH_INPUT,
                    "input_id": input_id,
                    "tool_id": record.get("tool_id"),
                    "title": record.get("title"),
                    "timing": deepcopy(timing),
                }
            )
    return refs


def research_ledger_gaps(ledger: list | None) -> list[str]:
    """The recorded supplement gaps of one Run, in ledger order."""

    if ledger is None:
        return []
    if not isinstance(ledger, list):
        raise ValueError("Research input ledger is invalid")
    gaps: list[str] = []
    for record in ledger:
        if not isinstance(record, dict):
            raise ValueError("Research input ledger entry is invalid")
        if record.get("kind") != LEDGER_KIND_GAP:
            continue
        code = record.get("code")
        if isinstance(code, str) and code:
            gaps.append(code)
    return list(dict.fromkeys(gaps))


def selection_snapshot_entry(package: EvidencePackage, *, recorded_at: str) -> dict:
    """The ledger entry that pins the Evidence Package a Run was selected from."""

    return {
        "kind": LEDGER_KIND_SELECTION,
        "evidence_package_id": str(package.id),
        "evidence_package_hash": evidence_package_hash(package),
        "recorded_at": recorded_at,
    }


def _token_identity(hit: SearchHit) -> str:
    return hashlib.sha256(
        f"{hit.source_id}\0{hit.title}\0{hit.text}".encode()
    ).hexdigest()


def _change_reason(kinds: tuple[str, ...] | list[str]) -> str:
    """Name the strongest reason a changed Document entered this window."""

    for kind in (
        "fact_extracted",
        "fact_mapped",
        "document_indexed",
        "document_classified",
        "document_parsed",
        "document_ingested",
    ):
        if kind in kinds:
            return f"new_{kind}"
    return "new_change"


def _background_reason(role: str) -> str:
    return (
        "direct_scope_match"
        if role == "direct"
        else "background_scope_match"
    )


def _unique_facts_by_signature(facts: list[Fact]) -> list[Fact]:
    """Collapse a Fact reached through more than one structured path.

    Database idempotency already prevents duplicate rows for one content
    version. This guard is for callers that pass the same Fact twice, and keeps
    the package deterministic without discarding genuinely different Facts.
    """

    unique: dict[UUID, Fact] = {}
    for fact in facts:
        unique.setdefault(fact.id, fact)
    return list(unique.values())


def _coverage_gaps(
    queries: list[dict],
    *,
    selected: list[dict],
    groups: list[dict],
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
        elif not any(
            group.get("match_role") == role
            for group in groups
        ):
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
    """Return allowable references after checking their saved identity.

    A frozen Run input may cite a completed parse batch that is no longer the
    Document's current one: a later reparse keeps the old batch and its Chunks
    readable for historical reports and retries. New evidence selection is
    unaffected, because it reads only the current batch in the Repository.
    """
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
                DocumentParseBatch,
                DocumentParseBatch.id == DocumentChunk.parse_batch_id,
            )
            .where(
                DocumentChunk.id == chunk_id,
                DocumentChunk.document_id == document_id,
                DocumentChunk.parse_batch_id == parse_batch_id,
                DocumentParseBatch.document_id == document_id,
                DocumentParseBatch.status == "completed",
                DocumentParseBatch.content_hash == Document.content_hash,
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
            # V1 evidence-group context travels with the readable Chunk so the
            # Agent receives the structured Fact beside the text it came from
            # instead of two unrelated evidence entries.
            "role": item.get("role"),
            "reasons": deepcopy(item.get("reasons", [])),
            "kinds": deepcopy(item.get("kinds", [])),
            "chunk_ids": deepcopy(item.get("chunk_ids", [str(chunk_id)])),
            "fact_ids": deepcopy(item.get("fact_ids", [])),
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


MAX_REPORT_ORIGIN_GROUPS = 20
MAX_REPORT_COMPARISONS = 20


def _independence_section(
    analysis: Analysis, metric_comparisons: list[dict]
) -> list[str]:
    """Deterministic source-independence and conflict text.

    The Research Agent may describe these facts, but the numbers and the
    published values must never depend on the model returning them: a report
    always shows how many independent originals the selection reached, which
    documents were confirmed as the same original, and every same-metric value
    that disagrees. Nothing is averaged and nothing is marked verified.
    """

    coverage = analysis.coverage if isinstance(analysis.coverage, dict) else {}
    groups = [
        item
        for item in coverage.get("origin_groups") or []
        if isinstance(item, dict)
    ]
    lines = ["", "## Sources and Conflicts", ""]
    if not groups:
        lines.append(
            "No origin declaration was recorded for this selection, so the "
            "number of independent originals is unknown rather than equal to "
            "the number of documents."
        )
    else:
        merged = [item for item in groups if item.get("merged")]
        lines.append(
            f"Independent originals: {len(groups)} covering "
            f"{sum(int(item.get('document_count') or 0) for item in groups)} "
            "document version(s)."
        )
        if merged:
            lines.append(
                "Confirmed reprints of one original are counted once "
                f"({len(merged)} group(s)); their copies stay open below."
            )
        for item in groups[:MAX_REPORT_ORIGIN_GROUPS]:
            if not item.get("merged"):
                continue
            sources = ", ".join(str(value) for value in item.get("source_ids") or [])
            lines.append(
                f"- {item.get('origin_id')}: {item.get('document_count')} "
                f"document(s) from {sources or 'unknown sources'} — the same "
                f"original by {item.get('kind')} {item.get('url') or ''}".rstrip()
            )
        if len(groups) > MAX_REPORT_ORIGIN_GROUPS:
            lines.append(
                f"- … {len(groups) - MAX_REPORT_ORIGIN_GROUPS} more origin "
                "group(s) stay in the Evidence Package."
            )
    lines.extend(["", "Same-metric values:"])
    if not metric_comparisons:
        lines.append(
            "No two selected Facts describe the same metric, so no comparison "
            "was possible."
        )
    for item in metric_comparisons[:MAX_REPORT_COMPARISONS]:
        flags = [
            label
            for label, flag in (
                ("values differ", item.get("value_conflict")),
                ("basis differs", item.get("basis_mismatch")),
                ("periods differ", item.get("period_mismatch")),
            )
            if flag
        ]
        lines.append(
            f"- {item.get('metric_id')} "
            f"({item.get('symbol') or item.get('subject_name') or 'unlabelled'}): "
            + ", ".join(flags or ["consistent"])
        )
        for fact in item.get("facts") or []:
            lines.append(
                "  - "
                f"{fact.get('value')} {fact.get('unit') or ''}"
                + (f" {fact['currency']}" if fact.get("currency") else "")
                + f" · period {fact.get('period_start') or 'unknown'} – "
                f"{fact.get('period_end') or 'unknown'}"
                f" · available {fact.get('available_at') or 'unknown'}"
                f" · document {fact.get('document_id')}"
            )
    if len(metric_comparisons) > MAX_REPORT_COMPARISONS:
        lines.append(
            f"- … {len(metric_comparisons) - MAX_REPORT_COMPARISONS} more "
            "comparison(s) stay in the Evidence Package."
        )
    if metric_comparisons:
        lines.append(
            "Conflicting or differently based values are listed as published; "
            "none of them is averaged, overwritten or marked verified."
        )
    return lines


def render_report(
    *,
    title: str,
    objective: str,
    scope: dict,
    as_of: datetime,
    analysis: Analysis,
    metric_comparisons: list[dict] | None = None,
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
    lines.extend(_independence_section(analysis, metric_comparisons or []))
    gaps = analysis.coverage.get("gaps", [])
    lines.extend(["", "## Coverage", ""])
    if gaps:
        lines.extend(f"- {gap}" for gap in gaps)
    else:
        lines.append(
            "No known gaps within the queries recorded by this Evidence Package."
        )
    return "\n".join(lines).rstrip() + "\n"
