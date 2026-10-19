"""Configurable compatible HTTP model calls with a minimal ordered fallback."""
import os
import re
from dataclasses import dataclass
import httpx
import yaml

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

def load_models(path: str) -> dict[str, ModelTarget]:
    with open(path, encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    result = {}
    for alias, item in config.get("models", {}).items():
        provider = config["providers"][item["provider"]]
        if provider["protocol"] != "openai_compatible":
            raise ValueError(f"Unsupported protocol for model {alias}")
        result[alias] = ModelTarget(
            base_url=resolve_env(provider["base_url"]),
            api_key=resolve_env(provider["api_key"]),
            name=resolve_env(item["name"]),
        )
    return result

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
