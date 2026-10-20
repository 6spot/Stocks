import asyncio

import httpx
import pytest
from stocks.providers import EmbeddingError, ModelClient, ModelTarget


def test_retryable_status_uses_configured_backup():
    requests = []
    def mock(request):
        requests.append(str(request.url))
        if request.url.host == "primary.example":
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    targets = {
        "a": ModelTarget("https://primary.example/v1", "key", "one"),
        "b": ModelTarget("https://secondary.example/v1", "key", "two"),
    }
    response = asyncio.run(ModelClient(targets, httpx.MockTransport(mock)).chat(
        "a", [{"role": "user", "content": "hi"}], ["b"]
    ))
    assert response["model_alias"] == "b"
    assert len(requests) == 2

def test_authentication_errors_do_not_trigger_backup():
    requests = []
    def mock(request):
        requests.append(str(request.url))
        return httpx.Response(401)
    targets = {
        "a": ModelTarget("https://primary.example/v1", "key", "one"),
        "b": ModelTarget("https://secondary.example/v1", "key", "two"),
    }
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(ModelClient(targets, httpx.MockTransport(mock)).chat(
            "a", [{"role": "user", "content": "hi"}], ["b"]
        ))
    assert len(requests) == 1


def test_embedding_retryable_failure_uses_compatible_backup():
    requests = []

    def mock(request):
        requests.append(request.url.host)
        if request.url.host == "primary.example":
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]},
        )

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "embedding-model",
            3,
        ),
        "backup": ModelTarget(
            "https://secondary.example/v1",
            "key",
            "embedding-model",
            3,
        ),
    }
    response = asyncio.run(
        ModelClient(targets, httpx.MockTransport(mock)).embed(
            ["query"],
            aliases=["primary", "backup"],
        )
    )

    assert response.model_alias == "backup"
    assert response.embeddings == ((1.0, 0.0, 0.0),)
    assert requests == ["primary.example", "secondary.example"]


def test_embedding_rejects_incompatible_fallback_before_request():
    requests = []

    def mock(request):
        requests.append(request.url.host)
        return httpx.Response(200)

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "embedding-model",
            3,
        ),
        "incompatible": ModelTarget(
            "https://secondary.example/v1",
            "key",
            "other-model",
            3,
        ),
    }
    with pytest.raises(ValueError, match="same model"):
        asyncio.run(
            ModelClient(targets, httpx.MockTransport(mock)).embed(
                ["query"],
                aliases=["primary", "incompatible"],
            )
        )

    assert requests == []


def test_embedding_network_error_uses_compatible_backup():
    requests = []

    def mock(request):
        requests.append(request.url.host)
        if request.url.host == "primary.example":
            raise httpx.ReadError("connection dropped", request=request)
        return httpx.Response(
            200,
            json={
                "model": "embedding-model",
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
            },
        )

    targets = {
        "primary": ModelTarget(
            "https://primary.example/v1",
            "key",
            "embedding-model",
            3,
        ),
        "backup": ModelTarget(
            "https://secondary.example/v1",
            "key",
            "embedding-model",
            3,
        ),
    }
    response = asyncio.run(
        ModelClient(targets, httpx.MockTransport(mock)).embed(
            ["query"],
            aliases=["primary", "backup"],
        )
    )

    assert response.model_alias == "backup"
    assert response.embeddings == ((1.0, 0.0, 0.0),)
    assert requests == ["primary.example", "secondary.example"]


def test_embedding_rejects_response_from_another_model():
    target = ModelTarget(
        "https://example/v1",
        "key",
        "embedding-model",
        3,
    )

    def mock(request):
        return httpx.Response(
            200,
            json={
                "model": "other-model",
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
            },
        )

    with pytest.raises(EmbeddingError) as exc_info:
        asyncio.run(
            ModelClient(
                {"embedding": target},
                httpx.MockTransport(mock),
            ).embed(["query"], aliases=["embedding"])
        )

    assert exc_info.value.code == "EMBEDDING_MODEL_MISMATCH"
