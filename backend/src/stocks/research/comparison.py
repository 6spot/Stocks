"""Deterministic comparison of two saved reports.

Comparison never re-runs research and never reads current data: both sides are
the stored Analysis, the frozen Evidence Package and the Document versions that
report actually cited. A difference between two reports therefore always means
one of two honest things: the newer Run selected different material, or the
source itself produced a new version. The module never guesses that a
previously reported value was known earlier, and it never matches Findings by
identity: only an exact ``(kind, statement)`` pair is treated as the same
finding, so a reworded conclusion shows up as one disappeared and one appeared
entry instead of a fabricated relation.

Nothing here rewrites the older report. The payload only lists the two stored
snapshots side by side, bounded to ``MAX_COMPARISON_ITEMS`` per group.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

MAX_COMPARISON_ITEMS = 50


def compare_report_sides(earlier: dict, later: dict) -> dict:
    """Compare an earlier and a later report snapshot, earlier first."""

    findings = _finding_diff(earlier, later)
    open_questions = _question_diff(earlier, later)
    documents = _document_diff(earlier, later)
    facts = _fact_diff(earlier, later)
    return {
        "earlier": _side_summary(earlier),
        "later": _side_summary(later),
        "ordering": {
            "by": "created_at",
            "requested_report_id": earlier.get("ordering", {}).get(
                "requested_report_id"
            ),
            "swapped": bool(earlier.get("ordering", {}).get("swapped")),
        },
        "warnings": _warnings(earlier, later),
        "findings": findings,
        "open_questions": open_questions,
        "documents": documents,
        "facts": facts,
        "coverage": _coverage_diff(earlier, later),
        "late_material": _late_material(earlier, later),
        "notes": [
            "Only the stored Analysis, Evidence Package and cited Document "
            "versions are compared; nothing is re-researched or rewritten.",
            "A difference is reported as new material or as a new source "
            "version, never as something the earlier Run already knew.",
        ],
    }


def _side_summary(side: dict) -> dict:
    report = side.get("report") or {}
    package = side.get("package") or {}
    analysis = side.get("analysis") or {}
    coverage = package.get("coverage") if isinstance(package.get("coverage"), dict) else {}
    return {
        "report_id": report.get("report_id"),
        "run_id": report.get("run_id"),
        "analysis_id": report.get("analysis_id"),
        "task_id": report.get("task_id"),
        "title": report.get("title"),
        "created_at": report.get("created_at"),
        "run_status": side.get("run_status"),
        "as_of": package.get("as_of"),
        "evidence_package_id": package.get("evidence_package_id"),
        "selection_policy_version": package.get("selection_policy_version"),
        "scope": deepcopy(package.get("scope")),
        "summary": analysis.get("summary"),
        "analysis_created_at": analysis.get("created_at"),
        "coverage": {
            "selected_count": package.get("selected_count"),
            "evidence_group_count": coverage.get("evidence_group_count"),
            "origin_group_count": coverage.get("origin_group_count"),
            "merged_origin_group_count": coverage.get("merged_origin_group_count"),
            "fact_count": coverage.get("fact_count"),
            "candidates_discovered": package.get("candidates_discovered"),
            "candidates_screened": package.get("candidates_screened"),
            "unexamined_count": package.get("unexamined_count"),
            "retrieval_truncated": package.get("retrieval_truncated"),
            "research_input_count": side.get("research_input_count"),
            "gaps": list(coverage.get("gaps") or []),
        },
    }


def _finding_key(finding: dict) -> tuple[str, str]:
    return (
        str(finding.get("kind") or ""),
        _normalized(finding.get("statement")),
    )


def _ref_key(reference: dict) -> tuple[str, str]:
    return (
        str(reference.get("document_id") or ""),
        str(reference.get("chunk_id") or ""),
    )


def _finding_diff(earlier: dict, later: dict) -> dict:
    older = _findings(earlier)
    newer = _findings(later)
    appeared = [item for key, item in newer.items() if key not in older]
    disappeared = [item for key, item in older.items() if key not in newer]
    persisting: list[dict] = []
    for key, item in older.items():
        current = newer.get(key)
        if current is None:
            continue
        earlier_refs = {_ref_key(ref) for ref in item.get("evidence_refs") or []}
        later_refs = {_ref_key(ref) for ref in current.get("evidence_refs") or []}
        persisting.append(
            {
                "kind": item.get("kind"),
                "statement": item.get("statement"),
                "earlier_uncertainty": item.get("uncertainty"),
                "later_uncertainty": current.get("uncertainty"),
                "uncertainty_changed": item.get("uncertainty")
                != current.get("uncertainty"),
                "rationale_changed": item.get("rationale")
                != current.get("rationale"),
                "evidence_changed": earlier_refs != later_refs,
            }
        )
    return {
        "appeared": _bounded(appeared),
        "disappeared": _bounded(disappeared),
        "persisting": _bounded(persisting),
    }


def _question_diff(earlier: dict, later: dict) -> dict:
    older = _questions(earlier)
    newer = _questions(later)
    return {
        "appeared": _bounded([item for item in newer if item not in older]),
        "disappeared": _bounded([item for item in older if item not in newer]),
        "persisting": _bounded([item for item in newer if item in older]),
    }


def _document_key(document: dict) -> tuple[str, str]:
    source_id = document.get("source_id")
    dedupe_key = document.get("dedupe_key")
    if source_id and dedupe_key:
        # The same stored material across reports; a new version is a real
        # source change rather than a different Document.
        return ("source", f"{source_id}\0{dedupe_key}")
    return ("document", str(document.get("document_id") or ""))


def _document_diff(earlier: dict, later: dict) -> dict:
    older = _documents(earlier)
    newer = _documents(later)
    appeared = [
        _observed_document(item, earlier)
        for key, item in newer.items()
        if key not in older
    ]
    disappeared = [
        _observed_document(item, later)
        for key, item in older.items()
        if key not in newer
    ]
    persisting: list[dict] = []
    for key, item in older.items():
        current = newer.get(key)
        if current is None:
            continue
        persisting.append(
            {
                "source_id": item.get("source_id"),
                "dedupe_key": item.get("dedupe_key"),
                "title": current.get("title"),
                "earlier": _document_snapshot(item),
                "later": _document_snapshot(current),
                "version_changed": item.get("version") != current.get("version"),
                "content_changed": item.get("content_hash") != current.get("content_hash"),
            }
        )
    return {
        "appeared": _bounded(appeared),
        "disappeared": _bounded(disappeared),
        "persisting": _bounded(persisting),
    }


def _observed_document(document: dict, other: dict) -> dict:
    """One report's own view of a Document, plus whether the other side had it."""

    return {
        **_document_snapshot(document),
        # This side is the only one that cited the Document; the reader still
        # opens exactly the version this side used.
        "only_in_report_id": (document.get("report_id")),
        "compared_report_id": other.get("report", {}).get("report_id"),
    }


def _document_snapshot(document: dict) -> dict:
    return {
        "report_id": document.get("report_id"),
        "document_id": document.get("document_id"),
        "version": document.get("version"),
        "content_hash": document.get("content_hash"),
        "source_id": document.get("source_id"),
        "dedupe_key": document.get("dedupe_key"),
        "title": document.get("title"),
        "original_url": document.get("original_url"),
        "published_at": document.get("published_at"),
        "collected_at": document.get("collected_at"),
        "origin": deepcopy(document.get("origin")),
        "origin_id": document.get("origin_id"),
        "chunk_ids": list(document.get("chunk_ids") or []),
        "fact_ids": list(document.get("fact_ids") or []),
        "role": document.get("role"),
        "version_matches": document.get("version_matches"),
    }


def _fact_diff(earlier: dict, later: dict) -> dict:
    older = _facts(earlier)
    newer = _facts(later)
    appeared = [
        _fact_snapshot(item, earlier)
        for key, item in newer.items()
        if key not in older
    ]
    disappeared = [
        _fact_snapshot(item, later)
        for key, item in older.items()
        if key not in newer
    ]
    metric_changes: list[dict] = []
    older_metrics = _facts_by_metric(older.values())
    newer_metrics = _facts_by_metric(newer.values())
    for identity, previous in older_metrics.items():
        current = newer_metrics.get(identity)
        if current is None:
            continue
        previous_basis = {_value_key(item) for item in previous}
        current_basis = {_value_key(item) for item in current}
        if previous_basis == current_basis:
            continue
        # Metric identity is (subject, symbol, metric); a value, unit, currency
        # or observation-period difference is named instead of averaged.
        metric_changes.append(
            {
                "subject_name": identity[0],
                "symbol": identity[1],
                "metric_id": identity[2],
                "value_changed": {item.get("value") for item in previous}
                != {item.get("value") for item in current},
                "basis_changed": {
                    (item.get("unit"), item.get("currency")) for item in previous
                }
                != {(item.get("unit"), item.get("currency")) for item in current},
                "period_changed": {
                    (item.get("period_start"), item.get("period_end"))
                    for item in previous
                }
                != {
                    (item.get("period_start"), item.get("period_end"))
                    for item in current
                },
                "earlier": [_metric_value(item) for item in previous],
                "later": [_metric_value(item) for item in current],
            }
        )
    return {
        "appeared": _bounded(appeared),
        "disappeared": _bounded(disappeared),
        "metric_changes": _bounded(metric_changes),
    }


def _facts_by_metric(facts: Any) -> dict[tuple, list[dict]]:
    grouped: dict[tuple, list[dict]] = {}
    for fact in facts:
        metric_id = fact.get("metric_id")
        if not metric_id:
            continue
        identity = (
            fact.get("subject_name"),
            fact.get("symbol"),
            metric_id,
        )
        grouped.setdefault(identity, []).append(fact)
    return grouped


def _value_key(fact: dict) -> tuple:
    return (
        _hashable(fact.get("value")),
        fact.get("unit"),
        fact.get("currency"),
        fact.get("period_start"),
        fact.get("period_end"),
    )


def _metric_value(fact: dict) -> dict:
    return {
        "fact_id": fact.get("fact_id"),
        "document_id": fact.get("document_id"),
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "currency": fact.get("currency"),
        "period_start": fact.get("period_start"),
        "period_end": fact.get("period_end"),
        "available_at": fact.get("available_at"),
        "statement": fact.get("statement"),
        # A cited Fact whose frozen value is not stored stays explicitly
        # unknown instead of looking like a zero or being filled from today.
        "unavailable": bool(fact.get("unavailable", False)),
    }


def _fact_snapshot(fact: dict, side: dict) -> dict:
    return {
        **_metric_value(fact),
        "subject_name": fact.get("subject_name"),
        "symbol": fact.get("symbol"),
        "metric_id": fact.get("metric_id"),
        "fact_type": fact.get("fact_type"),
        "report_id": side.get("report", {}).get("report_id"),
    }


def _coverage_diff(earlier: dict, later: dict) -> dict:
    older = _coverage(earlier)
    newer = _coverage(later)
    earlier_gaps = [str(gap) for gap in older.get("gaps") or []]
    later_gaps = [str(gap) for gap in newer.get("gaps") or []]
    return {
        "earlier": _coverage_counts(earlier),
        "later": _coverage_counts(later),
        "gaps_added": _bounded(
            [gap for gap in later_gaps if gap not in earlier_gaps]
        ),
        "gaps_removed": _bounded(
            [gap for gap in earlier_gaps if gap not in later_gaps]
        ),
        # ``None`` means the candidate pool was unknown, so a comparison must
        # keep saying unknown instead of turning it into zero.
        "unexamined_unknown": {
            "earlier": _unexamined_count(earlier) is None,
            "later": _unexamined_count(later) is None,
        },
    }


def _coverage_counts(side: dict) -> dict:
    package = side.get("package") or {}
    coverage = _coverage(side)
    return {
        "selected_count": package.get("selected_count"),
        "evidence_group_count": coverage.get("evidence_group_count"),
        "origin_group_count": coverage.get("origin_group_count"),
        "merged_origin_group_count": coverage.get("merged_origin_group_count"),
        "fact_count": coverage.get("fact_count"),
        "candidates_discovered": package.get("candidates_discovered"),
        "candidates_screened": package.get("candidates_screened"),
        "unexamined_count": package.get("unexamined_count"),
        "retrieval_truncated": package.get("retrieval_truncated"),
        "research_input_count": side.get("research_input_count"),
        "gap_count": len(coverage.get("gaps") or []),
    }


def _late_material(earlier: dict, later: dict) -> dict:
    """Documents the later report cited that the earlier reference time predates."""

    as_of = (earlier.get("package") or {}).get("as_of")
    observed: list[dict] = []
    for document in _documents(later).values():
        collected_at = document.get("collected_at")
        published_at = document.get("published_at")
        if not _after(collected_at, as_of) and not _after(published_at, as_of):
            continue
        observed.append(
            {
                **_document_snapshot(document),
                "earlier_as_of": as_of,
                "collected_after_as_of": _after(collected_at, as_of),
                "published_after_as_of": _after(published_at, as_of),
            }
        )
    return {
        "earlier_as_of": as_of,
        "documents": _bounded(observed),
        "note": (
            "These Documents were published or collected after the earlier "
            "report's reference time, so they explain a difference as a real "
            "source change rather than as something known before."
        ),
    }


def _warnings(earlier: dict, later: dict) -> list[dict]:
    warnings: list[dict] = []
    older_report = earlier.get("report") or {}
    newer_report = later.get("report") or {}
    if older_report.get("task_id") != newer_report.get("task_id"):
        warnings.append(
            {
                "code": "different_task",
                "message": "The two reports come from different Tasks.",
            }
        )
    older_scope = _canonical(earlier.get("package", {}).get("scope"))
    newer_scope = _canonical(later.get("package", {}).get("scope"))
    if older_scope != newer_scope:
        warnings.append(
            {
                "code": "different_scope",
                "message": "The two reports were produced for different scopes.",
            }
        )
    older_as_of = (earlier.get("package") or {}).get("as_of")
    newer_as_of = (later.get("package") or {}).get("as_of")
    if older_as_of is not None and newer_as_of is not None:
        if not _after(newer_as_of, older_as_of):
            warnings.append(
                {
                    "code": "as_of_not_advancing",
                    "message": (
                        "The later report does not reference a later as_of, so "
                        "it is not a time-ordered continuation."
                    ),
                }
            )
    return warnings


def _coverage(side: dict) -> dict:
    coverage = (side.get("package") or {}).get("coverage")
    return coverage if isinstance(coverage, dict) else {}


def _unexamined_count(side: dict) -> object:
    """The package-level count; the coverage dict never copies it."""

    return (side.get("package") or {}).get("unexamined_count")


def _findings(side: dict) -> dict[tuple[str, str], dict]:
    output: dict[tuple[str, str], dict] = {}
    for finding in (side.get("analysis") or {}).get("findings") or []:
        if not isinstance(finding, dict):
            continue
        output.setdefault(_finding_key(finding), deepcopy(finding))
    return output


def _questions(side: dict) -> list[str]:
    output: list[str] = []
    for question in (side.get("analysis") or {}).get("open_questions") or []:
        text = _normalized(question)
        if text and text not in output:
            output.append(text)
    return output


def _documents(side: dict) -> dict[tuple[str, str], dict]:
    output: dict[tuple[str, str], dict] = {}
    report_id = (side.get("report") or {}).get("report_id")
    for document in side.get("documents") or []:
        if not isinstance(document, dict):
            continue
        key = _document_key(document)
        entry = output.get(key)
        if entry is None:
            output[key] = {
                **deepcopy(document),
                "report_id": report_id,
                "chunk_ids": list(document.get("chunk_ids") or []),
                "fact_ids": list(document.get("fact_ids") or []),
            }
            continue
        # Several cited Chunks of one Document version stay one entry, but every
        # citation is kept so the reader can still reach each one.
        for field in ("chunk_ids", "fact_ids"):
            for value in document.get(field) or []:
                if value not in entry[field]:
                    entry[field].append(value)
    return output


def _facts(side: dict) -> dict[str, dict]:
    output: dict[str, dict] = {}
    report_id = (side.get("report") or {}).get("report_id")
    for fact in side.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        fact_id = fact.get("fact_id")
        key = str(fact_id) if fact_id else _canonical(fact)
        output.setdefault(key, {**deepcopy(fact), "report_id": report_id})
    return output


def _bounded(items: list) -> dict:
    return {
        "count": len(items),
        "items": items[:MAX_COMPARISON_ITEMS],
        "truncated": len(items) > MAX_COMPARISON_ITEMS,
    }


def _normalized(value: object) -> str:
    return " ".join(str(value or "").split())


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _hashable(value: object) -> object:
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_hashable(item) for item in value)
    return value


def _after(value: object, reference: object) -> bool:
    left = _timestamp(value)
    right = _timestamp(reference)
    if left is None or right is None:
        return False
    try:
        return left > right
    except TypeError:
        # A naive and an aware value cannot be ordered; the comparison stays
        # silent rather than guessing which one came later.
        return False


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
