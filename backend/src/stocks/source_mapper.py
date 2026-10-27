"""Source Mapper: turn a stored structured response into Facts.

A configured API source keeps its raw response in S3 and as a Document like every
other source. This module reads that already-stored response and maps configured
field paths onto the generic Fact shape. It never downloads anything and never
calls a model: mapping is a deterministic program, so a numeric response needs no
classification and no vectors to become queryable.

Every mapped Fact cites the exact JSON pointer it was read from, so a reader can
resolve the value back to the raw bytes even when the response nested it several
levels deep. A field that is absent, null, or not a number stays unmapped rather
than being filled in: an unknown unit or a missing value must not turn into a
plausible-looking fact.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from stocks.facts import FactInput
from stocks.processing.parser import ParseFailure, decode_bytes

MAPPER_PROCESSOR_VERSION = "source-mapper-v1"
MAX_MAPPED_FACTS = 500
SUPPORTED_MEDIA_TYPES = frozenset({"application/json", "text/json"})


class MappingError(ValueError):
    """A structured response could not be mapped as configured."""

    def __init__(self, code: str, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


@dataclass(frozen=True)
class NumericField:
    """One numeric value to read from the response."""

    pointer: str
    fact_type: str
    unit: str
    metric_id: str | None = None
    subject_pointer: str | None = None
    symbol_pointer: str | None = None
    period_start_pointer: str | None = None
    period_end_pointer: str | None = None
    available_at_pointer: str | None = None
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TextField:
    """One textual statement to read from the response."""

    pointer: str
    fact_type: str
    metric_id: str | None = None
    subject_pointer: str | None = None
    symbol_pointer: str | None = None
    period_start_pointer: str | None = None
    period_end_pointer: str | None = None
    available_at_pointer: str | None = None
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SourceMapper:
    """A validated field mapping for one structured source."""

    id: str
    numeric_fields: tuple[NumericField, ...] = ()
    text_fields: tuple[TextField, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip() or len(self.id) > 120:
            raise MappingError(
                "MAPPER_INVALID", "Source mapper requires a non-empty id"
            )
        if not self.numeric_fields and not self.text_fields:
            raise MappingError(
                "MAPPER_INVALID", "Source mapper requires at least one field"
            )
        total = len(self.numeric_fields) + len(self.text_fields)
        if total > MAX_MAPPED_FACTS:
            raise MappingError(
                "MAPPER_INVALID",
                f"Source mapper supports at most {MAX_MAPPED_FACTS} fields",
            )
        pointers = [
            item.pointer for item in (*self.numeric_fields, *self.text_fields)
        ]
        if len(set(pointers)) != len(pointers):
            raise MappingError(
                "MAPPER_INVALID", "Source mapper field paths must be unique"
            )
        for item in (*self.numeric_fields, *self.text_fields):
            _validate_pointer(item.pointer)
            for pointer in (
                item.subject_pointer,
                item.symbol_pointer,
                item.period_start_pointer,
                item.period_end_pointer,
                item.available_at_pointer,
            ):
                if pointer is not None:
                    _validate_pointer(pointer)
            if not item.fact_type.strip() or len(item.fact_type) > 80:
                raise MappingError(
                    "MAPPER_INVALID", "Source mapper fact_type is invalid"
                )
            if item.metric_id is not None and (
                not item.metric_id.strip() or len(item.metric_id) > 120
            ):
                raise MappingError(
                    "MAPPER_INVALID", "Source mapper metric_id is invalid"
                )


def _validate_pointer(pointer: str) -> None:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise MappingError(
            "MAPPER_INVALID",
            "Source mapper field path must be a JSON pointer starting with '/'",
        )
    if len(pointer) > 512:
        raise MappingError("MAPPER_INVALID", "Source mapper field path is too long")


def resolve_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    """Resolve an RFC 6901 JSON pointer.

    Returns ``(found, value)``. A missing path is distinguished from a present
    ``null`` so a mapper can report an incomplete response instead of inventing a
    value for a field the source never actually provided.
    """

    if pointer == "":
        return True, document
    current = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, list):
            try:
                index = int(token)
            except ValueError:
                return False, None
            if index < 0 or index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def decode_json_response(payload: bytes, content_type: str) -> Any:
    """Strictly decode a stored structured response."""

    media_type = content_type.split(";")[0].strip().lower()
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise MappingError(
            "MAPPING_UNSUPPORTED_CONTENT_TYPE",
            "Source mapper only reads JSON responses",
            details={"content_type": media_type[:255]},
        )
    charset = None
    for part in content_type.split(";")[1:]:
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip("\"'")
    try:
        text, _ = decode_bytes(payload, charset)
    except ParseFailure as exc:
        raise MappingError(exc.code, exc.message, details=exc.details) from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise MappingError(
            "MAPPING_INVALID_JSON", "Stored response is not valid JSON"
        ) from exc


def map_response(
    document,
    payload: bytes,
    mapper: SourceMapper,
    *,
    created_at: datetime,
) -> tuple[FactInput, ...]:
    """Map one stored response onto Facts, citing each field path.

    A field that is configured but absent is reported as an incomplete mapping
    rather than silently dropped, because "the source did not return this metric"
    and "this metric does not exist" are different statements for research.
    """

    if created_at.utcoffset() is None:
        raise ValueError("created_at must include a timezone")
    body = decode_json_response(payload, document.content_type)
    facts: list[FactInput] = []
    missing: list[str] = []

    for item in mapper.numeric_fields:
        found, value = resolve_pointer(body, item.pointer)
        if not found or value is None:
            missing.append(item.pointer)
            continue
        number = _as_number(value)
        if number is None:
            # A non-numeric value at a numeric path is a source contract change,
            # not a value to guess at.
            raise MappingError(
                "MAPPING_VALUE_NOT_NUMERIC",
                "Configured numeric field did not contain a number",
                details={"pointer": item.pointer},
            )
        facts.append(
            _build_fact(
                document,
                body,
                item,
                value=number,
                statement=None,
                created_at=created_at,
            )
        )

    for item in mapper.text_fields:
        found, value = resolve_pointer(body, item.pointer)
        if not found or value is None:
            missing.append(item.pointer)
            continue
        if not isinstance(value, str) or not value.strip():
            raise MappingError(
                "MAPPING_VALUE_NOT_TEXT",
                "Configured text field did not contain non-empty text",
                details={"pointer": item.pointer},
            )
        facts.append(
            _build_fact(
                document,
                body,
                item,
                value=None,
                statement=value.strip(),
                created_at=created_at,
            )
        )

    if not facts:
        raise MappingError(
            "MAPPING_NO_FIELDS_MATCHED",
            "Stored response contained none of the configured fields",
            details={"mapper_id": mapper.id, "missing": missing[:10]},
        )
    return tuple(facts)


def _build_fact(
    document,
    body: Any,
    item,
    *,
    value: float | None,
    statement: str | None,
    created_at: datetime,
) -> FactInput:
    subject = _optional_text(body, item.subject_pointer)
    symbol = _optional_text(body, item.symbol_pointer)
    period_start = _optional_time(body, item.period_start_pointer)
    period_end = _optional_time(body, item.period_end_pointer)
    available_at = _optional_time(body, item.available_at_pointer)
    if available_at is None and document.published_at is not None:
        # The source's own publication time is what made the value knowable; the
        # collection time only says when Stocks happened to read it.
        available_at = document.published_at
    attributes = dict(item.attributes)
    if statement is None:
        attributes.setdefault("mapped_from", item.pointer)
    return FactInput(
        fact_type=item.fact_type,
        origin_method="source_mapping",
        evidence_ref={
            "kind": "json_pointer",
            "pointer": item.pointer,
            "content_hash": document.content_hash,
            "document_version": document.version,
        },
        subject_name=subject,
        symbol=symbol,
        metric_id=item.metric_id,
        statement=statement,
        value=value,
        unit=item.unit if value is not None else None,
        period_start=period_start,
        period_end=period_end,
        available_at=available_at,
        attributes=attributes,
    )


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        # A numeric string is still an unambiguous value; a string with units or
        # currency symbols in it is not, so it stays unmapped.
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _optional_text(body: Any, pointer: str | None) -> str | None:
    if pointer is None:
        return None
    found, value = resolve_pointer(body, pointer)
    if not found or not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:255]


def _optional_time(body: Any, pointer: str | None) -> datetime | None:
    if pointer is None:
        return None
    found, value = resolve_pointer(body, pointer)
    if not found or not isinstance(value, str) or not value.strip():
        return None
    parsed = _parse_time(value.strip())
    if parsed is None or parsed.utcoffset() is None:
        raise MappingError(
            "MAPPING_TIMESTAMP_INVALID",
            "Configured timestamp field is not a timezone-aware ISO 8601 value",
            details={"pointer": pointer},
        )
    return parsed


_PERIOD_PATTERNS = (
    # A monthly observation period such as "2026-08" starts at its first day.
    ("month", re.compile(r"^(\d{4})-(\d{2})$")),
    # An annual period such as "2026" starts at January 1st.
    ("year", re.compile(r"^(\d{4})$")),
)


def _parse_time(value: str) -> datetime | None:
    """Parse an instant, a date, or a coarse period boundary.

    A structured source commonly reports an observation period (`2026-08`) or an
    observation date (`2026-09-01`) rather than an instant. Storing them naively
    would let a caller silently compare them with timezone-aware values, so a
    period is anchored at the start of that period in UTC. Anything else that is
    not a full ISO 8601 timestamp is refused.
    """

    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is not None:
            return parsed
    except ValueError:
        parsed = None
    for kind, pattern in _PERIOD_PATTERNS:
        match = pattern.fullmatch(value)
        if match is None:
            continue
        parts = [int(item) for item in match.groups()]
        if kind == "month":
            year, month = parts
            if not 1 <= month <= 12:
                return None
            return datetime(year, month, 1, tzinfo=UTC)
        return datetime(parts[0], 1, 1, tzinfo=UTC)
    # A full calendar date is a daily observation period, not an instant: anchor
    # it at midnight UTC like the coarser month and year boundaries above.
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def parse_mapper(raw: object) -> SourceMapper:
    """Build one mapper from its configuration block."""

    if not isinstance(raw, dict):
        raise MappingError("MAPPER_INVALID", "Source mapper must be a mapping")
    unknown = set(raw) - {"id", "fields"}
    if unknown:
        raise MappingError(
            "MAPPER_INVALID", f"Unknown source mapper field: {sorted(unknown)[0]}"
        )
    mapper_id = raw.get("id")
    if not isinstance(mapper_id, str):
        raise MappingError("MAPPER_INVALID", "Source mapper requires an id")
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise MappingError(
            "MAPPER_INVALID", "Source mapper requires a non-empty fields list"
        )
    numeric: list[NumericField] = []
    textual: list[TextField] = []
    for item in raw_fields:
        if not isinstance(item, dict):
            raise MappingError("MAPPER_INVALID", "Source mapper field must be a mapping")
        kind = item.get("kind")
        allowed = {
            "kind",
            "pointer",
            "fact_type",
            "unit",
            "metric_id",
            "subject_pointer",
            "symbol_pointer",
            "period_start_pointer",
            "period_end_pointer",
            "available_at_pointer",
            "attributes",
        }
        unknown = set(item) - allowed
        if unknown:
            raise MappingError(
                "MAPPER_INVALID",
                f"Unknown source mapper field attribute: {sorted(unknown)[0]}",
            )
        pointer = item.get("pointer")
        fact_type = item.get("fact_type")
        if not isinstance(pointer, str) or not isinstance(fact_type, str):
            raise MappingError(
                "MAPPER_INVALID", "Source mapper field requires pointer and fact_type"
            )
        attributes = item.get("attributes", {})
        if not isinstance(attributes, dict):
            raise MappingError(
                "MAPPER_INVALID", "Source mapper field attributes must be a mapping"
            )
        common = {
            "pointer": pointer,
            "fact_type": fact_type,
            "metric_id": item.get("metric_id"),
            "subject_pointer": item.get("subject_pointer"),
            "symbol_pointer": item.get("symbol_pointer"),
            "period_start_pointer": item.get("period_start_pointer"),
            "period_end_pointer": item.get("period_end_pointer"),
            "available_at_pointer": item.get("available_at_pointer"),
            "attributes": attributes,
        }
        if kind == "numeric":
            unit = item.get("unit")
            if not isinstance(unit, str) or not unit.strip():
                # A numeric mapping without a declared unit cannot produce a
                # comparable fact, so the configuration is refused up front
                # instead of storing unit-less numbers.
                raise MappingError(
                    "MAPPER_INVALID",
                    "Numeric source mapper field requires an explicit unit",
                )
            numeric.append(NumericField(unit=unit.strip(), **common))
        elif kind == "text":
            textual.append(TextField(**common))
        else:
            raise MappingError(
                "MAPPER_INVALID", "Source mapper field kind must be numeric or text"
            )
    return SourceMapper(
        id=mapper_id,
        numeric_fields=tuple(numeric),
        text_fields=tuple(textual),
    )
