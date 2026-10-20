from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_CONFIG_BYTES = 1_000_000
CONDITION_FIELDS = {
    "output",
    "output.items",
    "output.coverage",
    "output.truncated",
    "evidence_refs",
}
NONEMPTY_CONDITION_FIELDS = {
    "output",
    "output.items",
    "output.coverage",
    "evidence_refs",
}
EQUALITY_CONDITION_FIELDS = {
    "output.truncated",
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


class ExecutionConfigError(ValueError):
    def __init__(self, code: str, message: str, *, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def to_dict(self) -> dict[str, str]:
        result: dict[str, str] = {
            "code": self.code,
            "message": self.message,
        }
        if self.field:
            result["field"] = self.field
        return result


class StepCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: str = Field(min_length=1, max_length=120)
    op: Literal["exists", "nonempty", "equals", "not_equals"]
    field: str | None = Field(default=None, max_length=120)
    value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def validate_operator_arguments(self) -> StepCondition:
        if self.op == "exists" and self.field is not None:
            raise ValueError("exists does not accept field")
        if self.op in {"equals", "not_equals"}:
            if not self.field:
                raise ValueError(f"{self.op} requires field")
            if self.value is None:
                raise ValueError(f"{self.op} requires value")
        if self.op in {"exists", "nonempty"} and self.value is not None:
            raise ValueError(f"{self.op} does not accept value")
        if self.field is not None and self.field not in CONDITION_FIELDS:
            raise ValueError("condition field is not supported")
        if self.field == "evidence_refs" and self.op in {"equals", "not_equals"}:
            raise ValueError("equals/not_equals do not support evidence_refs")
        if (
            self.op == "nonempty"
            and self.field is not None
            and self.field not in NONEMPTY_CONDITION_FIELDS
        ):
            raise ValueError(
                f"nonempty does not support field {self.field}"
            )
        if (
            self.op in {"equals", "not_equals"}
            and self.field not in EQUALITY_CONDITION_FIELDS
        ):
            raise ValueError(
                f"{self.op} does not support field {self.field}"
            )
        if (
            self.field == "output.truncated"
            and self.op in {"equals", "not_equals"}
            and not isinstance(self.value, bool)
        ):
            raise ValueError("output.truncated requires a boolean value")
        if self.op in {"equals", "not_equals"} and not isinstance(
            self.value, bool
        ):
            raise ValueError(f"{self.op} requires a boolean value")
        return self


class SearchStepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["repository_search"]
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=8, ge=1, le=50)
    source_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_source_ids(self) -> SearchStepConfig:
        if any(
            not source_id
            or len(source_id) > 120
            for source_id in self.source_ids
        ) or len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError(
                "source_ids must be unique non-empty values of at most 120 chars"
            )
        return self


class AgentStepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["research_agent"]
    agent: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    input: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)


class TaskStepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    when: StepCondition | None = None
    repository_search: SearchStepConfig | None = None
    research_agent: AgentStepConfig | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> TaskStepConfig:
        kinds = [
            self.repository_search is not None,
            self.research_agent is not None,
        ]
        if sum(kinds) != 1:
            raise ValueError("each step must define exactly one supported step kind")
        if self.repository_search is not None:
            assert self.repository_search.kind == "repository_search"
        if self.research_agent is not None:
            assert self.research_agent.kind == "research_agent"
        return self

    @property
    def condition(self) -> StepCondition | None:
        return self.when

    @property
    def kind(self) -> str:
        if self.repository_search is not None:
            return "repository_search"
        return "research_agent"


class TaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    name: str = Field(min_length=1, max_length=255)
    type: Literal["research"]
    trigger: Literal["manual"]
    enabled: bool = True
    objective: str = Field(min_length=1, max_length=4000)
    strategy: str | None = Field(default=None, max_length=255)
    scope: dict
    steps: list[TaskStepConfig] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_steps(self) -> TaskConfig:
        if not isinstance(self.scope, dict) or not self.scope:
            raise ValueError("scope must be a non-empty mapping")
        _validate_json_value(self.scope, path="scope")
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate step id: {step.id}")
            condition = step.condition
            if condition is not None and condition.step not in seen:
                raise ValueError(
                    f"step {step.id} condition must reference an earlier step: "
                    f"{condition.step}"
                )
            if (
                step.research_agent is not None
                and step.research_agent.input not in seen
            ):
                raise ValueError(
                    f"step {step.id} input must reference an earlier step: "
                    f"{step.research_agent.input}"
                )
            seen.add(step.id)
        return self


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    role: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1, max_length=200_000)
    model_ref: str = Field(min_length=1, max_length=120)
    tools: list[str] = Field(default_factory=list, max_length=0)
    output_schema: dict
    input_schema: dict

    @model_validator(mode="after")
    def validate_schemas(self) -> AgentConfig:
        for name, schema in (
            ("input_schema", self.input_schema),
            ("output_schema", self.output_schema),
        ):
            if not schema or schema.get("type") != "object":
                raise ValueError(f"{name} must be a non-empty object schema")
            if schema.get("additionalProperties") is not False:
                raise ValueError(
                    f"{name} must declare additionalProperties: false"
                )
            required = schema.get("required")
            properties = schema.get("properties")
            if (
                not isinstance(required, list)
                or any(not isinstance(key, str) for key in required)
                or set(required) != set(properties or {})
            ):
                raise ValueError(
                    f"{name} must require every declared property"
                )
            _validate_schema_keywords(schema, path=name)
            _validate_json_schema_shape(schema, path=name)
        return self


class TaskConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tasks: list[TaskConfig] = Field(min_length=1)
    agents: list[AgentConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> TaskConfiguration:
        task_ids = [task.id for task in self.tasks]
        agent_ids = [agent.id for agent in self.agents]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task ids must be unique")
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("agent ids must be unique")
        known_agents = set(agent_ids)
        for task in self.tasks:
            if not task.steps or task.steps[-1].research_agent is None:
                raise ValueError(
                    "A research Task requires a final Research Agent step"
                )
            for step in task.steps:
                condition = step.condition
                if condition is not None:
                    referenced = next(
                        item for item in task.steps if item.id == condition.step
                    )
                    if referenced.repository_search is None:
                        raise ValueError(
                            f"step {step.id} condition may inspect only a "
                            "repository search result"
                        )
                if (
                    step.research_agent is not None
                    and step.research_agent.agent not in known_agents
                ):
                    raise ValueError(
                        f"unknown Agent reference: {step.research_agent.agent}"
                    )
                if step.research_agent is not None:
                    source_kind = self._referenced_step_kind(
                        task, step.research_agent.input
                    )
                    if source_kind != "repository_search":
                        raise ValueError(
                            f"step {step.id} input must reference a repository search"
                        )
                    source_step = next(
                        item
                        for item in task.steps
                        if item.id == step.research_agent.input
                    )
                    if source_step.condition is not None:
                        if (
                            step.condition is None
                            or step.condition.step != source_step.id
                            or step.condition.op != "nonempty"
                            or step.condition.field != "output.items"
                        ):
                            raise ValueError(
                                f"step {step.id} must guard conditional input "
                                f"{source_step.id} with nonempty output.items"
                            )
                    if step.condition is not None:
                        condition = step.condition
                        if (
                            condition.step != source_step.id
                            or condition.op != "nonempty"
                            or condition.field != "output.items"
                        ):
                            raise ValueError(
                                f"step {step.id} Agent conditions may only guard "
                                f"its input with nonempty output.items"
                            )
        return self

    @staticmethod
    def _referenced_step_kind(task: TaskConfig, step_id: str) -> str | None:
        for step in task.steps:
            if step.id == step_id:
                return step.kind
        return None

    @property
    def task_map(self) -> dict[str, TaskConfig]:
        return {task.id: task for task in self.tasks}

    @property
    def agent_map(self) -> dict[str, AgentConfig]:
        return {agent.id: agent for agent in self.agents}


def load_task_configuration(path: str | Path) -> TaskConfiguration:
    config_path = Path(path)
    try:
        if config_path.stat().st_size > MAX_CONFIG_BYTES:
            raise ExecutionConfigError(
                "CONFIG_TOO_LARGE", "Task configuration is too large"
            )
        raw_text = config_path.read_text(encoding="utf-8")
        raw = yaml.load(raw_text, Loader=_UniqueKeySafeLoader)
    except ExecutionConfigError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ExecutionConfigError(
            "CONFIG_UNAVAILABLE", "Task configuration cannot be read"
        ) from exc
    except yaml.YAMLError as exc:
        raise ExecutionConfigError(
            "CONFIG_INVALID", "Task configuration is not valid YAML"
        ) from exc
    if not isinstance(raw, dict) or set(raw) != {"tasks", "agents"}:
        raise ExecutionConfigError(
            "CONFIG_INVALID",
            "Task configuration must contain only tasks and agents",
        )
    try:
        return TaskConfiguration.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(item) for item in first["loc"])
        raise ExecutionConfigError(
            "CONFIG_INVALID",
            f"Task configuration is invalid: {first['msg']}",
            field=location,
        ) from exc


SCHEMA_KEYWORDS = {
    "title",
    "type",
    "additionalProperties",
    "required",
    "properties",
    "items",
    "minItems",
    "maxItems",
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


def _validate_json_value(
    value: object,
    *,
    path: str,
    active_containers: set[int] | None = None,
) -> None:
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers")
        return
    active = active_containers if active_containers is not None else set()
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} must not contain recursive YAML aliases")
        active.add(identity)
        try:
            for index, item in enumerate(value):
                _validate_json_value(
                    item,
                    path=f"{path}[{index}]",
                    active_containers=active,
                )
        finally:
            active.remove(identity)
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} must not contain recursive YAML aliases")
        active.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str) or not key:
                    raise ValueError(
                        f"{path} must use non-empty string keys"
                    )
                _validate_json_value(
                    item,
                    path=f"{path}.{key}",
                    active_containers=active,
                )
        finally:
            active.remove(identity)
        return
    raise ValueError(f"{path} must contain only JSON-compatible values")


def _validate_schema_keywords(
    schema: object,
    *,
    path: str,
) -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path} must be a JSON schema object")
    unknown = set(schema) - SCHEMA_KEYWORDS
    if unknown:
        raise ValueError(
            f"{path} contains unsupported schema keyword {sorted(unknown)[0]}"
        )
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in SCHEMA_TYPES:
        raise ValueError(f"{path} has an unsupported type")
    if schema_type is None and "anyOf" not in schema:
        raise ValueError(f"{path} must declare a type or anyOf")
    if "enum" in schema and (
        not isinstance(schema["enum"], list) or not schema["enum"]
    ):
        raise ValueError(f"{path}.enum must be a non-empty list")
    if "anyOf" in schema:
        options = schema["anyOf"]
        if not isinstance(options, list) or not options:
            raise ValueError(f"{path}.anyOf must be a non-empty list")
        for index, option in enumerate(options):
            _validate_schema_keywords(
                option,
                path=f"{path}.anyOf[{index}]",
            )
    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError(f"{path}.properties must be a mapping")
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or any(not isinstance(key, str) for key in required)
            or len(set(required)) != len(required)
        ):
            raise ValueError(
                f"{path}.required must be a unique string list"
            )
        for key, value in properties.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path}.properties has an invalid key")
            _validate_schema_keywords(value, path=f"{path}.{key}")
        additional = schema.get("additionalProperties")
        if additional not in {None, True, False}:
            raise ValueError(
                f"{path}.additionalProperties must be a boolean"
            )
    if schema_type == "array" and "items" in schema:
        _validate_schema_keywords(
            schema["items"],
            path=f"{path}.items",
        )


def _validate_json_schema_shape(schema: dict, *, path: str) -> None:
    schema_type = schema.get("type")
    if "required" in schema and schema_type != "object":
        raise ValueError(f"{path}.required requires an object type")
    if "properties" in schema and schema_type != "object":
        raise ValueError(f"{path}.properties requires an object type")
    if "items" in schema and schema_type != "array":
        raise ValueError(f"{path}.items requires an array type")
    if schema_type == "array" and "items" not in schema:
        raise ValueError(f"{path}.items is required for an array")
    if "minItems" in schema and schema_type != "array":
        raise ValueError(f"{path}.minItems requires an array type")
    if "maxItems" in schema and schema_type != "array":
        raise ValueError(f"{path}.maxItems requires an array type")
    if "enum" in schema:
        _validate_json_value(schema["enum"], path=f"{path}.enum")
    for keyword in ("minItems", "maxItems"):
        if keyword in schema and (
            not isinstance(schema[keyword], int)
            or isinstance(schema[keyword], bool)
            or schema[keyword] < 0
        ):
            raise ValueError(f"{path}.{keyword} must be a non-negative integer")
    if "minItems" in schema and "maxItems" in schema:
        if schema["minItems"] > schema["maxItems"]:
            raise ValueError(
                f"{path}.minItems must not exceed maxItems"
            )
    properties = schema.get("properties", {})
    for key, value in properties.items():
        _validate_json_schema_shape(value, path=f"{path}.{key}")
    if "items" in schema:
        _validate_json_schema_shape(
            schema["items"],
            path=f"{path}.items",
        )
    for index, option in enumerate(schema.get("anyOf", [])):
        _validate_json_schema_shape(
            option,
            path=f"{path}.anyOf[{index}]",
        )


def snapshot_payload(
    task: TaskConfig,
    agents: list[AgentConfig],
) -> dict:
    return {
        "task": task.model_dump(mode="json"),
        "agents": [agent.model_dump(mode="json") for agent in agents],
    }


def snapshot_hash(snapshot: dict) -> str:
    encoded = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
