"""Turn a stored JSON or XML response into located, searchable values.

The stored response stays the evidence; this module only projects its structure
into one text line per value together with the exact locator that finds that
value again: a JSON Pointer for JSON and an element path for XML. Numbers and
units are kept exactly as the source wrote them (JSON numbers keep their
original scale), nothing is summarized or rewritten, and a malformed, duplicated
or oversized response fails the parse attempt instead of producing a partial
body.

Errors are raised as :class:`StructuredError` with a stable code; the parse
stage turns them into the persisted batch failure.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from defusedxml import ElementTree

MAX_STRUCTURED_DEPTH = 32
MAX_STRUCTURED_VALUES = 5_000
MAX_STRUCTURED_CHARACTERS = 4_000_000
MAX_PATH_CHARACTERS = 1_000
MAX_XML_ELEMENTS = 20_000
_PLAIN_PATH_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StructuredError(ValueError):
    """One structured original could not be projected into located values."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


def _reject_constant(token: str) -> Any:
    """JSON has no NaN or Infinity literal; never read one as a number."""

    raise StructuredError(
        "INVALID_JSON",
        "Stored response is not valid JSON",
        details={"detail": "non-finite numbers are not valid JSON"},
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key, value in pairs:
        if key in mapping:
            raise StructuredError(
                "DUPLICATE_JSON_KEY",
                "Stored response repeats a JSON object key",
            )
        mapping[key] = value
    return mapping


def _pointer_token(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _display_key(key: str) -> str:
    """A readable path component; the pointer stays the locator of record."""

    if _PLAIN_PATH_KEY.fullmatch(key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _display_path(parent: str, key: str, *, index: int | None) -> str:
    if index is None:
        component = _display_key(key)
        return f"{parent}.{component}" if parent else component
    return f"{parent or key}[{index}]"


class _Budget:
    """Bound one document's extracted values and characters."""

    def __init__(self) -> None:
        self.values = 0
        self.characters = 0

    def add(self, text: str) -> None:
        self.values += 1
        self.characters += len(text)
        if self.values > MAX_STRUCTURED_VALUES:
            raise StructuredError(
                "STRUCTURED_CONTENT_TOO_LARGE",
                "Structured response has more values than the parser allows",
                details={"limit_values": MAX_STRUCTURED_VALUES},
            )
        if self.characters > MAX_STRUCTURED_CHARACTERS:
            raise StructuredError(
                "STRUCTURED_CONTENT_TOO_LARGE",
                "Structured response is larger than the parser allows",
                details={"limit_characters": MAX_STRUCTURED_CHARACTERS},
            )


def _scalar_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, str)):
        return str(value)
    return None


def json_values(text: str) -> list[tuple[str, dict]]:
    """One ``path = value`` line per JSON leaf, located by JSON Pointer."""

    try:
        document = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except StructuredError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise StructuredError(
            "INVALID_JSON",
            "Stored response is not valid JSON",
        ) from exc
    budget = _Budget()
    output: list[tuple[str, dict]] = []
    stack: list[tuple[Any, str, str, int]] = [(document, "", "", 0)]
    while stack:
        value, pointer, display, depth = stack.pop()
        if isinstance(value, dict):
            if depth >= MAX_STRUCTURED_DEPTH:
                raise StructuredError(
                    "STRUCTURED_CONTENT_TOO_DEEP",
                    "Structured response nests deeper than the parser allows",
                    details={"limit_depth": MAX_STRUCTURED_DEPTH},
                )
            for key in reversed(list(value)):
                stack.append(
                    (
                        value[key],
                        f"{pointer}/{_pointer_token(key)}",
                        _display_path(display, key, index=None),
                        depth + 1,
                    )
                )
            continue
        if isinstance(value, list):
            if depth >= MAX_STRUCTURED_DEPTH:
                raise StructuredError(
                    "STRUCTURED_CONTENT_TOO_DEEP",
                    "Structured response nests deeper than the parser allows",
                    details={"limit_depth": MAX_STRUCTURED_DEPTH},
                )
            for index in reversed(range(len(value))):
                stack.append(
                    (
                        value[index],
                        f"{pointer}/{index}",
                        _display_path(display, "", index=index),
                        depth + 1,
                    )
                )
            continue
        scalar = _scalar_text(value)
        if scalar is None or not scalar.strip():
            continue
        if len(display) > MAX_PATH_CHARACTERS:
            raise StructuredError(
                "STRUCTURED_CONTENT_TOO_DEEP",
                "Structured response path is longer than the parser allows",
                details={"limit_characters": MAX_PATH_CHARACTERS},
            )
        if pointer == "":
            line = scalar
        else:
            line = f"{display} = {scalar}"
        budget.add(line)
        output.append(
            (
                line,
                {
                    "kind": "json_pointer",
                    "pointer": pointer,
                    "coordinate_unit": "json_values",
                },
            )
        )
    return output


def _element_path(parent: str, name: str, index: int | None) -> str:
    component = name if index is None else f"{name}[{index}]"
    return f"{parent}/{component}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def xml_values(text: str) -> list[tuple[str, dict]]:
    """One located value per XML element text or attribute."""

    try:
        root = ElementTree.fromstring(text)
    except Exception as exc:  # noqa: BLE001 - any XML or defusedxml failure
        raise StructuredError(
            "INVALID_XML",
            "Stored response is not a safely parsed XML document",
        ) from exc
    budget = _Budget()
    output: list[tuple[str, dict]] = []
    # Each entry carries the full path already computed by its parent, so a
    # repeated sibling keeps the position the parent assigned to it.
    stack: list[tuple[Any, str, int]] = [
        (root, _element_path("", _local_name(root.tag), None), 0)
    ]
    elements = 0
    while stack:
        element, current, depth = stack.pop()
        elements += 1
        if elements > MAX_XML_ELEMENTS:
            raise StructuredError(
                "STRUCTURED_CONTENT_TOO_LARGE",
                "XML document has more elements than the parser allows",
                details={"limit_elements": MAX_XML_ELEMENTS},
            )
        if depth > MAX_STRUCTURED_DEPTH:
            raise StructuredError(
                "STRUCTURED_CONTENT_TOO_DEEP",
                "XML document nests deeper than the parser allows",
                details={"limit_depth": MAX_STRUCTURED_DEPTH},
            )
        for attribute, value in element.attrib.items():
            if not isinstance(value, str) or not value.strip():
                continue
            budget.add(value)
            output.append(
                (
                    f"{current}@{_local_name(attribute)} = {value}",
                    {
                        "kind": "xml_path",
                        "path": current,
                        "attribute": _local_name(attribute),
                        "coordinate_unit": "xml_elements",
                    },
                )
            )
        body = element.text.strip() if isinstance(element.text, str) else ""
        if body:
            budget.add(body)
            output.append(
                (
                    f"{current} = {body}",
                    {
                        "kind": "xml_path",
                        "path": current,
                        "coordinate_unit": "xml_elements",
                    },
                )
            )
        children = list(element)
        totals: dict[str, int] = {}
        for child in children:
            child_name = _local_name(child.tag)
            totals[child_name] = totals.get(child_name, 0) + 1
        seen: dict[str, int] = {}
        for child in reversed(children):
            child_name = _local_name(child.tag)
            # Repeated siblings carry their 1-based position; unique ones do not.
            seen[child_name] = seen.get(child_name, 0) + 1
            position = totals[child_name] - seen[child_name] + 1
            stack.append(
                (
                    child,
                    _element_path(
                        current,
                        child_name,
                        position if totals[child_name] > 1 else None,
                    ),
                    depth + 1,
                )
            )
    return output
