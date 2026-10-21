"""Persistence and bounded query for generic Facts.

One table serves both origins — text extracted by a Generation Model and values
mapped directly from a raw API response — so nothing here is per-metric. A Fact
belongs to exactly one Document content version and carries an ``evidence_ref``
that resolves back to stored bytes: a Chunk locator for text, a JSON field path
for a structured response.

Idempotency is expressed as a unique ``(document_id, fact_key)``. Re-running the
same processor for the same content version with the same source positions writes
the same keys, so a retry or an explicit reprocess cannot multiply identical
Facts. A revised value arrives on a new Document version, which is a new
``document_id`` and therefore a new Fact: the old row is never overwritten, so an
older report's citation keeps pointing at the value it actually used.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from stocks.models import FACT_ORIGIN_METHODS, Document, Fact

MAX_FACT_QUERY_LIMIT = 200
MAX_STATEMENT_CHARACTERS = 4000


@dataclass(frozen=True)
class FactInput:
    """One candidate Fact, before its deterministic key is assigned."""

    fact_type: str
    origin_method: str
    evidence_ref: dict
    subject_name: str | None = None
    symbol: str | None = None
    metric_id: str | None = None
    statement: str | None = None
    value: float | None = None
    unit: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    available_at: datetime | None = None
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PersistFactsResult:
    document_id: UUID
    created: int
    reused: int
    total: int
    created_ids: tuple[UUID, ...] = ()

    def to_dict(self) -> dict:
        return {
            "document_id": str(self.document_id),
            "created": self.created,
            "reused": self.reused,
            "total": self.total,
            "created_ids": [str(value) for value in self.created_ids],
        }


def fact_key(fact: FactInput) -> str:
    """Deterministic identity of one Fact inside its Document version.

    The identity is the source position, not the value: a field path or a Chunk
    locator plus the offset it was read from. Extracting the same value twice from
    the same place is therefore the same Fact, while a genuinely different
    position gets its own row even when the text happens to match.
    """

    payload = {
        "origin": fact.origin_method,
        "type": fact.fact_type,
        "evidence": fact.evidence_ref,
        "metric": fact.metric_id,
        "subject": fact.subject_name,
        "symbol": fact.symbol,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_fact(fact: FactInput) -> None:
    """Deterministic checks applied before a Fact can be stored."""


    if fact.origin_method not in FACT_ORIGIN_METHODS:
        raise ValueError("invalid fact origin method")
    if not fact.fact_type.strip() or len(fact.fact_type) > 80:
        raise ValueError("fact_type must be a non-empty value of at most 80 characters")
    if not isinstance(fact.evidence_ref, dict) or not fact.evidence_ref:
        raise ValueError("evidence_ref must be a non-empty mapping")
    if fact.statement is None and fact.value is None:
        raise ValueError("a fact requires a statement or a numeric value")
    if fact.value is not None and fact.unit is None:
        # An unknown unit stays unknown by refusing to store the number at all,
        # rather than guessing one that research would later treat as comparable.
        raise ValueError("a numeric fact requires an explicit unit")
    if fact.statement is not None and len(fact.statement) > MAX_STATEMENT_CHARACTERS:
        raise ValueError("fact statement exceeds the stored length limit")
    if not isinstance(fact.attributes, dict):
        raise ValueError("fact attributes must be a mapping")
    for value in (fact.period_start, fact.period_end, fact.available_at):
        if value is not None and value.utcoffset() is None:
            raise ValueError("fact timestamps must include a timezone")
    if (
        fact.period_start is not None
        and fact.period_end is not None
        and fact.period_start > fact.period_end
    ):
        raise ValueError("fact period_start must not be after period_end")


def persist_facts(
    session: Session,
    document_id: UUID,
    facts: tuple[FactInput, ...] | list[FactInput],
    *,
    processor_version: str,
    config_fingerprint: str,
    created_at: datetime,
) -> PersistFactsResult:
    """Write this attempt's Facts for one Document version without committing.

    Called inside the caller's transaction so a committed Fact never exists
    without its stage progress. ``replace`` semantics apply per Document version:
    the caller decides whether to clear a previous extraction by passing
    ``replace_all=True`` (an explicit reprocess), and otherwise existing rows with
    the same key are reused instead of duplicated.
    """

    for fact in facts:
        validate_fact(fact)
    if created_at.utcoffset() is None:
        raise ValueError("created_at must include a timezone")
    if not processor_version.strip() or not config_fingerprint.strip():
        raise ValueError("Fact provenance must not be empty")

    keys = [fact_key(fact) for fact in facts]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate fact identity within one extraction result")

    existing = set(
        session.scalars(
            select(Fact.fact_key).where(Fact.document_id == document_id)
        ).all()
    )
    created = 0
    reused = 0
    created_ids: list[UUID] = []
    for fact, key in zip(facts, keys, strict=True):
        if key in existing:
            reused += 1
            continue
        row = Fact(
            document_id=document_id,
            fact_key=key,
            fact_type=fact.fact_type,
            origin_method=fact.origin_method,
            evidence_ref=dict(fact.evidence_ref),
            subject_name=fact.subject_name,
            symbol=fact.symbol,
            metric_id=fact.metric_id,
            statement=fact.statement,
            value=fact.value,
            unit=fact.unit,
            period_start=fact.period_start,
            period_end=fact.period_end,
            available_at=fact.available_at,
            attributes=dict(fact.attributes),
            processor_version=processor_version,
            config_fingerprint=config_fingerprint,
            created_at=created_at,
        )
        session.add(row)
        session.flush()
        created_ids.append(row.id)
        existing.add(key)
        created += 1
    session.flush()
    return PersistFactsResult(
        document_id=document_id,
        created=created,
        reused=reused,
        total=len(facts),
        created_ids=tuple(created_ids),
    )


def clear_facts(session: Session, document_id: UUID) -> int:
    """Remove this Document version's Facts for an explicit replacement.

    Only the caller that is about to write a complete new result does this, and
    only for its own Document version. Other versions keep their own Facts, so a
    report citing an older version stays reproducible. Without committing, the
    caller keeps the delete and the new rows in one transaction.
    """

    result = session.execute(
        delete(Fact).where(Fact.document_id == document_id)
    )
    return int(result.rowcount or 0)


@dataclass(frozen=True)
class FactFilters:
    source_ids: tuple[str, ...] = ()
    document_ids: tuple[UUID, ...] = ()
    fact_types: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    subject_names: tuple[str, ...] = ()
    metric_ids: tuple[str, ...] = ()
    origin_methods: tuple[str, ...] = ()
    period_start_from: datetime | None = None
    period_start_to: datetime | None = None
    available_from: datetime | None = None
    available_to: datetime | None = None


def search_facts(
    session: Session,
    filters: FactFilters | None = None,
    *,
    limit: int = 50,
) -> list[Fact]:
    """Bounded structured query: subject, type, metric, period and source.

    Ordering is by the source's own availability time then identifier, so a page
    is stable and a caller can tell truncation from a genuinely short result.
    """

    if not 1 <= limit <= MAX_FACT_QUERY_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_FACT_QUERY_LIMIT}")
    active = filters or FactFilters()
    if active.origin_methods:
        unknown = [
            method
            for method in active.origin_methods
            if method not in FACT_ORIGIN_METHODS
        ]
        if unknown:
            raise ValueError("unknown fact origin method")

    statement = (
        select(Fact)
        .join(Document, Document.id == Fact.document_id)
        .order_by(Fact.available_at.nulls_last(), Fact.created_at, Fact.id)
        .limit(limit)
    )
    if active.document_ids:
        statement = statement.where(Fact.document_id.in_(active.document_ids))
    if active.source_ids:
        statement = statement.where(Document.source_id.in_(active.source_ids))
    if active.fact_types:
        statement = statement.where(Fact.fact_type.in_(active.fact_types))
    if active.symbols:
        statement = statement.where(Fact.symbol.in_(active.symbols))
    if active.subject_names:
        statement = statement.where(Fact.subject_name.in_(active.subject_names))
    if active.metric_ids:
        statement = statement.where(Fact.metric_id.in_(active.metric_ids))
    if active.origin_methods:
        statement = statement.where(
            Fact.origin_method.in_(active.origin_methods)
        )
    if active.period_start_from is not None:
        statement = statement.where(Fact.period_start >= active.period_start_from)
    if active.period_start_to is not None:
        statement = statement.where(Fact.period_start <= active.period_start_to)
    if active.available_from is not None:
        statement = statement.where(Fact.available_at >= active.available_from)
    if active.available_to is not None:
        statement = statement.where(Fact.available_at <= active.available_to)
    return list(session.scalars(statement).all())


def list_document_facts(session: Session, document_id: UUID) -> list[Fact]:
    return list(
        session.scalars(
            select(Fact)
            .where(Fact.document_id == document_id)
            .order_by(Fact.created_at, Fact.id)
        ).all()
    )


def facts_for_documents(
    session: Session,
    document_ids: tuple[UUID, ...] | list[UUID],
    *,
    limit: int = MAX_FACT_QUERY_LIMIT,
) -> list[Fact]:
    """Read the bounded set of Facts derived from a page of Documents.

    Incremental Evidence Selection already holds a page of changed Documents. It
    needs their structured Facts in one query rather than one query per Document;
    the ordering keeps the result deterministic so repeated runs over the same
    window form the same evidence groups.
    """

    if not 1 <= limit <= MAX_FACT_QUERY_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_FACT_QUERY_LIMIT}")
    normalized = tuple(dict.fromkeys(document_ids))
    if not normalized:
        return []
    return list(
        session.scalars(
            select(Fact)
            .where(Fact.document_id.in_(normalized))
            .order_by(
                Fact.available_at.nulls_last(),
                Fact.created_at,
                Fact.id,
            )
            .limit(limit)
        ).all()
    )


def fact_to_dict(fact: Fact) -> dict:
    """The read shape shared by the CLI and the read-only API."""

    return {
        "id": str(fact.id),
        "document_id": str(fact.document_id),
        "fact_type": fact.fact_type,
        "origin_method": fact.origin_method,
        "evidence_ref": fact.evidence_ref,
        "subject_name": fact.subject_name,
        "symbol": fact.symbol,
        "metric_id": fact.metric_id,
        "statement": fact.statement,
        "value": fact.value,
        "unit": fact.unit,
        "period_start": _iso(fact.period_start),
        "period_end": _iso(fact.period_end),
        "available_at": _iso(fact.available_at),
        "attributes": fact.attributes,
        "processor_version": fact.processor_version,
        "created_at": _iso(fact.created_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
