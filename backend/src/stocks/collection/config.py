from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from stocks.collection.errors import CollectionError, ConfigurationError
from stocks.collection.urls import validate_https_url

MAX_CONFIG_BYTES = 1_000_000
CONTACT_USER_AGENT = re.compile(
    r"^[^\s()]+/\S+\s+\([^\s()]+@[^\s()]+\.[^\s()]+\)$"
)


def validate_user_agent(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("must not contain control characters")
    if not CONTACT_USER_AGENT.fullmatch(value):
        raise ValueError(
            "must identify the client and include a contact email in parentheses"
        )
    return value


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=255)
    connector: Literal["rss"]
    enabled: bool = True
    publisher: str = Field(min_length=1, max_length=255)
    trust_tier: Literal["T1", "T2", "T3", "T4"]
    feed_url: str = Field(min_length=1, max_length=2048)
    timeout_seconds: float = Field(ge=1, le=120)
    max_response_bytes: int = Field(ge=1_024, le=20_000_000)
    max_redirects: int = Field(ge=0, le=10)
    max_items: int = Field(ge=1, le=200)
    user_agent: str = Field(min_length=1, max_length=255)

    @field_validator("feed_url")
    @classmethod
    def validate_feed_url(cls, value: str) -> str:
        try:
            validate_https_url(value)
        except CollectionError as exc:
            raise ValueError(exc.message) from exc
        return value

    @field_validator("name", "publisher", "user_agent")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("user_agent")
    @classmethod
    def validate_contact_user_agent(cls, value: str) -> str:
        return validate_user_agent(value)


def load_sources(path: str | Path) -> list[SourceConfig]:
    config_path = Path(path)
    try:
        size = config_path.stat().st_size
    except OSError as exc:
        raise ConfigurationError(
            "CONFIG_UNAVAILABLE",
            "Source configuration file cannot be read",
        ) from exc
    if size > MAX_CONFIG_BYTES:
        raise ConfigurationError("CONFIG_TOO_LARGE", "Source configuration file is too large")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(
            "CONFIG_INVALID",
            "Source configuration is not valid YAML",
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"sources"}:
        raise ConfigurationError(
            "CONFIG_INVALID",
            "Source configuration must contain only a sources list",
        )
    raw_sources = payload["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigurationError("CONFIG_INVALID", "sources must be a non-empty list")

    sources: list[SourceConfig] = []
    seen: set[str] = set()
    for raw_source in raw_sources:
        try:
            source = SourceConfig.model_validate(raw_source)
        except ValidationError as exc:
            raise ConfigurationError(
                "CONFIG_INVALID",
                "Source configuration entry is invalid",
            ) from exc
        if source.source_id in seen:
            raise ConfigurationError(
                "DUPLICATE_SOURCE",
                "Source identifiers must be unique",
                details={"source_id": source.source_id},
            )
        seen.add(source.source_id)
        sources.append(source)
    return sources


def select_sources(
    sources: list[SourceConfig], source_ids: list[str] | None = None
) -> list[SourceConfig]:
    by_id = {source.source_id: source for source in sources}
    if source_ids:
        missing = [source_id for source_id in source_ids if source_id not in by_id]
        if missing:
            raise ConfigurationError(
                "SOURCE_NOT_FOUND",
                "Configured source was not found",
                details={"source_id": missing[0]},
            )
        selected = [by_id[source_id] for source_id in source_ids]
    else:
        selected = [source for source in sources if source.enabled]
    disabled = [source.source_id for source in selected if not source.enabled]
    if disabled:
        raise ConfigurationError(
            "SOURCE_DISABLED",
            "Configured source is disabled",
            details={"source_id": disabled[0]},
        )
    if not selected:
        raise ConfigurationError("SOURCE_NOT_FOUND", "No enabled sources are configured")
    return selected
