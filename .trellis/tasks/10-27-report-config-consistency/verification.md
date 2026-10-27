# MP-01 远程验证与交付

日期：2026-10-27。实现与验证完成；用户随后明确授权归档、提交并推送，进入收尾。

## 实际变更

- `WorkerContext.from_settings()` 从 Settings 取得 tasks/models/sources 路径；Worker CLI 只传实际提供的路径覆盖。
- collection、execution、processing、indexing、search 的现有配置参数在执行点从 Settings 解析默认值；保留显式参数优先、模型/凭据惰性加载、已有记录检查和错误码。
- Compose 不再固定 Worker 配置参数。API 与 Worker 继续共享 `.env` 和只读配置挂载。
- 更新执行契约、启动说明与部署工程规范，补充必要配置入口回归。未增加 taxonomy Settings、业务调度逻辑、模型快照变更、数据库模型或 MP-02 工作。

## 环境和证据

全部应用验证只在 `root@100.120.14.84:/tmp/stocks-mp01-config-consistency` 执行；本机只读取、编辑、打包和接收证据。使用任务独立 PostgreSQL/pgvector（回环 15561）与 S3（回环 18361），Python 复用 `/opt/stocks-test-runtime/tool-venvs/stock-tools`，`PYTHONPATH` 指向本任务 `backend/src`。

- [修复前路径](research/evidence/before-paths.json)：Settings 指向 `custom/mp01-{tasks,models,sources}.yaml`，Worker 却使用 `config/*.yaml`；探针按预期退出 1。
- [修复后路径](research/evidence/after-paths.json)：同一探针退出 0，Worker 与 Settings 的自定义路径相等，tools 路径保持现有传递方式。
- [新库初始化](research/evidence/schema.log)：全新任务数据库执行 `alembic upgrade head`，随后 `alembic check` 输出 `No new upgrade operations detected.`。这是初始化检查，未添加或验证历史数据迁移。
- [Compose 解析](research/evidence/compose.json)：`docker compose --project-name stocks-mp01-check config --format json` 解析成功；断言 API/Worker 四项配置路径相等、共享只读 `/app/config` 挂载，以及 Worker command 为 `python -m stocks.worker`。

## 定向回归

在远程任务目录载入私有 `runtime.env` 后，从 `backend/` 执行：

```bash
.venv/bin/python -m pytest -q tests/test_search_cli.py
.venv/bin/python -m pytest -q tests/test_worker_processing.py -k 'configuration or builtin_defaults or cli_status'
.venv/bin/python -m pytest -q tests/test_collection.py -k collection_cli_configuration_path_errors
.venv/bin/python -m pytest -q tests/test_execution.py -k 'cli_trigger or cli_reports_unknown_task_and_invalid_limit'
```

| 范围 | 实际结果 | 日志 |
| --- | --- | --- |
| 搜索配置、已有记录惰性读取、六个入口 help | 12 passed | [search](research/evidence/search-tests.log) |
| Worker、processing、indexing 的默认/显式路径、状态及错误 | 13 passed, 21 deselected | [worker](research/evidence/worker-tests.log) |
| collection 的默认/显式路径及缺失/非法配置错误码 | 4 passed, 49 deselected | [collection](research/evidence/collection-tests.log) |
| CLI 入队持久化、显式覆盖、活跃 Run 惰性检查及参数错误 | 5 passed, 62 deselected | [execution](research/evidence/execution-tests.log) |

总计 **34 passed**；未运行全量测试。项目未配置独立 lint/type-check 命令，本次没有另行运行此类工具或镜像构建。

## API → Worker 持久化验收

[验收脚本](research/config_handoff_smoke.py)基于已归档 pipeline smoke，使用可辨认的自定义 sources/tasks/models；Worker 由 `WorkerContext.from_settings(model_transport=...)` 构建，不显式覆盖配置路径。脚本配置的外部研究 Tools 关闭，仅为本任务受控证据链服务。

[成功 manifest](research/evidence/manifest.json)记录：

- 真实应用登录/CSRF 后，API 入队自定义采集任务；Worker 领取并使用唯一自定义 Source（Feed 与正文各调用一次），原始快照实际写入/读回任务 S3。
- parse/index/classify/facts 四阶段均持久化 `completed`，各一次尝试，并生成 Chunk、Fact 和 embedding。
- API 入队自定义研究任务 `mp_research_a5aa4df7addd`，Worker 生成持久 Analysis、Report、EvidencePackage；实际 ModelCall 保存 `controlled-research`。
- 研究 Run：`e8b6cd94-1056-40e1-bd56-308cd5b40e76`；Report：`bacf3bb0-92d6-4975-989c-92304ce06232`。
- 报告引用、Chunk/Fact 定位、原件 SHA-256 和语义搜索接口断言通过。

该验收使用真实数据库、对象存储和应用逻辑，以及**受控 HTTP/模型传输**，不代表真实外部来源或 Provider 成功；未做浏览器 UI 验收或完整 Compose 镜像部署。后续 pytest 使用同一任务独立库的清表 fixture，验收时的持久结果以 manifest 为留存证据。

## 审查与资源收尾

Pi 原生 `trellis-check` 子 Agent 对生产实现、测试和执行/启动文档做只读定向审查，结论为 **No findings**；未另行运行验证。其结束后主 Agent 将防回归入口规则补充到部署工程规范，并自行核对该文档改动。

任务容器 `stocks-mp01-postgres`、`stocks-mp01-s3` 已移除，私有 `runtime.env` 已删除，Compose 检查临时 `.env` 已删除。远程任务目录保留代码、脱敏日志及受控快照，不再有本任务运行容器或后台测试进程；未修改共享运行时或其他任务资源。manifest 的 retained 标记反映验收结束当时，资源最终状态以本段为准。

原有 `AGENTS.md`、路线图及其他规划任务改动保持原状，不纳入本任务提交。用户授权的归档、提交、推送结果由归档状态和会话记录追踪。
