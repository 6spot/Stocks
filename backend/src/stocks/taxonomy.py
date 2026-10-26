"""Controlled classification vocabulary owned by Stocks, not by a model vendor.

The taxonomy file is the only place label ids are defined: it produces the JSON
Schema the Decision Model has to answer, the deterministic validation of the
returned labels, and the version recorded with each stored classification. Adding
a label is a configuration change; changing an id or dropping one is a new
``version``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

import yaml

from stocks.providers import _UniqueKeySafeLoader

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_COMPANIES = 20
MAX_SYMBOLS = 20
MAX_REASON_LENGTH = 600

SECTIONS = (
    ("categories", "category", "categories"),
    ("event_types", "event_type", "event_types"),
    ("topics", "topics", "topics"),
    ("industries", "related_industries", "industries"),
    ("regions", "regions", "regions"),
)
VOCABULARY_SECTIONS = (
    "version",
    "instructions",
    "categories",
    "event_types",
    "topics",
    "industries",
    "regions",
)


@dataclass(frozen=True)
class Label:
    id: str
    name: str
    description: str
    aliases: tuple[str, ...] = ()

    def candidate_payload(self) -> dict:
        """Shared model-facing semantics; only id is a valid output value."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "aliases": list(self.aliases),
        }

    def candidate_text(self) -> str:
        return json.dumps(self.candidate_payload(), ensure_ascii=False)


@dataclass(frozen=True)
class Taxonomy:
    """A validated vocabulary plus the derived Decision Model contract."""

    version: str
    instructions: str
    categories: tuple[Label, ...]
    event_types: tuple[Label, ...]
    topics: tuple[Label, ...]
    industries: tuple[Label, ...]
    regions: tuple[Label, ...]

    @property
    def category_ids(self) -> tuple[str, ...]:
        return tuple(label.id for label in self.categories)

    @property
    def event_type_ids(self) -> tuple[str, ...]:
        return tuple(label.id for label in self.event_types)

    @property
    def topic_ids(self) -> tuple[str, ...]:
        return tuple(label.id for label in self.topics)

    @property
    def industry_ids(self) -> tuple[str, ...]:
        return tuple(label.id for label in self.industries)

    @property
    def region_ids(self) -> tuple[str, ...]:
        return tuple(label.id for label in self.regions)

    def candidate_payload(self) -> dict:
        """Preserve candidate order for prompts and vocabulary identity."""
        return {
            section: [label.candidate_payload() for label in getattr(self, section)]
            for section, _, _ in SECTIONS
        }

    def schema(self) -> dict:
        """The JSON Schema the Decision Model answer must satisfy."""

        def enumeration(values: Iterable[str]) -> dict:
            return {"type": "string", "enum": list(values)}

        def identifier() -> dict:
            return {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "pattern": "^[^\\s].*$",
            }

        return {
            "title": "document_classification",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "category",
                "topics",
                "related_industries",
                "regions",
                "companies",
                "symbols",
                "should_extract",
                "reason",
            ],
            "properties": {
                "category": enumeration(self.category_ids),
                "event_type": {
                    "anyOf": [enumeration(self.event_type_ids), {"type": "null"}]
                },
                "topics": {
                    "type": "array",
                    "items": enumeration(self.topic_ids),
                },
                "related_industries": {
                    "type": "array",
                    "items": enumeration(self.industry_ids),
                },
                "regions": {
                    "type": "array",
                    "items": enumeration(self.region_ids),
                },
                "companies": {
                    "type": "array",
                    "maxItems": MAX_COMPANIES,
                    "items": identifier(),
                },
                "symbols": {
                    "type": "array",
                    "maxItems": MAX_SYMBOLS,
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 12,
                        "pattern": "^[A-Z][A-Z0-9.^-]*$",
                    },
                },
                "should_extract": {"type": "boolean"},
                "reason": {"type": "string", "minLength": 1},
            },
        }

    def normalize(self, payload: object) -> dict:
        """Validate one Decision Model answer against this vocabulary.

        Raises ``ClassificationInvalid`` for anything outside the controlled
        vocabulary: an unknown label, a duplicate, a wrong type, an unknown field
        or an empty reason. Nothing is coerced or silently dropped, because a
        silently accepted extra label would become a searchable tag nobody
        defined.
        """

        if not isinstance(payload, dict):
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID", "Classification result must be an object"
            )
        allowed = set(self.schema()["properties"])
        unknown = set(payload) - allowed
        if unknown:
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID",
                f"Classification result has an unknown field: {sorted(unknown)[0]}",
            )

        category = payload.get("category")
        if category not in self.category_ids:
            raise ClassificationInvalid(
                "CLASSIFICATION_OUT_OF_VOCABULARY",
                "Classification category is not in the controlled vocabulary",
            )
        event_type = payload.get("event_type")
        if event_type is not None and event_type not in self.event_type_ids:
            raise ClassificationInvalid(
                "CLASSIFICATION_OUT_OF_VOCABULARY",
                "Classification event type is not in the controlled vocabulary",
            )

        companies = _identifier_list(
            payload.get("companies"), "companies", MAX_COMPANIES
        )
        symbols = _symbol_list(payload.get("symbols"))
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID", "Classification result requires a reason"
            )
        if len(reason) > MAX_REASON_LENGTH:
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID", "Classification reason is too long"
            )
        should_extract = payload.get("should_extract")
        if not isinstance(should_extract, bool):
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID",
                "Classification must decide should_extract as a boolean",
            )

        return {
            "category": category,
            "event_type": event_type,
            "topics": _label_list(payload.get("topics"), "topics", self.topic_ids),
            "related_industries": _label_list(
                payload.get("related_industries"),
                "related_industries",
                self.industry_ids,
            ),
            "regions": _label_list(
                payload.get("regions"), "regions", self.region_ids
            ),
            "companies": companies,
            "symbols": symbols,
            "should_extract": should_extract,
            "reason": reason.strip(),
        }


class ClassificationInvalid(ValueError):
    """A Decision Model answer that does not satisfy the Stocks contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


def _label_list(value: object, field: str, allowed: tuple[str, ...]) -> list[str]:
    if not isinstance(value, list):
        raise ClassificationInvalid(
            "CLASSIFICATION_INVALID", f"Classification {field} must be a list"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in allowed:
            raise ClassificationInvalid(
                "CLASSIFICATION_OUT_OF_VOCABULARY",
                f"Classification {field} contains a value outside the vocabulary",
            )
        if item in result:
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID",
                f"Classification {field} contains a duplicate value",
            )
        result.append(item)
    return result


def _identifier_list(value: object, field: str, limit: int) -> list[str]:
    if not isinstance(value, list):
        raise ClassificationInvalid(
            "CLASSIFICATION_INVALID", f"Classification {field} must be a list"
        )
    if len(value) > limit:
        raise ClassificationInvalid(
            "CLASSIFICATION_INVALID", f"Classification {field} has too many entries"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 120:
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID",
                f"Classification {field} contains an invalid entry",
            )
        normalized = item.strip()
        if normalized in result:
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID",
                f"Classification {field} contains a duplicate entry",
            )
        result.append(normalized)
    return result


def _symbol_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ClassificationInvalid(
            "CLASSIFICATION_INVALID", "Classification symbols must be a list"
        )
    if len(value) > MAX_SYMBOLS:
        raise ClassificationInvalid(
            "CLASSIFICATION_INVALID", "Classification symbols has too many entries"
        )
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not 1 <= len(item) <= 12
            or not re.fullmatch(r"[A-Z][A-Z0-9.^-]*", item)
        ):
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID",
                "Classification symbols contains an invalid ticker",
            )
        if item in result:
            raise ClassificationInvalid(
                "CLASSIFICATION_INVALID",
                "Classification symbols contains a duplicate ticker",
            )
        result.append(item)
    return result


def _labels(raw: object, section: str, *, required: bool) -> tuple[Label, ...]:
    if raw is None and not required:
        return ()
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"Taxonomy section {section} must be a non-empty list")
    labels: list[Label] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"Taxonomy section {section} has an invalid entry")
        unknown = set(item) - {"id", "name", "description", "aliases"}
        if unknown:
            raise ValueError(
                f"Taxonomy section {section} has an unknown field: "
                f"{sorted(unknown)[0]}"
            )
        label_id = item.get("id")
        name = _label_text(item.get("name"), section, "name", 120)
        description = _label_text(item.get("description"), section, "description", 2000)
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or len(aliases) > 32:
            raise ValueError(f"Taxonomy section {section} has invalid aliases")
        aliases = tuple(_label_text(alias, section, "alias", 120) for alias in aliases)
        if len({alias.casefold() for alias in aliases}) != len(aliases):
            raise ValueError(f"Taxonomy section {section} has duplicate aliases")
        if not isinstance(label_id, str) or not IDENTIFIER.fullmatch(label_id):
            raise ValueError(f"Taxonomy section {section} has an invalid id")
        if label_id in seen:
            raise ValueError(f"Taxonomy section {section} has a duplicate id")
        seen.add(label_id)
        labels.append(Label(id=label_id, name=name, description=description, aliases=aliases))
    return tuple(labels)


def _label_text(value: object, section: str, field: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > limit
    ):
        raise ValueError(f"Taxonomy section {section} has an invalid {field}")
    return value


def parse_taxonomy(config: object) -> Taxonomy:
    """Validate one parsed taxonomy mapping."""

    if not isinstance(config, dict):
        raise ValueError("Taxonomy must be a mapping")
    unknown = set(config) - set(VOCABULARY_SECTIONS)
    if unknown:
        raise ValueError(f"Unknown taxonomy section: {sorted(unknown)[0]}")
    version = config.get("version")
    if not isinstance(version, str) or not version.strip() or len(version) > 60:
        raise ValueError("Taxonomy version must be a non-empty string")
    instructions = config.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("Taxonomy instructions must be a non-empty string")
    return Taxonomy(
        version=version.strip(),
        instructions=instructions.strip(),
        categories=_labels(config.get("categories"), "categories", required=True),
        event_types=_labels(config.get("event_types"), "event_types", required=False),
        topics=_labels(config.get("topics"), "topics", required=False),
        industries=_labels(config.get("industries"), "industries", required=False),
        regions=_labels(config.get("regions"), "regions", required=False),
    )


def load_taxonomy(path: str) -> Taxonomy:
    try:
        with open(path, encoding="utf-8") as stream:
            config = yaml.load(stream, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError("Taxonomy configuration is not valid YAML") from exc
    except OSError as exc:
        raise ValueError("Taxonomy configuration could not be read") from exc
    return parse_taxonomy(config or {})
