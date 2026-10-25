"""Configurable compatible HTTP model calls with bounded ordered fallback."""
import asyncio
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx
import yaml

from stocks.embedding_space import VECTOR_DIMENSIONS

RETRYABLE_STATUS = {402, 408, 429, 500, 502, 503, 504}
PLACEHOLDER = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")
CONFIG_SECTIONS = {"providers", "models", "embedding", "agents"}
POSTGRES_INTEGER_MAX = 2_147_483_647
# Provider-reported cost is stored in ``numeric(14, 6)``; anything beyond this
# is a provider defect, not a real single call.
MAX_MODEL_COST_USD = Decimal("1000000")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(
            None,
            None,
            "expected a mapping node",
            node.start_mark,
        )
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def resolve_env(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Model configuration value must be a non-empty string")
    match = PLACEHOLDER.fullmatch(value)
    if not match:
        return value
    result = os.getenv(match.group(1))
    if not result:
        raise ValueError(f"Missing environment variable {match.group(1)}")
    return result


@dataclass(frozen=True)
class ModelTarget:
    base_url: str
    api_key: str
    name: str
    dimensions: int | None = None
    capabilities: tuple[str, ...] = ("chat", "text")
    timeout_seconds: float = 60
    retryable_statuses: tuple[int, ...] = tuple(sorted(RETRYABLE_STATUS))
    provider: str | None = None


@dataclass(frozen=True)
class ChatRouting:
    primary: str
    fallbacks: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    timeout_seconds: float
    max_attempts: int
    retry_backoff_seconds: float

    @property
    def aliases(self) -> tuple[str, ...]:
        return (self.primary, *self.fallbacks)


@dataclass(frozen=True)
class ModelAttempt:
    attempt_number: int
    attempt_alias: str
    attempt_model_name: str
    reported_model_name: str | None
    status: str
    duration_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: Decimal | None = None
    fallback_reason: str | None = None
    error: dict[str, str] | None = None

    def to_dict(self) -> dict:
        return {
            "attempt_number": self.attempt_number,
            "attempt_alias": self.attempt_alias,
            "attempt_model_name": self.attempt_model_name,
            "reported_model_name": self.reported_model_name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": (
                str(self.cost_usd) if self.cost_usd is not None else None
            ),
            "fallback_reason": self.fallback_reason,
            "error": self.error,
        }


@dataclass(frozen=True)
class ChatResponse:
    model_alias: str
    requested_model_name: str
    reported_model_name: str | None
    content: str
    usage: dict[str, int | None]
    attempts: tuple[ModelAttempt, ...]
    # Provider-reported cost for the accepted attempt, when the response
    # carried one. Unknown cost stays ``None``; Stocks never prices tokens.
    cost_usd: Decimal | None = None


class ModelError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        attempts: tuple[ModelAttempt, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.attempts = attempts

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


@dataclass(frozen=True)
class EmbeddingTargets:
    aliases: tuple[str, ...]
    targets: tuple[ModelTarget, ...]

    def __post_init__(self) -> None:
        if not self.aliases or len(self.aliases) != len(self.targets):
            raise ValueError("Embedding routing is incomplete")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("Embedding model aliases must be unique")

    @property
    def primary(self) -> str:
        return self.aliases[0]

    @property
    def fallbacks(self) -> list[str]:
        return list(self.aliases[1:])

    @property
    def model_name(self) -> str:
        return self.targets[0].name

    @property
    def dimensions(self) -> int:
        dimensions = self.targets[0].dimensions
        if dimensions is None:
            raise ValueError("Embedding model dimensions are not configured")
        return dimensions


@dataclass(frozen=True)
class EmbeddingResponse:
    model_alias: str
    embeddings: tuple[tuple[float, ...], ...]


class EmbeddingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as stream:
            config = yaml.load(stream, Loader=_UniqueKeySafeLoader) or {}
    except yaml.YAMLError as exc:
        raise ValueError("Model configuration is not valid YAML") from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError("Model configuration cannot be read") from exc
    if not isinstance(config, dict):
        raise ValueError("Model configuration must be a mapping")
    unknown_sections = set(config) - CONFIG_SECTIONS
    if unknown_sections:
        raise ValueError(
            f"Unknown model configuration section: {sorted(unknown_sections)[0]}"
        )
    for section in CONFIG_SECTIONS & set(config):
        if not isinstance(config[section], dict):
            raise ValueError(
                f"Model configuration section {section} must be a mapping"
            )
        if any(
            not isinstance(key, str) or not key
            for key in config[section]
        ):
            raise ValueError(
                f"Model configuration section {section} has an invalid name"
            )
    return config


def _load_target(config: dict, alias: str) -> ModelTarget:
    if not isinstance(alias, str) or not alias or len(alias) > 120:
        raise ValueError("Invalid model alias")
    models = config.get("models")
    item = models.get(alias) if isinstance(models, dict) else None
    if not isinstance(item, dict):
        raise ValueError(f"Invalid model or provider configuration for {alias}")
    provider_alias = item.get("provider")
    providers = config.get("providers")
    provider = (
        providers.get(provider_alias)
        if isinstance(providers, dict) and isinstance(provider_alias, str)
        else None
    )
    if not isinstance(provider, dict):
        raise ValueError(f"Unknown model or provider reference for {alias}")
    unknown_item_fields = set(item) - {
        "provider",
        "name",
        "dimensions",
        "capabilities",
        "timeout_seconds",
        "retryable_statuses",
    }
    if unknown_item_fields:
        raise ValueError(
            f"Unknown model field for {alias}: {sorted(unknown_item_fields)[0]}"
        )
    missing_model_fields = {"provider", "name"} - set(item)
    if missing_model_fields:
        raise ValueError(
            f"Model {alias} is missing {sorted(missing_model_fields)[0]}"
        )
    unknown_provider_fields = set(provider) - {
        "protocol",
        "base_url",
        "api_key",
        "capabilities",
        "timeout_seconds",
        "retryable_statuses",
    }
    if unknown_provider_fields:
        raise ValueError(
            f"Unknown provider field for {provider_alias}: "
            f"{sorted(unknown_provider_fields)[0]}"
        )
    missing_provider_fields = {"protocol", "base_url", "api_key"} - set(provider)
    if missing_provider_fields:
        raise ValueError(
            f"Provider {provider_alias} is missing "
            f"{sorted(missing_provider_fields)[0]}"
        )
    if provider["protocol"] != "openai_compatible":
        raise ValueError(f"Unsupported protocol for model {alias}")
    base_url = resolve_env(provider["base_url"]).rstrip("/")
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError(f"Invalid base URL for model {alias}")
    raw_dimensions = item.get("dimensions")
    dimensions = None
    if raw_dimensions is not None:
        if isinstance(raw_dimensions, bool):
            raise ValueError(f"Invalid dimensions for model {alias}")
        try:
            dimensions = int(resolve_env(str(raw_dimensions)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid dimensions for model {alias}") from exc
    if dimensions is not None:
        if dimensions < 1:
            raise ValueError(f"Invalid dimensions for model {alias}")
    capabilities = item.get(
        "capabilities",
        provider.get("capabilities", ["chat", "text"]),
    )
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or any(not isinstance(value, str) or not value for value in capabilities)
        or len(set(capabilities)) != len(capabilities)
    ):
        raise ValueError(f"Invalid capabilities for model {alias}")
    raw_timeout = item.get(
        "timeout_seconds",
        provider.get("timeout_seconds", 60),
    )
    if isinstance(raw_timeout, bool):
        raise ValueError(f"Invalid timeout for model {alias}")
    try:
        timeout_seconds = float(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid timeout for model {alias}") from exc
    if not 0 < timeout_seconds <= 600:
        raise ValueError(f"Invalid timeout for model {alias}")
    raw_statuses = item.get(
        "retryable_statuses",
        provider.get("retryable_statuses", sorted(RETRYABLE_STATUS)),
    )
    if (
        not isinstance(raw_statuses, list)
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value not in RETRYABLE_STATUS
            for value in raw_statuses
        )
        or len(set(raw_statuses)) != len(raw_statuses)
    ):
        raise ValueError(f"Invalid retryable statuses for model {alias}")
    api_key = resolve_env(provider["api_key"])
    model_name = resolve_env(item["name"])
    if not api_key or not model_name or len(model_name) > 255:
        raise ValueError(f"Invalid model credentials or name for {alias}")
    return ModelTarget(
        base_url=base_url,
        api_key=api_key,
        name=model_name,
        dimensions=dimensions,
        capabilities=tuple(capabilities),
        timeout_seconds=timeout_seconds,
        retryable_statuses=tuple(raw_statuses),
        provider=provider_alias,
    )


def load_models(path: str) -> dict[str, ModelTarget]:
    config = _load_config(path)
    return {
        alias: _load_target(config, alias)
        for alias in config.get("models", {})
    }


def load_chat_routing(
    path: str, agent_alias: str
) -> tuple[ChatRouting, dict[str, ModelTarget]]:
    config = _load_config(path)
    agents = config.get("agents")
    agent = agents.get(agent_alias) if isinstance(agents, dict) else None
    if not isinstance(agent, dict) or not isinstance(agent.get("routing"), dict):
        raise ValueError(f"Unknown Agent routing reference: {agent_alias}")
    unknown_agent_fields = set(agent) - {"routing", "protocol"}
    if unknown_agent_fields:
        raise ValueError(
            f"Unknown Agent field for {agent_alias}: "
            f"{sorted(unknown_agent_fields)[0]}"
        )
    if "protocol" in agent:
        protocol = agent["protocol"]
        if not isinstance(protocol, str) or not protocol.strip():
            raise ValueError(f"Agent {agent_alias} protocol must be a non-empty string")
    route = agent["routing"]
    unknown = set(route) - {
        "primary",
        "fallbacks",
        "required_capabilities",
        "timeout_seconds",
        "retries",
        "retry_backoff_seconds",
    }
    if unknown:
        raise ValueError(
            f"Unknown routing field for Agent {agent_alias}: {sorted(unknown)[0]}"
        )
    primary = route.get("primary")
    fallbacks = route.get("fallbacks", [])
    required = route.get("required_capabilities", [])
    if not isinstance(primary, str) or not primary:
        raise ValueError(f"Agent {agent_alias} routing requires a primary model")
    if (
        not isinstance(fallbacks, list)
        or any(not isinstance(alias, str) or not alias for alias in fallbacks)
    ):
        raise ValueError(f"Agent {agent_alias} fallbacks must be a list of aliases")
    aliases = (primary, *fallbacks)
    if len(set(aliases)) != len(aliases):
        raise ValueError(f"Agent {agent_alias} routing aliases must be unique")
    if (
        not isinstance(required, list)
        or any(not isinstance(value, str) or not value for value in required)
        or len(set(required)) != len(required)
    ):
        raise ValueError(
            f"Agent {agent_alias} required capabilities must be a list"
        )
    if not {"chat", "structured_output"}.issubset(required):
        raise ValueError(
            f"Agent {agent_alias} routing must require chat and "
            "structured_output capabilities"
        )
    raw_timeout = route.get("timeout_seconds", 60)
    raw_backoff = route.get("retry_backoff_seconds", 0)
    if isinstance(raw_timeout, bool) or isinstance(raw_backoff, bool):
        raise ValueError(
            f"Agent {agent_alias} timeout or retry backoff is invalid"
        )
    try:
        timeout_seconds = float(raw_timeout)
        retry_backoff = float(raw_backoff)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Agent {agent_alias} timeout or retry backoff is invalid"
        ) from exc
    retries = route.get("retries", 0)
    if not 0 < timeout_seconds <= 600:
        raise ValueError(f"Agent {agent_alias} timeout is invalid")
    if (
        not isinstance(retries, int)
        or isinstance(retries, bool)
        or not 0 <= retries <= 5
    ):
        raise ValueError(f"Agent {agent_alias} retries must be between 0 and 5")
    if not 0 <= retry_backoff <= 30:
        raise ValueError(f"Agent {agent_alias} retry backoff is invalid")
    required_payloads = {
        "chat": "Chat-compatible JSON output",
        "text": "Text generation",
        "structured_output": "JSON Schema structured output",
    }
    missing_capability = set(required) - set(required_payloads)
    if missing_capability:
        raise ValueError(
            f"Unsupported required capability: {sorted(missing_capability)[0]}"
        )
    targets = {alias: _load_target(config, alias) for alias in aliases}
    for alias, target in targets.items():
        missing = set(required) - set(target.capabilities)
        if missing:
            raise ValueError(
                f"Model {alias} lacks required capabilities: "
                f"{sorted(missing)[0]}"
            )
    return (
        ChatRouting(
            primary=primary,
            fallbacks=tuple(fallbacks),
            required_capabilities=tuple(required),
            timeout_seconds=timeout_seconds,
            max_attempts=retries + 1,
            retry_backoff_seconds=retry_backoff,
        ),
        targets,
    )


def model_route_snapshot(
    routing: ChatRouting,
    targets: dict[str, ModelTarget],
    *,
    model_ref: str | None = None,
) -> dict:
    return {
        "routing": {
            "primary": routing.primary,
            "fallbacks": list(routing.fallbacks),
            "required_capabilities": list(routing.required_capabilities),
            "timeout_seconds": routing.timeout_seconds,
            "max_attempts": routing.max_attempts,
            "retry_backoff_seconds": routing.retry_backoff_seconds,
        },
        "targets": [
            {
                "alias": alias,
                "provider": targets[alias].provider,
                "model_name": targets[alias].name,
                "endpoint_hash": _endpoint_hash(targets[alias].base_url),
                "capabilities": list(targets[alias].capabilities),
                "timeout_seconds": targets[alias].timeout_seconds,
                "retryable_statuses": list(targets[alias].retryable_statuses),
            }
            for alias in routing.aliases
        ],
        "model_ref": model_ref or routing.primary,
    }


def load_embedding_targets(path: str) -> EmbeddingTargets:
    config = _load_config(path)
    embedding = config.get("embedding")
    raw_aliases = (
        embedding.get("models") if isinstance(embedding, dict) else None
    )
    if not isinstance(raw_aliases, list):
        raise ValueError("Embedding models must be configured")
    unknown_embedding_fields = set(embedding) - {"models"}
    if unknown_embedding_fields:
        raise ValueError(
            "Unknown embedding configuration field: "
            f"{sorted(unknown_embedding_fields)[0]}"
        )
    aliases = tuple(raw_aliases)
    if not aliases or any(not isinstance(alias, str) or not alias for alias in aliases):
        raise ValueError("Embedding models must contain at least one alias")
    if len(set(aliases)) != len(aliases):
        raise ValueError("Embedding model aliases must be unique")

    targets = tuple(_load_target(config, alias) for alias in aliases)
    expected = (targets[0].name, targets[0].dimensions)
    if expected[1] is None:
        raise ValueError("Embedding model dimensions must be configured")
    if expected[1] != VECTOR_DIMENSIONS:
        raise ValueError(
            f"Embedding model dimensions must match {VECTOR_DIMENSIONS}"
        )
    if any((target.name, target.dimensions) != expected for target in targets):
        raise ValueError("Embedding fallbacks must use the same model and dimensions")
    return EmbeddingTargets(aliases=aliases, targets=targets)


def embedding_route_snapshot(targets: EmbeddingTargets) -> dict:
    return {
        "aliases": list(targets.aliases),
        "targets": [
            {
                "alias": alias,
                "provider": target.provider,
                "model_name": target.name,
                "endpoint_hash": _endpoint_hash(target.base_url),
                "dimensions": target.dimensions,
                "capabilities": list(target.capabilities),
                "timeout_seconds": target.timeout_seconds,
                "retryable_statuses": list(target.retryable_statuses),
            }
            for alias, target in zip(
                targets.aliases, targets.targets, strict=True
            )
        ],
    }


def _endpoint_hash(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if "?" in normalized or "#" in normalized:
        raise ValueError("Model endpoint must not contain query or fragment")
    return hashlib.sha256(normalized.encode()).hexdigest()


def _reported_model_matches(reported: str, requested: str) -> bool:
    """Whether a provider-reported model name identifies the requested one.

    Some gateways namespace a model by provider (``workbuddy/bge-m3``) and echo
    back only the bare id (``bge-m3``). Accept that suffix form, but keep
    rejecting anything that does not name the same model, so a silently
    substituted model is still caught.
    """

    if reported == requested:
        return True
    _, separator, bare = requested.rpartition("/")
    return bool(separator and bare) and reported == bare


def _parse_embeddings(
    payload: object, *, count: int, dimensions: int, model_name: str
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(payload, dict):
        raise EmbeddingError(
            "EMBEDDING_RESPONSE_INVALID",
            "Embedding provider returned an invalid response",
        )
    response_model = payload.get("model")
    if response_model is not None and not _reported_model_matches(
        response_model, model_name
    ):
        raise EmbeddingError(
            "EMBEDDING_MODEL_MISMATCH",
            "Embedding provider returned a different model",
        )
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != count:
        raise EmbeddingError(
            "EMBEDDING_RESPONSE_INVALID",
            "Embedding provider returned an unexpected number of vectors",
        )
    vectors: list[tuple[float, ...] | None] = [None] * count
    # `index` positions each vector, so a reordered response is still usable.
    # A provider may omit it (some gateways drop it for the first entry); the
    # position in `data` then has to stand in, which the OpenAI schema defines
    # as the order of the inputs. An entry may not claim a slot another entry
    # already filled, so a contradictory response is still rejected.
    active_indexes = set(range(count))
    for position, item in enumerate(data):
        if not isinstance(item, dict):
            raise EmbeddingError(
                "EMBEDDING_RESPONSE_INVALID",
                "Embedding provider returned invalid vector indexes",
            )
        if "index" in item:
            raw_index = item["index"]
            if (
                not isinstance(raw_index, int)
                or isinstance(raw_index, bool)
                or raw_index not in active_indexes
            ):
                raise EmbeddingError(
                    "EMBEDDING_RESPONSE_INVALID",
                    "Embedding provider returned invalid vector indexes",
                )
            index = raw_index
        else:
            # No index: fall back to the position, but only if that slot is
            # still free, otherwise the ordering is genuinely ambiguous.
            if position not in active_indexes:
                raise EmbeddingError(
                    "EMBEDDING_RESPONSE_INVALID",
                    "Embedding provider returned invalid vector indexes",
                )
            index = position
        active_indexes.remove(index)
        raw_vector = item.get("embedding")
        if not isinstance(raw_vector, list) or len(raw_vector) != dimensions:
            raise EmbeddingError(
                "EMBEDDING_DIMENSION_MISMATCH",
                "Embedding provider returned incompatible vector dimensions",
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in raw_vector
        ):
            raise EmbeddingError(
                "EMBEDDING_RESPONSE_INVALID",
                "Embedding provider returned invalid vector values",
            )
        vectors[index] = tuple(float(value) for value in raw_vector)
    assert all(vector is not None for vector in vectors)
    return tuple(vector for vector in vectors if vector is not None)


def _http_error(status_code: int) -> dict[str, str]:
    if status_code in {401, 403}:
        code = "MODEL_AUTHENTICATION_FAILED"
    elif status_code == 402:
        code = "MODEL_QUOTA_EXHAUSTED"
    elif status_code == 429:
        code = "MODEL_RATE_LIMITED"
    elif status_code == 408:
        code = "MODEL_TIMEOUT"
    elif status_code >= 500:
        code = "MODEL_SERVICE_UNAVAILABLE"
    elif status_code in {400, 404, 422}:
        code = "MODEL_REQUEST_INVALID"
    else:
        code = "MODEL_REQUEST_REJECTED"
    return {
        "code": code,
        "message": "Model provider request failed",
        "status": str(status_code),
    }


async def _retry_delay(seconds: float) -> None:
    if seconds:
        await asyncio.sleep(seconds)


def _parse_chat_payload(payload: object, schema: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("invalid payload")
    reported_model = payload.get("model")
    if reported_model is not None and (
        not isinstance(reported_model, str)
        or not reported_model
        or len(reported_model) > 255
    ):
        raise ValueError("invalid reported model")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise ValueError("missing content")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("structured output must be an object")
    _validate_json_schema(parsed, schema)
    raw_usage = payload.get("usage", {})
    if raw_usage is None:
        raw_usage = {}
    if not isinstance(raw_usage, dict):
        raise ValueError("invalid usage")
    usage: dict[str, int | None] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw_usage.get(key)
        if value is not None and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > POSTGRES_INTEGER_MAX
        ):
            raise ValueError("invalid usage")
        usage[key] = value
    raw_cost = raw_usage.get("cost_usd")
    if raw_cost is None:
        raw_cost = raw_usage.get("cost")
    cost_usd: Decimal | None = None
    if raw_cost is not None:
        if isinstance(raw_cost, bool) or isinstance(raw_cost, str):
            raise ValueError("invalid usage")
        if not isinstance(raw_cost, (int, float)):
            raise ValueError("invalid usage")
        if isinstance(raw_cost, float) and not math.isfinite(raw_cost):
            raise ValueError("invalid usage")
        value = Decimal(str(raw_cost))
        if value < 0 or value > MAX_MODEL_COST_USD:
            raise ValueError("invalid usage")
        try:
            cost_usd = value.quantize(Decimal("0.000001"))
        except InvalidOperation as exc:
            raise ValueError("invalid usage") from exc
    return {
        "reported_model_name": reported_model,
        "content": content,
        "usage": usage,
        "cost_usd": cost_usd,
    }


def _validate_json_schema(value: object, schema: dict) -> None:
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                _validate_json_schema(value, option)
                break
            except ValueError:
                continue
        else:
            raise ValueError("value does not match any allowed schema")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("value is not in the allowed set")
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ValueError("expected object")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(
            key not in value for key in required
        ):
            raise ValueError("missing required field")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(
            key not in properties for key in value
        ):
            raise ValueError("unexpected field")
        for key, item_schema in properties.items():
            if key in value:
                _validate_json_schema(value[key], item_schema)
        return
    if schema_type == "array":
        if not isinstance(value, list) or len(value) < schema.get("minItems", 0):
            raise ValueError("expected array")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError("array too long")
        item_schema = schema.get("items", {})
        for item in value:
            _validate_json_schema(item, item_schema)
        return
    if schema_type == "string":
        if not isinstance(value, str):
            raise ValueError("expected string")
        return
    if schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("expected integer")
        return
    if schema_type == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("expected number")
        return
    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("expected boolean")
        return
    if schema_type == "null":
        if value is not None:
            raise ValueError("expected null")
        return


class ModelClient:
    def __init__(
        self,
        targets: dict[str, ModelTarget],
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.targets = targets
        self.transport = transport

    async def chat(
        self, primary: str, messages: list[dict], fallback: list[str] | None = None
    ) -> dict:
        names = [primary, *(fallback or [])]
        last_error: Exception | None = None
        for alias in names:
            target = self.targets[alias]
            async with httpx.AsyncClient(
                timeout=target.timeout_seconds,
                transport=self.transport,
            ) as client:
                try:
                    response = await client.post(
                        f"{target.base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {target.api_key}"},
                        json={"model": target.name, "messages": messages},
                    )
                    response.raise_for_status()
                    return {"model_alias": alias, "response": response.json()}
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in RETRYABLE_STATUS:
                        raise
                    last_error = exc
                except (httpx.ConnectError, httpx.TimeoutException) as exc:
                    last_error = exc
        raise RuntimeError("All configured model targets failed") from last_error

    async def research_chat(
        self,
        routing: ChatRouting,
        messages: list[dict],
        *,
        schema_name: str,
        schema: dict,
        validate_content: Callable[[str], object] | None = None,
    ) -> ChatResponse:
        if (
            not routing.primary
            or not routing.fallbacks == tuple(dict.fromkeys(routing.fallbacks))
            or not 0 < routing.timeout_seconds <= 600
            or not 1 <= routing.max_attempts <= 6
            or not 0 <= routing.retry_backoff_seconds <= 30
        ):
            raise ValueError("Research model routing is invalid")
        if (
            not isinstance(schema, dict)
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), dict)
        ):
            raise ValueError("Research output schema must be an object schema")
        aliases = routing.aliases
        try:
            targets = [self.targets[alias] for alias in aliases]
        except KeyError as exc:
            raise ValueError("Configured model alias cannot be resolved") from exc
        required_capabilities = {
            "chat",
            "structured_output",
            *routing.required_capabilities,
        }
        for target in targets:
            missing = required_capabilities - set(target.capabilities)
            if missing:
                raise ValueError(
                    "Configured fallback does not support required capabilities"
                )

        requested_alias = routing.primary
        requested_model_name = targets[0].name
        attempts: list[ModelAttempt] = []
        attempt_number = 0
        for alias_index, (alias, target) in enumerate(zip(aliases, targets)):
            # The routing timeout is the Agent-level budget for one attempt and
            # the reason `agents.research` declares 300s: one structured research
            # answer is much larger than a classification call. The per-target
            # timeout stays authoritative for plain chat, which has no routing
            # object to carry a different budget.
            request_timeout = max(target.timeout_seconds, routing.timeout_seconds)
            async with httpx.AsyncClient(
                timeout=request_timeout,
                transport=self.transport,
            ) as client:
                for retry_index in range(routing.max_attempts):
                    attempt_number += 1
                    started = time.monotonic()
                    parsed = None
                    try:
                        response = await client.post(
                            f"{target.base_url.rstrip('/')}/chat/completions",
                            headers={"Authorization": f"Bearer {target.api_key}"},
                            json={
                                "model": target.name,
                                "messages": messages,
                                "response_format": {
                                    "type": "json_schema",
                                    "json_schema": {
                                        "name": schema_name,
                                        "strict": True,
                                        "schema": schema,
                                    },
                                },
                            },
                        )
                        duration_ms = int((time.monotonic() - started) * 1000)
                        if response.status_code in target.retryable_statuses:
                            error = _http_error(response.status_code)
                            can_retry = retry_index + 1 < routing.max_attempts
                            attempts.append(
                                ModelAttempt(
                                    attempt_number=attempt_number,
                                    attempt_alias=alias,
                                    attempt_model_name=target.name,
                                    reported_model_name=None,
                                    status="failed",
                                    duration_ms=duration_ms,
                                    fallback_reason=(
                                        "retryable_status"
                                        if can_retry or alias_index + 1 < len(aliases)
                                        else None
                                    ),
                                    error=error,
                                )
                            )
                            if can_retry:
                                await _retry_delay(routing.retry_backoff_seconds)
                                continue
                            break
                        if response.status_code >= 400:
                            error = _http_error(response.status_code)
                            attempts.append(
                                ModelAttempt(
                                    attempt_number=attempt_number,
                                    attempt_alias=alias,
                                    attempt_model_name=target.name,
                                    reported_model_name=None,
                                    status="failed",
                                    duration_ms=duration_ms,
                                    error=error,
                                )
                            )
                            raise ModelError(
                                error["code"],
                                "Model provider rejected the request",
                                attempts=tuple(attempts),
                            )
                        payload = response.json()
                        parsed = _parse_chat_payload(payload, schema)
                        if validate_content is not None:
                            validate_content(parsed["content"])
                        attempts.append(
                            ModelAttempt(
                                attempt_number=attempt_number,
                                attempt_alias=alias,
                                attempt_model_name=target.name,
                                reported_model_name=parsed["reported_model_name"],
                                status="succeeded",
                                duration_ms=duration_ms,
                                prompt_tokens=parsed["usage"]["prompt_tokens"],
                                completion_tokens=parsed["usage"][
                                    "completion_tokens"
                                ],
                                total_tokens=parsed["usage"]["total_tokens"],
                                cost_usd=parsed["cost_usd"],
                            )
                        )
                        return ChatResponse(
                            model_alias=alias,
                            requested_model_name=requested_model_name,
                            reported_model_name=parsed["reported_model_name"],
                            content=parsed["content"],
                            usage=parsed["usage"],
                            attempts=tuple(attempts),
                            cost_usd=parsed["cost_usd"],
                        )
                    except ModelError as exc:
                        raise ModelError(
                            exc.code,
                            exc.message,
                            attempts=tuple(attempts),
                        ) from exc
                    except (ValueError, KeyError, TypeError) as exc:
                        duration_ms = int((time.monotonic() - started) * 1000)
                        can_retry = retry_index + 1 < routing.max_attempts
                        attempts.append(
                            ModelAttempt(
                                attempt_number=attempt_number,
                                attempt_alias=alias,
                                attempt_model_name=target.name,
                                reported_model_name=(
                                    parsed["reported_model_name"]
                                    if parsed is not None
                                    else None
                                ),
                                status="failed",
                                duration_ms=duration_ms,
                                prompt_tokens=(
                                    parsed["usage"]["prompt_tokens"]
                                    if parsed is not None
                                    else None
                                ),
                                completion_tokens=(
                                    parsed["usage"]["completion_tokens"]
                                    if parsed is not None
                                    else None
                                ),
                                total_tokens=(
                                    parsed["usage"]["total_tokens"]
                                    if parsed is not None
                                    else None
                                ),
                                cost_usd=(
                                    parsed["cost_usd"]
                                    if parsed is not None
                                    else None
                                ),
                                fallback_reason=(
                                    "invalid_response"
                                    if can_retry or alias_index + 1 < len(aliases)
                                    else None
                                ),
                                error={
                                    "code": "MODEL_RESPONSE_INVALID",
                                    "message": "Model response did not match the required schema",
                                },
                            )
                        )
                        if can_retry:
                            await _retry_delay(routing.retry_backoff_seconds)
                            continue
                        break
                    except httpx.TimeoutException:
                        duration_ms = int((time.monotonic() - started) * 1000)
                        can_retry = retry_index + 1 < routing.max_attempts
                        error = {
                            "code": "MODEL_TIMEOUT",
                            "message": "Model provider request timed out",
                        }
                        attempts.append(
                            ModelAttempt(
                                attempt_number=attempt_number,
                                attempt_alias=alias,
                                attempt_model_name=target.name,
                                reported_model_name=None,
                                status="failed",
                                duration_ms=duration_ms,
                                fallback_reason=(
                                    "timeout"
                                    if can_retry or alias_index + 1 < len(aliases)
                                    else None
                                ),
                                error=error,
                            )
                        )
                        if can_retry:
                            await _retry_delay(routing.retry_backoff_seconds)
                            continue
                        break
                    except (
                        httpx.ConnectError,
                        httpx.NetworkError,
                        httpx.RemoteProtocolError,
                    ) as exc:
                        duration_ms = int((time.monotonic() - started) * 1000)
                        can_retry = retry_index + 1 < routing.max_attempts
                        attempts.append(
                            ModelAttempt(
                                attempt_number=attempt_number,
                                attempt_alias=alias,
                                attempt_model_name=target.name,
                                reported_model_name=None,
                                status="failed",
                                duration_ms=duration_ms,
                                fallback_reason=(
                                    "connection_error"
                                    if can_retry or alias_index + 1 < len(aliases)
                                    else None
                                ),
                                error={
                                    "code": "MODEL_CONNECTION_FAILED",
                                    "message": "Model provider connection failed",
                                },
                            )
                        )
                        if can_retry:
                            await _retry_delay(routing.retry_backoff_seconds)
                            continue
                        break
                    except httpx.HTTPError as exc:
                        attempts.append(
                            ModelAttempt(
                                attempt_number=attempt_number,
                                attempt_alias=alias,
                                attempt_model_name=target.name,
                                reported_model_name=None,
                                status="failed",
                                duration_ms=int((time.monotonic() - started) * 1000),
                                error={
                                    "code": "MODEL_REQUEST_FAILED",
                                    "message": "Model provider request failed",
                                },
                            )
                        )
                        raise ModelError(
                            "MODEL_REQUEST_FAILED",
                            "Model provider request failed",
                            attempts=tuple(attempts),
                        ) from exc
        last_error = attempts[-1].error if attempts else None
        if last_error is None:
            raise ModelError(
                "MODEL_ROUTING_FAILED",
                "All configured model targets failed",
            )
        raise ModelError(
            last_error["code"],
            "All configured model targets failed",
            attempts=tuple(attempts),
        )

    async def embed(
        self,
        texts: Sequence[str],
        *,
        aliases: Sequence[str],
    ) -> EmbeddingResponse:
        inputs = list(texts)
        if not aliases:
            raise ValueError("At least one embedding target is required")
        if len(set(aliases)) != len(aliases):
            raise ValueError("Embedding target aliases must be unique")
        if not inputs or any(not text.strip() for text in inputs):
            raise ValueError("Embedding inputs must not be empty")

        try:
            targets = [self.targets[alias] for alias in aliases]
        except KeyError as exc:
            raise ValueError("Configured embedding alias cannot be resolved") from exc
        dimensions = targets[0].dimensions
        if dimensions is None:
            raise ValueError("Embedding model dimensions must be configured")
        if any(
            target.name != targets[0].name or target.dimensions != dimensions
            for target in targets
        ):
            raise ValueError(
                "Embedding fallbacks must use the same model and dimensions"
            )

        last_error: Exception | None = None
        for alias, target in zip(aliases, targets):
            # ``dimensions`` is an OpenAI-compatible request field. Providers that
            # support reduced-size vectors (for example Ark's
            # ``doubao-embedding-text`` models, whose default is larger than this
            # deployment's vector space) only return the configured size when the
            # request asks for it, so the validated dimension is always sent.
            # A provider that rejects the field is not supported: the dimension is
            # required here to validate the returned vector length.
            body: dict[str, object] = {
                "model": target.name,
                "input": inputs,
                "dimensions": dimensions,
            }
            async with httpx.AsyncClient(
                timeout=target.timeout_seconds,
                transport=self.transport,
            ) as client:
                try:
                    response = await client.post(
                        f"{target.base_url.rstrip('/')}/embeddings",
                        headers={"Authorization": f"Bearer {target.api_key}"},
                        json=body,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if (
                        exc.response.status_code
                        not in target.retryable_statuses
                    ):
                        raise EmbeddingError(
                            "EMBEDDING_REQUEST_FAILED",
                            "Embedding provider request failed",
                        ) from exc
                    last_error = exc
                    continue
                except (httpx.ConnectError, httpx.TimeoutException) as exc:
                    last_error = exc
                    continue
                except httpx.RequestError as exc:
                    if isinstance(
                        exc,
                        (
                            httpx.NetworkError,
                            httpx.TimeoutException,
                            httpx.RemoteProtocolError,
                        ),
                    ):
                        last_error = exc
                        continue
                    raise EmbeddingError(
                        "EMBEDDING_REQUEST_FAILED",
                        "Embedding provider request failed",
                    ) from exc
            try:
                payload = response.json()
            except ValueError as exc:
                raise EmbeddingError(
                    "EMBEDDING_RESPONSE_INVALID",
                    "Embedding provider returned invalid JSON",
                ) from exc
            return EmbeddingResponse(
                model_alias=alias,
                embeddings=_parse_embeddings(
                    payload,
                    count=len(inputs),
                    dimensions=dimensions,
                    model_name=target.name,
                ),
            )
        raise EmbeddingError(
            "EMBEDDING_REQUEST_FAILED",
            "All configured embedding targets failed",
        ) from last_error
