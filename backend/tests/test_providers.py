import asyncio
import httpx
import pytest
from stocks.providers import ModelClient, ModelTarget

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
