import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from stocks.providers import (
    ChatRouting,
    EmbeddingError,
    ModelClient,
    ModelError,
    ModelTarget,
)

ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


def answer_targets() -> dict:
    return {
        "a": ModelTarget(
            "https://primary.example/v1",
            "key",
            "one",
            capabilities=("chat", "text", "structured_output"),
        )
    }


def answer_routing() -> ChatRouting:
    return ChatRouting(
        primary="a",
        fallbacks=(),
        required_capabilities=("chat", "text", "structured_output"),
        timeout_seconds=5,
        max_attempts=1,
        retry_backoff_seconds=0,
    )


def answer_payload(usage: dict) -> dict:
    return {
        "model": "one",
        "choices": [{"message": {"content": json.dumps({"answer": "ok"})}}],
        "usage": usage,
    }


def research_answer(usage: dict):
    def handler(request):
        return httpx.Response(200, json=answer_payload(usage))

    return asyncio.run(
        ModelClient(
            answer_targets(), httpx.MockTransport(handler)
        ).research_chat(
            answer_routing(),
            [{"role": "user", "content": "hi"}],
            schema_name="answer",
            schema=ANSWER_SCHEMA,
        )
    )


def test_provider_reported_cost_is_kept_on_the_attempt():
    response = research_answer(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.001234,
        }
    )

    assert response.cost_usd == Decimal("0.001234")
    assert response.attempts[0].cost_usd == Decimal("0.001234")
    assert response.attempts[0].to_dict()["cost_usd"] == "0.001234"


def test_provider_without_a_cost_keeps_it_unknown():
    response = research_answer(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    )

    assert response.cost_usd is None
    assert response.attempts[0].cost_usd is None


def test_research_request_uses_the_routing_timeout_budget():
    """The Agent routing timeout is the per-attempt budget for research calls.

    ``agents.research.routing.timeout_seconds`` is configured at 300 in this
    deployment because one structured research answer is much larger than a
    classification call; the provider-level default (60s) must not silently
    cap it. httpx exposes the effective timeout as the request extension,
    so a handler can read it from the mock transport.
    """

    seen_timeouts = []

    def mock(request):
        timeout = request.extensions.get("timeout") or {}
        seen_timeouts.append(timeout.get("connect"))
        return httpx.Response(
            200,
            json={
                "model": "one",
                "choices": [
                    {"message": {"content": json.dumps({"answer": "ok"})}}
                ],
                "usage": {"total_tokens": 3},
            },
        )

    target = ModelTarget(
        "https://primary.example/v1",
        "key",
        "one",
        capabilities=("chat", "text", "structured_output"),
        timeout_seconds=60,
    )
    routing = ChatRouting(
        primary="a",
        fallbacks=(),
        required_capabilities=("chat", "text", "structured_output"),
        timeout_seconds=300,
        max_attempts=1,
        retry_backoff_seconds=0,
    )
    asyncio.run(
        ModelClient({"a": target}, httpx.MockTransport(mock)).research_chat(
            routing,
            [{"role": "user", "content": "hi"}],
            schema_name="answer",
            schema=ANSWER_SCHEMA,
        )
    )

    assert seen_timeouts == [300]


def test_provider_rejects_a_non_numeric_cost():
    with pytest.raises(ModelError) as excinfo:
        research_answer({"total_tokens": 15, "cost": "free"})

    assert excinfo.value.code == "MODEL_RESPONSE_INVALID"
    assert excinfo.value.attempts[0].cost_usd is None


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


def test_embedding_accepts_a_gateway_echoing_the_bare_model_id():
    """A gateway may namespace the request but echo the bare model id back.

    magpie takes `workbuddy/bge-m3` and reports `model: "bge-m3"`. It is the
    same model, so the response is usable.
    """

    target = ModelTarget("https://example/v1", "key", "workbuddy/bge-m3", 3)

    def mock(request):
        return httpx.Response(
            200,
            json={
                "model": "bge-m3",
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
            },
        )

    response = asyncio.run(
        ModelClient({"embedding": target}, httpx.MockTransport(mock)).embed(
            ["query"], aliases=["embedding"]
        )
    )

    assert response.embeddings == ((1.0, 0.0, 0.0),)


def test_embedding_still_rejects_a_bare_id_from_a_different_model():
    """Relaxing the namespace must not stop a real substitution being caught."""

    target = ModelTarget("https://example/v1", "key", "workbuddy/bge-m3", 3)

    def mock(request):
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
            },
        )

    with pytest.raises(EmbeddingError) as exc_info:
        asyncio.run(
            ModelClient({"embedding": target}, httpx.MockTransport(mock)).embed(
                ["query"], aliases=["embedding"]
            )
        )

    assert exc_info.value.code == "EMBEDDING_MODEL_MISMATCH"


def test_embedding_accepts_a_gateway_dropping_the_first_index():
    """magpie returns [None, 1, 2, ...]; positions still fix the order."""

    target = ModelTarget("https://example/v1", "key", "embedding-model", 3)

    def mock(request):
        return httpx.Response(
            200,
            json={
                "model": "embedding-model",
                "data": [
                    {"embedding": [1.0, 0.0, 0.0]},
                    {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                ],
            },
        )

    response = asyncio.run(
        ModelClient({"embedding": target}, httpx.MockTransport(mock)).embed(
            ["a", "b"], aliases=["embedding"]
        )
    )

    assert response.embeddings == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def test_embedding_rejects_two_vectors_claiming_the_same_slot():
    """Duplicate indexes leave a slot empty, so the order is unresolvable."""

    target = ModelTarget("https://example/v1", "key", "embedding-model", 3)

    def mock(request):
        return httpx.Response(
            200,
            json={
                "model": "embedding-model",
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    {"index": 0, "embedding": [0.0, 1.0, 0.0]},
                ],
            },
        )

    with pytest.raises(EmbeddingError) as exc_info:
        asyncio.run(
            ModelClient({"embedding": target}, httpx.MockTransport(mock)).embed(
                ["a", "b"], aliases=["embedding"]
            )
        )

    assert exc_info.value.code == "EMBEDDING_RESPONSE_INVALID"


def test_embedding_requests_the_configured_dimensions():
    """Reduced-size vectors must be asked for, not just validated afterwards.

    Ark's ``doubao-embedding-text`` models default to a larger vector than this
    deployment's space and only return the configured size when the request
    carries ``dimensions``.
    """

    seen: list[dict] = []

    def mock(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "doubao-embedding-text-240715",
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
            },
        )

    target = ModelTarget(
        "https://ark.example/api/v3", "key", "doubao-embedding-text-240715", 3
    )
    asyncio.run(
        ModelClient({"embedding": target}, httpx.MockTransport(mock)).embed(
            ["query"], aliases=["embedding"]
        )
    )

    assert seen == [
        {
            "model": "doubao-embedding-text-240715",
            "input": ["query"],
            "dimensions": 3,
        }
    ]


def test_embedding_rejects_an_unset_dimension_before_any_request():
    """The dimension is required: it validates the returned vector length.

    There is therefore no request body to send, and no ``null`` field is ever
    serialized.
    """

    seen: list[dict] = []

    def mock(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]},
        )

    target = ModelTarget("https://example/v1", "key", "embedding-model", None)
    with pytest.raises(ValueError, match="dimensions must be configured"):
        asyncio.run(
            ModelClient({"embedding": target}, httpx.MockTransport(mock)).embed(
                ["query"], aliases=["embedding"]
            )
        )

    assert seen == []
