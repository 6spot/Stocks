"""Run only on the task-isolated test server; contains no credentials."""
import json
import os
from stocks.settings import settings
from stocks.worker import WorkerContext

os.environ.update(
    TASKS_CONFIG_PATH="custom/mp01-tasks.yaml",
    MODELS_CONFIG_PATH="custom/mp01-models.yaml",
    SOURCES_CONFIG_PATH="custom/mp01-sources.yaml",
    TOOLS_CONFIG_PATH="custom/mp01-tools.yaml",
)
settings.cache_clear()
config = settings()
context = WorkerContext.from_settings()
expected = {"tasks": config.tasks_config_path, "models": config.models_config_path,
            "sources": config.sources_config_path}
actual = {"tasks": context.task_config_path, "models": context.model_config_path,
          "sources": context.sources_config_path}
print(json.dumps({"expected": expected, "actual": actual,
                  "matches": actual == expected, "tools": context.settings.tools_config_path}, indent=2))
raise SystemExit(0 if actual == expected else 1)
