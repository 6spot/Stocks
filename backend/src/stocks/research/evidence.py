"""Merge incremental changes and historical context into one evidence set.

Evidence Selection reads two things from the same Repository. A change window
tells it what became newly researchable; a semantic query over saved Chunks tells
it what historical material is relevant. Both legitimately describe the same
Document, and often the same Chunk, so the model must never be handed one piece
of material twice.

A *group* is one Document version plus the structured Facts derived from it. The
report still cites a stored Chunk, because a Fact must be traceable to the bytes
that produced it; the Facts are attached to that group so a Document and its Fact
are never counted as independent support.

Two merges exist and both stay deterministic. Documents that are the same
evidence chain merge by identity. Documents that declare the same original — a
canonical link, the same article URL, or the same absolute feed id — merge into
one *origin group*, so a syndicated article never looks like several independent
confirmations. Fuzzy duplicate detection from titles or vector similarity is
deliberately absent, and Facts that disagree are compared, never averaged.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from stocks.models import Fact
from stocks.origins import origin_key
from stocks.repository import SearchHit

NEW_INFORMATION = "new_information"
HISTORICAL_CONTEXT = "historical_context"
EvidenceRole = Literal["new_information", "historical_context"]


@dataclass
class EvidenceGroup:
    """One evidence chain: a Document version, its Chunks and its Facts."""

    document_id: UUID
    role: EvidenceRole
    source_id: str
    title: str
    document_version: int
    published_at: datetime | None = None
    collected_at: datetime | None = None
    reasons: list[str] = field(default_factory=list)
    # ``direct`` / ``background`` describes the semantic query that found this
    # material; ``role`` above describes whether it is new or historical. Keeping
    # both lets a background query still surface a genuinely new change.
    match_role: str | None = None
    # Explicit origin declaration of this Document version, when the source made
    # one. ``None`` means "no confirmed original", never "probably unique".
    origin: dict | None = None
    kinds: list[str] = field(default_factory=list)
    watermark: int | None = None
    chunks: list[SearchHit] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)

    @property
    def chunk_ids(self) -> list[str]:
        return list(dict.fromkeys(str(chunk.chunk_id) for chunk in self.chunks))

    @property
    def fact_ids(self) -> list[str]:
        return list(
            dict.fromkeys(str(fact["fact_id"]) for fact in self.facts)
        )

    @property
    def is_structured_only(self) -> bool:
        return not self.chunks

    def to_ref(self) -> dict:
        """Project this group for ``EvidencePackage`` and the Research Agent.

        The representative Chunk is the first selected Chunk and is what a Report
        cites. A Fact-only Document has no readable Chunk, so ``ref`` is ``None``
        and the group stays as structured evidence rather than inventing text.
        """

        representative = self.chunks[0] if self.chunks else None
        return {
            "ref": str(representative.chunk_id) if representative else None,
            "evidence_group": True,
            "role": self.role,
            "match_role": self.match_role,
            "document_id": str(self.document_id),
            "document_version": self.document_version,
            "source_id": self.source_id,
            "title": self.title,
            "published_at": (
                self.published_at.isoformat()
                if self.published_at is not None
                else None
            ),
            "collected_at": (
                self.collected_at.isoformat()
                if self.collected_at is not None
                else None
            ),
            "kinds": list(self.kinds),
            "reasons": list(dict.fromkeys(self.reasons)),
            "watermark": self.watermark,
            "chunk_ids": self.chunk_ids,
            "fact_refs": deepcopy(self.facts),
            "chunk_count": len(self.chunks),
            "fact_count": len(self.facts),
            "origin": deepcopy(self.origin),
        }


class EvidenceCollector:
    """Accumulate candidates from both read paths without double counting.

    Groups are keyed by Document, which is the unit the contract merges on. A
    second Chunk from the same Document joins the existing group and does not
    create a second counted piece of independent evidence.
    """

    def __init__(self) -> None:
        self._groups: dict[UUID, EvidenceGroup] = {}
        self._fact_ids: set[UUID] = set()
        self._chunk_ids: set[UUID] = set()

    def add_hit(
        self,
        hit: SearchHit,
        *,
        role: EvidenceRole,
        reason: str,
        match_role: str | None = None,
        facts: list[dict] | None = None,
        kinds: tuple[str, ...] | list[str] = (),
        watermark: int | None = None,
        origin: dict | None = None,
    ) -> EvidenceGroup:
        group = self._groups.get(hit.document_id)
        if group is None:
            group = EvidenceGroup(
                document_id=hit.document_id,
                role=role,
                source_id=hit.source_id,
                title=hit.title,
                document_version=hit.version,
                published_at=hit.published_at,
                collected_at=hit.collected_at,
                origin=deepcopy(origin),
            )
            self._groups[hit.document_id] = group
        if hit.chunk_id not in self._chunk_ids:
            self._chunk_ids.add(hit.chunk_id)
            group.chunks.append(hit)
        if role == NEW_INFORMATION:
            # New information wins over background for the same Document; the
            # group is still one evidence chain, and the reason records both.
            group.role = NEW_INFORMATION
        if reason not in group.reasons:
            group.reasons.append(reason)
        if match_role is not None:
            group.match_role = match_role
        for kind in kinds:
            if kind not in group.kinds:
                group.kinds.append(kind)
        if watermark is not None:
            group.watermark = (
                watermark if group.watermark is None else max(group.watermark, watermark)
            )
        if facts:
            self.add_facts(group, facts)
        return group

    def add_fact_only(
        self,
        *,
        document_id: UUID,
        source_id: str,
        title: str,
        document_version: int,
        published_at: datetime | None,
        collected_at: datetime | None,
        facts: list[dict],
        kinds: tuple[str, ...] | list[str] = (),
        watermark: int | None = None,
        origin: dict | None = None,
    ) -> EvidenceGroup | None:
        """Keep structured evidence whose Document has no readable current Chunk.

        A structured API response legitimately enters research as Facts without a
        vector index. The group cites the original bytes through each Fact's own
        ``evidence_ref`` instead of inventing a Chunk reference.
        """

        if not facts:
            return None
        group = self._groups.get(document_id)
        if group is None:
            group = EvidenceGroup(
                document_id=document_id,
                role=NEW_INFORMATION,
                source_id=source_id,
                title=title,
                document_version=document_version,
                published_at=published_at,
                collected_at=collected_at,
                reasons=["new_structured_evidence"],
                origin=deepcopy(origin),
            )
            self._groups[document_id] = group
        self.add_facts(group, facts)
        for kind in kinds:
            if kind not in group.kinds:
                group.kinds.append(kind)
        if watermark is not None:
            group.watermark = (
                watermark if group.watermark is None else max(group.watermark, watermark)
            )
        return group

    def add_facts(self, group: EvidenceGroup, facts: list[dict]) -> None:
        for fact in facts:
            uuid_id = UUID(str(fact["fact_id"]))
            if uuid_id in self._fact_ids:
                continue
            self._fact_ids.add(uuid_id)
            group.facts.append(deepcopy(fact))

    def get(self, document_id: UUID) -> EvidenceGroup | None:
        return self._groups.get(document_id)

    def has_chunk(self, chunk_id: UUID) -> bool:
        return chunk_id in self._chunk_ids

    def has_document(self, document_id: UUID) -> bool:
        return document_id in self._groups

    def record_match(self, hit: SearchHit, *, role: str, reason: str) -> None:
        """Note that the semantic path also reached an existing group.

        The incremental path may have added the same Document+Chunk first. The
        group must still record the query-side match so ``direct``/``background``
        remains visible without adding the material a second time.
        """

        group = self._groups.get(hit.document_id)
        if group is None:
            return
        if group.match_role is None:
            group.match_role = role
        if reason not in group.reasons:
            group.reasons.append(reason)

    def groups(self) -> list[EvidenceGroup]:
        """Deterministic priority: new information, recent change, identity."""

        def key(group: EvidenceGroup) -> tuple:
            role_rank = 0 if group.role == NEW_INFORMATION else 1
            return (
                role_rank,
                -(group.watermark or 0),
                str(group.document_id),
            )

        return sorted(self._groups.values(), key=key)

    @property
    def chunk_count(self) -> int:
        return sum(len(group.chunks) for group in self._groups.values())

    @property
    def fact_count(self) -> int:
        return len(self._fact_ids)


@dataclass
class OriginGroup:
    """Documents that declared the same original, presented as one source.

    The group never replaces the material: every Document keeps its own Chunks,
    Facts and original bytes, so a reader can still open each copy. It only says
    how many *independent originals* the selection actually reached.
    """

    origin_id: str
    kind: str
    key: str
    url: str | None
    declared_sources: list[dict] = field(default_factory=list)
    document_ids: list[UUID] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)

    def to_ref(self) -> dict:
        return {
            "origin_id": self.origin_id,
            "kind": self.kind,
            "key": self.key,
            "url": self.url,
            "declared_sources": deepcopy(self.declared_sources),
            "document_ids": [str(value) for value in self.document_ids],
            "source_ids": list(dict.fromkeys(self.source_ids)),
            "document_count": len(self.document_ids),
            "independent_source_count": 1,
            "merged": len(self.document_ids) > 1,
        }


def group_origins(groups: list[EvidenceGroup]) -> list[OriginGroup]:
    """One group per confirmed original, in the order the groups were ranked.

    A Document whose source declared nothing keeps its own group keyed by its
    Document id: "no declaration" is reported as unknown, never as unique.
    """

    collected: dict[str, OriginGroup] = {}
    order: list[str] = []
    for group in groups:
        key = origin_key(group.origin)
        if key is None:
            kind = "single_document"
            key = f"document:{group.document_id}"
            url = None
            declared: list[dict] = []
        else:
            kind = str(group.origin["kind"])
            url = group.origin.get("url")
            declared = list(group.origin.get("declared_sources") or [])
        entry = collected.get(key)
        if entry is None:
            entry = OriginGroup(
                origin_id=f"origin-{len(order) + 1}",
                kind=kind,
                key=key,
                url=url,
                declared_sources=declared,
            )
            collected[key] = entry
            order.append(key)
        if group.document_id not in entry.document_ids:
            entry.document_ids.append(group.document_id)
        if group.source_id not in entry.source_ids:
            entry.source_ids.append(group.source_id)
    return [collected[key] for key in order]


@dataclass(frozen=True)
class _FactBasis:
    value: object
    unit: str | None
    currency: str | None
    period_start: object
    period_end: object


def metric_comparisons(groups: list[EvidenceGroup]) -> list[dict]:
    """Compare same-metric Facts across the whole selection, side by side.

    A differing value with an identical basis, a different basis (unit or
    currency), and a different observation period are separate flags on one
    entry. Nothing is averaged, overwritten or marked resolved: the entry only
    states what the selected originals actually said.
    """

    by_metric: dict[tuple, list[tuple[dict, _FactBasis]]] = {}
    order: list[tuple] = []
    for group in groups:
        for item in group.facts:
            metric_id = item.get("metric_id")
            if not metric_id:
                continue
            identity = (
                item.get("subject_name"),
                item.get("symbol"),
                metric_id,
            )
            if identity not in by_metric:
                by_metric[identity] = []
                order.append(identity)
            attributes = item.get("attributes")
            currency = (
                attributes.get("currency")
                if isinstance(attributes, dict)
                else None
            )
            basis = _FactBasis(
                value=item.get("value"),
                unit=item.get("unit"),
                currency=currency if isinstance(currency, str) else None,
                period_start=item.get("period_start"),
                period_end=item.get("period_end"),
            )
            by_metric[identity].append((item, basis))

    comparisons: list[dict] = []
    for identity in order:
        entries = by_metric[identity]
        if len(entries) < 2:
            continue
        bases = {
            (basis.unit, basis.currency, basis.period_start, basis.period_end)
            for _, basis in entries
        }
        values_by_basis: dict[tuple, set] = {}
        for _, basis in entries:
            keys = values_by_basis.setdefault(
                (basis.unit, basis.currency, basis.period_start, basis.period_end),
                set(),
            )
            keys.add(_hashable(basis.value))
        periods = {(basis.period_start, basis.period_end) for _, basis in entries}
        units = {basis.unit for _, basis in entries}
        value_conflict = any(len(values) > 1 for values in values_by_basis.values())
        basis_mismatch = len(units) > 1 or len(
            {(basis.unit, basis.currency) for _, basis in entries}
        ) > 1
        period_mismatch = len(periods) > 1
        comparison_id = f"comparison-{len(comparisons) + 1}"
        subject_name, symbol, metric_id = identity
        for item, _basis in entries:
            item["comparison_id"] = comparison_id
            item["value_conflict"] = value_conflict
            item["basis_mismatch"] = basis_mismatch
            item["period_mismatch"] = period_mismatch
        comparisons.append(
            {
                "comparison_id": comparison_id,
                "subject_name": subject_name,
                "symbol": symbol,
                "metric_id": metric_id,
                "value_conflict": value_conflict,
                "basis_mismatch": basis_mismatch,
                "period_mismatch": period_mismatch,
                "fact_count": len(entries),
                "units": sorted({unit for unit in units if unit}),
                "facts": [
                    {
                        "fact_id": item.get("fact_id"),
                        "document_id": item.get("document_id"),
                        "value": item.get("value"),
                        "unit": item.get("unit"),
                        "currency": basis.currency,
                        "period_start": item.get("period_start"),
                        "period_end": item.get("period_end"),
                        "available_at": item.get("available_at"),
                        "statement": item.get("statement"),
                    }
                    for item, basis in entries
                ],
                "note": (
                    "Values are listed as published; nothing is averaged or "
                    "marked verified."
                ),
            }
        )
    return comparisons


def _hashable(value: object) -> object:
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_hashable(item) for item in value)
    return value


def fact_selection_ref(fact: Fact, *, conflict: bool = False) -> dict:
    """The stable structured projection attached to an evidence group."""

    return {
        "fact_id": str(fact.id),
        "document_id": str(fact.document_id),
        "fact_type": fact.fact_type,
        "origin_method": fact.origin_method,
        "evidence_ref": deepcopy(fact.evidence_ref),
        "subject_name": fact.subject_name,
        "symbol": fact.symbol,
        "metric_id": fact.metric_id,
        "statement": fact.statement,
        "value": fact.value,
        "unit": fact.unit,
        "period_start": (
            fact.period_start.isoformat() if fact.period_start else None
        ),
        "period_end": (
            fact.period_end.isoformat() if fact.period_end else None
        ),
        "available_at": (
            fact.available_at.isoformat() if fact.available_at else None
        ),
        "attributes": deepcopy(fact.attributes),
        "conflict": conflict,
    }


def mark_conflicting_facts(facts: list[Fact]) -> list[dict]:
    """Project Facts, flagging same-metric values that disagree.

    Conflict is decided from structured identity only: subject, symbol, metric
    and unit. A differing value or period is kept beside the other one rather than
    averaged or overwritten, and every Fact in the disagreement is flagged so the
    reader does not have to infer it from the numbers.
    """

    projection = [fact_selection_ref(fact) for fact in facts]
    by_identity: dict[tuple, set[tuple]] = {}
    for fact in facts:
        by_identity.setdefault(_fact_identity(fact), set()).add(
            (fact.value, fact.period_start, fact.period_end, fact.statement)
        )
    for item, fact in zip(projection, facts, strict=True):
        if len(by_identity[_fact_identity(fact)]) > 1:
            item["conflict"] = True
    return projection


def _fact_identity(fact: Fact) -> tuple:
    return (fact.subject_name, fact.symbol, fact.metric_id, fact.unit)
