from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from stocks.collection.errors import CollectionError
from stocks.collection.urls import validate_https_url
from stocks.tools.errors import ToolAuthorizationError, ToolConfigurationError

MCP_PROTOCOL_VERSION = "2025-11-25"
DEFAULT_TOOLS_CONFIG_PATH = "config/tools.yaml"
MAX_CONFIG_BYTES = 1_000_000
MAX_SCHEMA_BYTES = 100_000
MAX_AUTHORIZED_TOOLS = 50
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SCHEMA_KEYWORDS = {
    "title",
    "description",
    "type",
    "additionalProperties",
    "required",
    "properties",
    "items",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "anyOf",
    "enum",
}
SCHEMA_TYPES = {
    "object",
    "array",
    "string",
    "integer",
    "number",
    "boolean",
    "null",
}


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
    mapping: dict[object, object] = {}
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


def validate_json_value(
    value: object,
    *,
    path: str,
    depth: int = 0,
    max_depth: int = 32,
    active: set[int] | None = None,
) -> None:
    if depth > max_depth:
        raise ValueError(f"{path} exceeds the JSON depth limit")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers")
        return
    containers = active if active is not None else set()
    if isinstance(value, list):
        identity = id(value)
        if identity in containers:
            raise ValueError(f"{path} must not contain recursive aliases")
        containers.add(identity)
        try:
            for index, item in enumerate(value):
                validate_json_value(
                    item,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    max_depth=max_depth,
                    active=containers,
                )
        finally:
            containers.remove(identity)
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in containers:
            raise ValueError(f"{path} must not contain recursive aliases")
        containers.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str) or not key:
                    raise ValueError(
                        f"{path} must use non-empty string keys"
                    )
                validate_json_value(
                    item,
                    path=f"{path}.{key}",
                    depth=depth + 1,
                    max_depth=max_depth,
                    active=containers,
                )
        finally:
            containers.remove(identity)
        return
    raise ValueError(f"{path} must contain only JSON-compatible values")


def _validate_schema(
    schema: object,
    *,
    path: str,
    max_depth: int,
    additional_properties_required: bool = False,
    depth: int = 0,
    active: set[int] | None = None,
) -> None:
    if depth > max_depth:
        raise ValueError(f"{path} exceeds the schema depth limit")
    if not isinstance(schema, dict):
        raise ValueError(f"{path} must be a JSON Schema object")
    containers = active if active is not None else set()
    identity = id(schema)
    if identity in containers:
        raise ValueError(f"{path} must not contain recursive aliases")
    containers.add(identity)
    try:
        unknown = set(schema) - SCHEMA_KEYWORDS
        if unknown:
            raise ValueError(
                f"{path} contains unsupported schema keyword "
                f"{sorted(unknown)[0]}"
            )
        schema_type = schema.get("type")
        if schema_type is not None and schema_type not in SCHEMA_TYPES:
            raise ValueError(f"{path} has an unsupported type")
        if schema_type is None and "anyOf" not in schema:
            raise ValueError(f"{path} must declare a type or anyOf")
        for label in ("title", "description"):
            if label in schema and (
                not isinstance(schema[label], str)
                or len(schema[label]) > 2_000
            ):
                raise ValueError(
                    f"{path}.{label} must be a bounded string"
                )
        if "enum" in schema:
            enum = schema["enum"]
            if not isinstance(enum, list) or not enum or len(enum) > 100:
                raise ValueError(
                    f"{path}.enum must be a bounded non-empty list"
                )
            validate_json_value(
                enum,
                path=f"{path}.enum",
                max_depth=max_depth,
            )
        if "anyOf" in schema:
            options = schema["anyOf"]
            if (
                not isinstance(options, list)
                or not options
                or len(options) > 16
            ):
                raise ValueError(
                    f"{path}.anyOf must be a bounded non-empty list"
                )
            for index, option in enumerate(options):
                _validate_schema(
                    option,
                    path=f"{path}.anyOf[{index}]",
                    max_depth=max_depth,
                    additional_properties_required=(
                        additional_properties_required
                    ),
                    depth=depth + 1,
                    active=containers,
                )
        if schema_type == "object":
            properties = schema.get("properties", {})
            if not isinstance(properties, dict) or len(properties) > 100:
                raise ValueError(
                    f"{path}.properties must be a bounded mapping"
                )
            required = schema.get("required", [])
            if (
                not isinstance(required, list)
                or len(required) > 100
                or any(
                    not isinstance(key, str) or not key
                    for key in required
                )
                or len(set(required)) != len(required)
                or not set(required).issubset(properties)
            ):
                raise ValueError(
                    f"{path}.required must be a unique property list"
                )
            if (
                additional_properties_required
                and schema.get("additionalProperties") is not False
            ):
                raise ValueError(
                    f"{path} must declare additionalProperties: false"
                )
            if (
                "additionalProperties" in schema
                and not isinstance(schema["additionalProperties"], bool)
            ):
                raise ValueError(
                    f"{path}.additionalProperties must be a boolean"
                )
            for key, value in properties.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 120
                ):
                    raise ValueError(
                        f"{path}.properties has an invalid key"
                    )
                _validate_schema(
                    value,
                    path=f"{path}.{key}",
                    max_depth=max_depth,
                    additional_properties_required=(
                        additional_properties_required
                    ),
                    depth=depth + 1,
                    active=containers,
                )
        elif "properties" in schema or "required" in schema:
            raise ValueError(
                f"{path}.properties and required need an object type"
            )
        if schema_type == "array":
            if "items" not in schema:
                raise ValueError(f"{path}.items is required for an array")
            _validate_schema(
                schema["items"],
                path=f"{path}.items",
                max_depth=max_depth,
                additional_properties_required=(
                    additional_properties_required
                ),
                depth=depth + 1,
                active=containers,
            )
        elif "items" in schema:
            raise ValueError(f"{path}.items requires an array type")
        for keyword in ("minItems", "maxItems", "minLength", "maxLength"):
            if keyword in schema and (
                not isinstance(schema[keyword], int)
                or isinstance(schema[keyword], bool)
                or schema[keyword] < 0
                or schema[keyword] > 1_000_000
            ):
                raise ValueError(
                    f"{path}.{keyword} must be a bounded non-negative integer"
                )
        for low, high in (
            ("minItems", "maxItems"),
            ("minLength", "maxLength"),
        ):
            if (
                low in schema
                and high in schema
                and schema[low] > schema[high]
            ):
                raise ValueError(f"{path}.{low} must not exceed {high}")
        if "minItems" in schema or "maxItems" in schema:
            if schema_type != "array":
                raise ValueError(
                    f"{path} item limits require an array type"
                )
        if "minLength" in schema or "maxLength" in schema:
            if schema_type != "string":
                raise ValueError(
                    f"{path} length limits require a string type"
                )
        for keyword in ("minimum", "maximum"):
            if keyword in schema and (
                isinstance(schema[keyword], bool)
                or not isinstance(schema[keyword], (int, float))
                or not math.isfinite(schema[keyword])
            ):
                raise ValueError(
                    f"{path}.{keyword} must be a finite number"
                )
        if (
            "minimum" in schema
            and "maximum" in schema
            and schema["minimum"] > schema["maximum"]
        ):
            raise ValueError(
                f"{path}.minimum must not exceed maximum"
            )
        if "minimum" in schema or "maximum" in schema:
            if schema_type not in {"integer", "number"}:
                raise ValueError(
                    f"{path} numeric limits require a numeric type"
                )
    finally:
        containers.remove(identity)


def _validate_schema_document(
    schema: dict[str, Any],
    *,
    path: str,
    max_depth: int,
    additional_properties_required: bool = False,
) -> None:
    validate_json_value(schema, path=path, max_depth=max_depth)
    _validate_schema(
        schema,
        path=path,
        max_depth=max_depth,
        additional_properties_required=additional_properties_required,
    )
    try:
        size = len(
            json.dumps(
                schema,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError(f"{path} must be JSON serializable") from exc
    if size > MAX_SCHEMA_BYTES:
        raise ValueError(f"{path} exceeds the schema byte limit")


def validate_json_schema_value(value: object, schema: dict[str, Any]) -> None:
    """Validate a JSON value against the bounded schema subset."""

    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate_json_schema_value(value, option)
                break
            except ValueError:
                continue
        else:
            raise ValueError("value does not match any allowed schema")
        if schema.get("type") is None and "enum" not in schema:
            return
        schema = {
            key: item
            for key, item in schema.items()
            if key != "anyOf"
        }
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("value is not in the allowed set")
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ValueError("expected object")
        required = schema.get("required", [])
        if any(key not in value for key in required):
            raise ValueError("missing required field")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(
            key not in properties for key in value
        ):
            raise ValueError("unexpected field")
        for key, item_schema in properties.items():
            if key in value:
                validate_json_schema_value(value[key], item_schema)
        return
    if schema_type == "array":
        if not isinstance(value, list):
            raise ValueError("expected array")
        if len(value) < schema.get("minItems", 0):
            raise ValueError("array is too short")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError("array is too long")
        for item in value:
            validate_json_schema_value(item, schema["items"])
        return
    if schema_type == "string":
        if not isinstance(value, str):
            raise ValueError("expected string")
        if len(value) < schema.get("minLength", 0):
            raise ValueError("string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError("string is too long")
        return
    if schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("expected integer")
    elif schema_type == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("expected number")
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("expected boolean")
        return
    elif schema_type == "null":
        if value is not None:
            raise ValueError("expected null")
        return
    else:
        return
    if "minimum" in schema and value < schema["minimum"]:
        raise ValueError("number is below minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise ValueError("number is above maximum")


def _normalize_path(value: object, *, field: str) -> tuple[str | int, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{field} must be a non-empty JSON path list")
    if len(value) > 8:
        raise ValueError(f"{field} exceeds the JSON path depth limit")
    path: list[str | int] = []
    for part in value:
        if isinstance(part, bool):
            raise ValueError(f"{field} contains an invalid path part")
        if isinstance(part, int):
            if part < 0 or part > 1_000_000:
                raise ValueError(
                    f"{field} contains an invalid array index"
                )
            path.append(part)
            continue
        if (
            not isinstance(part, str)
            or not part
            or len(part) > 120
        ):
            raise ValueError(f"{field} contains an invalid path part")
        path.append(part)
    return tuple(path)


class ToolBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_calls: int = Field(default=1, ge=1, le=100)
    # Token accounting belongs to the research loop, which knows the active
    # model and tokenizer; the transport must not approximate it from words.
    max_tokens: int = Field(default=16_000, ge=1, le=2_000_000)
    max_request_bytes: int = Field(
        default=100_000,
        ge=256,
        le=1_000_000,
    )
    max_result_bytes: int = Field(
        default=1_000_000,
        ge=1_024,
        le=20_000_000,
    )


class ResultMappingConfig(BaseModel):
    """Extract configured JSON paths from a tools/call result.

    ``output_path`` selects one value. ``fields`` selects a bounded object.
    With neither, the complete CallToolResult is the mapped value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    output_path: tuple[str | int, ...] | None = None
    fields: dict[str, tuple[str | int, ...]] = Field(default_factory=dict)

    @field_validator("output_path", mode="before")
    @classmethod
    def validate_output_path(cls, value: object) -> object:
        if value is None:
            return None
        return _normalize_path(value, field="output_path")

    @field_validator("fields", mode="before")
    @classmethod
    def validate_fields(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > 20:
            raise ValueError("fields must be a bounded mapping")
        result: dict[str, tuple[str | int, ...]] = {}
        for key, path in value.items():
            if (
                not isinstance(key, str)
                or not IDENTIFIER_PATTERN.fullmatch(key)
            ):
                raise ValueError("fields contains an invalid output key")
            result[key] = _normalize_path(path, field=f"fields.{key}")
        return result

    @model_validator(mode="after")
    def validate_mapping(self) -> ResultMappingConfig:
        if self.output_path is not None and self.fields:
            raise ValueError(
                "result_mapping accepts output_path or fields, not both"
            )
        return self


class ToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    enabled: bool = False
    endpoint: str = Field(min_length=1, max_length=2_048)
    remote_name: str | None = Field(default=None, max_length=200)
    # The configured Source a retrieved original is registered under. A Tool
    # that only discovers links has no Source identity and cannot become
    # evidence, so the id is mandatory for every catalog entry.
    source_id: str
    bearer_token_env: str | None = Field(default=None, max_length=128)
    authorization_prefix: str = Field(
        default="Bearer ",
        min_length=1,
        max_length=32,
    )
    timeout_seconds: float = Field(default=30, ge=1, le=120)
    max_json_depth: int = Field(default=32, ge=4, le=64)
    max_sse_events: int = Field(default=100, ge=1, le=1_000)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    budgets: ToolBudgets = Field(default_factory=ToolBudgets)
    result_mapping: ResultMappingConfig = Field(
        default_factory=ResultMappingConfig
    )

    @field_validator("id", "remote_name")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("must be a bounded identifier")
        return value

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        if not SOURCE_ID_PATTERN.fullmatch(value):
            raise ValueError("source_id must be a configured Source id")
        return value

    @field_validator("bearer_token_env")
    @classmethod
    def validate_environment_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not ENVIRONMENT_NAME_PATTERN.fullmatch(value):
            raise ValueError(
                "bearer_token_env must be an environment variable name"
            )
        return value

    @field_validator("authorization_prefix")
    @classmethod
    def validate_authorization_prefix(cls, value: str) -> str:
        if (
            not value.endswith(" ")
            or any(
                ord(char) < 0x21 or ord(char) > 0x7E
                for char in value
            )
        ):
            raise ValueError(
                "authorization_prefix must end in a space and contain "
                "visible ASCII characters"
            )
        return value

    @field_validator("input_schema")
    @classmethod
    def validate_input_schema(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("input_schema must be an object schema")
        return value

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is not None and not value:
            raise ValueError("output_schema must not be empty")
        return value

    @field_validator("result_mapping", mode="before")
    @classmethod
    def build_result_mapping(cls, value: object) -> object:
        if value is None:
            return ResultMappingConfig()
        return value

    @model_validator(mode="after")
    def validate_tool(self) -> ToolConfig:
        try:
            parsed = validate_https_url(self.endpoint)
        except CollectionError as exc:
            raise ValueError(
                "endpoint must be a public HTTPS URL"
            ) from exc
        if parsed.query or parsed.fragment:
            raise ValueError(
                "endpoint must not contain a query or fragment"
            )
        _validate_schema_document(
            self.input_schema,
            path="input_schema",
            max_depth=self.max_json_depth,
            additional_properties_required=True,
        )
        if self.output_schema is not None:
            _validate_schema_document(
                self.output_schema,
                path="output_schema",
                max_depth=self.max_json_depth,
            )
        return self

    @property
    def mcp_name(self) -> str:
        return self.remote_name or self.id


class ToolConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: list[ToolConfig] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_tools(self) -> ToolConfiguration:
        tool_ids = [tool.id for tool in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("tool ids must be unique")
        remote_keys = [
            (tool.endpoint, tool.mcp_name)
            for tool in self.tools
        ]
        if len(remote_keys) != len(set(remote_keys)):
            raise ValueError(
                "endpoint and remote tool names must be unique"
            )
        return self

    @property
    def enabled_tools(self) -> tuple[ToolConfig, ...]:
        return tuple(tool for tool in self.tools if tool.enabled)

    def authorize(
        self,
        tool_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str],
    ) -> tuple[ToolConfig, ...]:
        return authorize_tools(self, tool_ids)


def authorize_tools(
    configuration: ToolConfiguration,
    tool_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str],
) -> tuple[ToolConfig, ...]:
    """Resolve an explicit trusted authorization set against the catalog."""

    if isinstance(tool_ids, str):
        raise ToolAuthorizationError(
            "TOOL_AUTHORIZATION_INVALID",
            "Authorized tool ids must be a collection of identifiers",
        )
    requested = tuple(tool_ids)
    if any(not isinstance(tool_id, str) for tool_id in requested):
        raise ToolAuthorizationError(
            "TOOL_AUTHORIZATION_INVALID",
            "Authorized tool ids must be strings",
        )
    if len(requested) > MAX_AUTHORIZED_TOOLS:
        raise ToolAuthorizationError(
            "TOO_MANY_AUTHORIZED_TOOLS",
            "Tool authorization exceeds the configured limit",
        )
    if len(requested) != len(set(requested)):
        raise ToolAuthorizationError(
            "DUPLICATE_TOOL_AUTHORIZATION",
            "Authorized tool ids must be unique",
        )
    by_id = {tool.id: tool for tool in configuration.tools}
    selected: list[ToolConfig] = []
    for tool_id in requested:
        tool = by_id.get(tool_id)
        if tool is None:
            raise ToolAuthorizationError(
                "TOOL_NOT_FOUND",
                "Authorized tool is not configured",
                details={"tool_id": tool_id[:120]},
            )
        if not tool.enabled:
            raise ToolAuthorizationError(
                "TOOL_DISABLED",
                "Authorized tool is disabled",
                details={"tool_id": tool.id},
            )
        selected.append(tool)
    return tuple(selected)


def load_tool_configuration(path: str | Path) -> ToolConfiguration:
    config_path = Path(path)
    try:
        size = config_path.stat().st_size
    except OSError:
        raise ToolConfigurationError(
            "CONFIG_UNAVAILABLE",
            "Tool configuration file cannot be read",
        ) from None
    if size > MAX_CONFIG_BYTES:
        raise ToolConfigurationError(
            "CONFIG_TOO_LARGE",
            "Tool configuration file is too large",
        )
    try:
        text = config_path.read_text(encoding="utf-8")
        raw = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ToolConfigurationError(
            "CONFIG_INVALID",
            "Tool configuration is not valid YAML",
        ) from None
    if not isinstance(raw, dict) or set(raw) != {"tools"}:
        raise ToolConfigurationError(
            "CONFIG_INVALID",
            "Tool configuration must contain only a tools list",
        )
    if not isinstance(raw["tools"], list):
        raise ToolConfigurationError(
            "CONFIG_INVALID",
            "tools must be a list",
        )
    try:
        return ToolConfiguration.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(item) for item in first["loc"])
        raise ToolConfigurationError(
            "CONFIG_INVALID",
            "Tool configuration entry is invalid",
            details={"field": field} if field else None,
        ) from None


load_tools = load_tool_configuration


def _content_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tool_snapshot_entry(tool: ToolConfig) -> dict[str, Any]:
    """The credential-free identity of one authorized Tool.

    A Run records this entry in ``TaskRun.model_snapshot`` so an execution can
    prove after the fact which Tool definition it was authorized to use. It
    carries configuration identity only: endpoint, schemas, budgets and whether
    a credential is required, never the credential or its value.
    """

    return {
        "tool_id": tool.id,
        "remote_name": tool.mcp_name,
        "endpoint_hash": _content_hash(tool.endpoint),
        "source_id": tool.source_id,
        "input_schema_hash": _content_hash(tool.input_schema),
        "output_schema_hash": (
            _content_hash(tool.output_schema)
            if tool.output_schema is not None
            else None
        ),
        "result_mapping_hash": _content_hash(
            tool.result_mapping.model_dump(mode="json")
        ),
        "credential_required": tool.bearer_token_env is not None,
        "timeout_seconds": tool.timeout_seconds,
        "max_json_depth": tool.max_json_depth,
        "max_sse_events": tool.max_sse_events,
        "budgets": tool.budgets.model_dump(mode="json"),
    }


def tool_authorization_snapshot(
    tools: tuple[ToolConfig, ...] | list[ToolConfig],
) -> list[dict[str, Any]]:
    return [tool_snapshot_entry(tool) for tool in tools]


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "ResultMappingConfig",
    "ToolBudgets",
    "ToolConfig",
    "ToolConfiguration",
    "authorize_tools",
    "load_tool_configuration",
    "load_tools",
    "tool_authorization_snapshot",
    "tool_snapshot_entry",
    "validate_json_schema_value",
    "validate_json_value",
]
