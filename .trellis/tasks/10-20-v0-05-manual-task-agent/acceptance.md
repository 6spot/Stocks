# V0-05 脱敏验收记录

验收日期：2026-10-20

## 环境与范围

- 所有验证在测试服务器 `100.120.14.84` 的任务隔离目录执行；本机只编辑和打包。
- 使用服务器持久 Python 3.12.15 环境、PostgreSQL 17.11 与 pgvector 0.8.7。
- 没有创建新容器；复用已有 `stocks-v0-03-accept-postgres` pgvector 容器，在其中创建
  V0-05 独立测试数据库和随机凭据角色。未删除或修改其他任务角色。
- 数据库初始化先遇到隔离普通角色不能创建或加载 `vector` 扩展的权限失败；迁移事务
  完整回滚。随后由容器管理员只在本次独立数据库安装扩展，并只给本次临时角色测试所需
  的超级用户权限。该变化已记录到后端测试规范，不用于生产权限设计。
- Provider 成功、重试和回退使用 `httpx.MockTransport`；数据库持久化与互斥使用真实
  PostgreSQL/pgvector。没有真实外部 Provider、仓库真实 Document 或 S3 数据参与
  V0-05 Agent 调用。

## AC-1：成功、失败、跳过与不可变快照

结果：通过声明范围；真实 Provider 端到端成功调用未验收。

- 远程 PostgreSQL 测试覆盖成功 Run 的结构化 StepResult、最终输出、ModelCall 和持久
  Chunk/Document/解析批次/版本/来源引用。
- 覆盖无证据时的失败 StepResult、Run 失败和既有成功 Run 不被覆盖；覆盖条件不满足时
  Agent 步骤保存为 `skipped`，Run 本身成功。
- Run 保存 objective、strategy、scope、Task/Agent 配置、Prompt 和模型/Embedding 路由
  快照；执行前后快照保持不变。
- `config/tasks.yaml` 的 `saved_evidence_brief`、`empty_evidence_skip` 和
  `unavailable_evidence_failure` 三个示例可由配置校验与 CLI 入口解析。

## AC-2：活动 Run 互斥与重新执行

结果：通过。

- 使用 PostgreSQL 条件唯一索引与 advisory transaction lock 验证并发触发只创建一个活跃
  Run，另一个触发返回相同 ID。
- 并发创建只解析一次模型路由配置；普通 `queued` Run 只能被一个所有权令牌领取。
- 活跃 Run 存在时，CLI 直接返回现有摘要且不执行、不读取模型密钥。
- 活跃 Run 结束后可以创建不同 ID 的新 Run。

## AC-3：有限重试、回退与凭据边界

结果：通过自动化边界测试；真实外部 Provider 调用未验收。

- HTTP 408、429、5xx、连接失败、远程协议错误和超时按配置依次重试或切换 fallback；
  每个候选使用自己的超时。
- 401/403、配置错误、缺失能力和无效请求不受盲目 fallback；非法结构化响应可在上限内
  重试或回退。
- ModelCall 保留请求/实际 alias、模型名、供应商报告模型名、耗时、Token、回退原因和
  安全错误；Run、StepResult 与 ModelCall 序列化检查未发现 API key、Authorization
  header、endpoint 明文或响应正文。
- 证据引用会校验属于本次选中的证据包，并保存可回查的持久标识；最终引用越界时在
  重试耗尽后记录 `MODEL_RESPONSE_INVALID`。

## 实际执行

```text
Python 3.12.15 + PostgreSQL 17.11/pgvector 0.8.7:
TEST_DATABASE_URL=<isolated-test-db> \
  .venv/bin/python -m pytest -q tests/test_execution.py tests/test_providers.py
exit 0
57 passed

TEST_DATABASE_URL=<isolated-test-db> \
  .venv/bin/python -m pytest -q \
  tests/test_embeddings.py tests/test_search_cli.py \
  tests/test_repository.py tests/test_processing.py
exit 0
51 passed

TEST_DATABASE_URL=<isolated-test-db> \
  .venv/bin/python -m pytest -q \
  tests/test_api.py tests/test_collection.py tests/test_s3.py
exit 0
45 passed

全新空库结构检查:
.venv/bin/alembic upgrade head
exit 0
.venv/bin/alembic check
No new upgrade operations detected.

.venv/bin/python -m compileall -q src tests
exit 0

配置与 CLI:
load_task_configuration("../config/tasks.yaml")
3 tasks, 1 agent
python -m stocks.execution --help
exit 0

纯静态检查:
git diff --check
exit 0
```

合计远程 `153 passed`。失败过程中的 9 个初始测试问题由测试断言、路径构造和 dataclass
注入字段缺陷引起，修正后同范围全部通过；3 个 pgvector 权限失败在隔离角色获得测试所需
权限后通过。未把失败中间结果计为通过。

## 未验证与限制

- 没有调用真实外部模型 Provider，因此实际供应商兼容性、凭据、网络和结构化输出支持
  尚未完成阶段验收；MockTransport 和本地数据库测试不能替代真实调用。
- 没有使用真实 Repository 证据完成一次成功的 Agent 研究；V0-05 只负责手动 Task Run
  与 Agent 执行，真实证据到报告闭环由后续 V0-06/V0-07 验收。
- `SIGKILL` 在原子领取后仍可留下 `running` Run；中断进程的自动恢复明确属于 V1。
- 证据引用越界最终复用 `MODEL_RESPONSE_INVALID` 错误码；更细的公开错误契约尚未定义。
- `ruff` 与 `mypy` 未安装在项目约定环境中，因此未运行；`compileall` 和静态差异检查
  通过。
- 开发阶段不维护增量迁移，验收只使用全新数据库；本次未验证任何旧库升级。
