"""Configurable compatible HTTP model calls with bounded ordered fallback."""
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
import yaml

from stocks.embedding_space import VECTOR_DIMENSIONS

RETRYABLE_STATUS = {402, 429, 500, 502, 503, 504}
PLACEHOLDER = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


def resolve_env(value: str) -> str:
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


@dataclass(frozen=True)
class EmbeddingTargets:
    aliases: tuple[str, ...]
    targets: tuple[ModelTarget, ...]

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
    with open(path, encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def _load_target(config: dict, alias: str) -> ModelTarget:
    try:
        item = config["models"][alias]
        provider = config["providers"][item["provider"]]
    except KeyError as exc:
        raise ValueError(f"Unknown model or provider reference for {alias}") from exc
    if provider["protocol"] != "openai_compatible":
        raise ValueError(f"Unsupported protocol for model {alias}")
    dimensions = item.get("dimensions")
    if dimensions is not None:
        dimensions = int(resolve_env(str(dimensions)))
        if dimensions < 1:
            raise ValueError(f"Invalid dimensions for model {alias}")
    return ModelTarget(
        base_url=resolve_env(provider["base_url"]),
        api_key=resolve_env(provider["api_key"]),
        name=resolve_env(item["name"]),
        dimensions=dimensions,
    )


def load_models(path: str) -> dict[str, ModelTarget]:
    config = _load_config(path)
    return {
        alias: _load_target(config, alias)
        for alias in config.get("models", {})
    }


def load_embedding_targets(path: str) -> EmbeddingTargets:
    config = _load_config(path)
    try:
        aliases = tuple(config["embedding"]["models"])
    except KeyError as exc:
        raise ValueError("Embedding models must be configured") from exc
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


def _parse_embeddings(
    payload: object, *, count: int, dimensions: int, model_name: str
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(payload, dict):
        raise EmbeddingError(
            "EMBEDDING_RESPONSE_INVALID",
            "Embedding provider returned an invalid response",
        )
    response_model = payload.get("model")
    if response_model is not None and response_model != model_name:
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
    active_indexes = set(range(count))
    for item in data:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("index"), int)
            or isinstance(item.get("index"), bool)
            or item["index"] not in active_indexes
        ):
            raise EmbeddingError(
                "EMBEDDING_RESPONSE_INVALID",
                "Embedding provider returned invalid vector indexes",
            )
        index = item["index"]
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
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            for alias in names:
                target = self.targets[alias]
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

    async def embed(
        self,
        texts: Sequence[str],
        *,
        aliases: Sequence[str],
    ) -> EmbeddingResponse:
        inputs = list(texts)
        if not aliases:
            raise ValueError("At least one embedding target is required")
        if not inputs or any(not text.strip() for text in inputs):
            raise ValueError("Embedding inputs must not be empty")

        targets = [self.targets[alias] for alias in aliases]
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
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            for alias, target in zip(aliases, targets):
                try:
                    response = await client.post(
                        f"{target.base_url.rstrip('/')}/embeddings",
                        headers={"Authorization": f"Bearer {target.api_key}"},
                        json={"model": target.name, "input": inputs},
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in RETRYABLE_STATUS:
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
