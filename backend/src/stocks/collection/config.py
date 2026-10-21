from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from stocks.collection.errors import CollectionError, ConfigurationError
from stocks.collection.urls import validate_https_url
from stocks.source_mapper import MappingError, SourceMapper, parse_mapper

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
    """One configured source.

    A source either discovers articles through a feed (``rss``) or reads a
    structured HTTP API response (``http_api``). The API variant carries a
    ``mapper``: the field mapping that turns the stored response into Facts. It does
    not carry a feed URL, and the RSS variant carries no mapper, so a configuration
    cannot half-describe both.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=255)
    connector: Literal["rss", "http_api"]
    enabled: bool = True
    # Optional disclosure cadence for this Source. It is not a collection
    # schedule: a 15-minute poll may legitimately return no new items.
    expected_freshness: str | None = Field(
        default=None, pattern=r"^[1-9][0-9]{0,5}[mhd]$"
    )
    publisher: str = Field(min_length=1, max_length=255)
    trust_tier: Literal["T1", "T2", "T3", "T4"]
    feed_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_url: str | None = Field(default=None, min_length=1, max_length=2048)
    query: dict[str, str] = Field(default_factory=dict)
    mapper: SourceMapper | None = None
    timeout_seconds: float = Field(ge=1, le=120)
    max_response_bytes: int = Field(ge=1_024, le=20_000_000)
    max_redirects: int = Field(ge=0, le=10)
    max_items: int = Field(ge=1, le=200)
    user_agent: str = Field(min_length=1, max_length=255)

    @field_validator("feed_url", "api_url")
    @classmethod
    def validate_fetch_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            validate_https_url(value)
        except CollectionError as exc:
            raise ValueError(exc.message) from exc
        return value

    @field_validator("mapper", mode="before")
    @classmethod
    def build_mapper(cls, value: object) -> SourceMapper | None:
        if value is None or isinstance(value, SourceMapper):
            return value
        try:
            return parse_mapper(value)
        except MappingError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip() or len(key) > 120:
                raise ValueError("query parameter names must be non-empty")
            if not isinstance(item, str) or len(item) > 500:
                raise ValueError("query parameter values must be strings")
        return value

    def model_post_init(self, __context: object, /) -> None:
        if self.expected_freshness is not None:
            amount = int(self.expected_freshness[:-1])
            unit = self.expected_freshness[-1]
            if amount * {"m": 60, "h": 3600, "d": 86400}[unit] > 2_592_000:
                raise ValueError(
                    "expected_freshness must not exceed 30 days"
                )
        if self.connector == "rss":
            if not self.feed_url:
                raise ValueError("an rss source requires feed_url")
            if self.api_url is not None or self.mapper is not None or self.query:
                raise ValueError(
                    "an rss source must not configure api_url, query or mapper"
                )
        else:
            if not self.api_url:
                raise ValueError("an http_api source requires api_url")
            if self.mapper is None:
                raise ValueError("an http_api source requires a mapper")
            if self.feed_url is not None:
                raise ValueError("an http_api source must not configure feed_url")

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
