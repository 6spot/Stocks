# V0-06 脱敏验收记录

验收日期：2026-10-20

## 环境与范围

- 所有验证在测试服务器 `100.120.14.84` 的任务隔离目录 `/tmp/stocks-v0-06-<run>` 执行；
  本机只编辑和打包代码，未在本机运行 pytest、Alembic、数据库或服务。
- 使用服务器持久 Python 3.12.15、SQLAlchemy 2.1.5、httpx 0.28.1 环境；数据库为
  PostgreSQL 17.11 + pgvector 0.8.7。
- 没有创建新容器：先确认既有容器归属，沿用 `stocks-v0-03-accept-postgres`（pgvector），
  只为本任务新建独立测试数据库与临时角色，角色按测试规范授予加载 `vector` 所需权限。
  其他任务的容器与角色未被修改或删除。
- 模型 Provider 的成功、重试、回退与非法引用全部使用 `httpx.MockTransport`；
  Evidence Package、Analysis、Report 与引用校验使用真实 PostgreSQL/pgvector 持久化。
- 没有真实外部模型 Provider 参与本次验收：测试服务器上不存在模型凭据，仓库只有
  `.env.example` 占位值，凭据不得伪造。因此研究质量与真实供应商兼容性尚未验收。

## AC-1：真实入库资料产出可追溯报告

结果：数据库闭环通过；真实 Provider 调用未验收。

- `select_evidence` 从当前成功解析批次的真实 Chunk 生成并持久化 Evidence Package，
  保存 objective、`run.scope`、`as_of`、规则版本、预算、每条实际 Query 及
  `direct | background` 角色、所选 Chunk 的 Document/Chunk/parse batch 版本与原始位置、
  选择理由、索引覆盖与缺口。
- `research_agent` 只读取该 Run 已保存的包；输出结构与每条引用在模型调用内校验，
  Analysis 与其确定性生成的 Markdown Report 在同一事务中与成功的 StepResult 一起提交。
- StepResult、Analysis 与 finding 保存的引用只含可回查身份字段
  （`chunk_id`、`document_id`、`parse_batch_id`、`document_version`、`source_id`、
  `position`），报告正文通过引用回查 Repository，不复制整篇原文。
- 远程测试断言 Report 含实际 Document/Chunk 标识、Analysis 指向正确 Package、
  Report 指向该 Analysis，且 Run 输出只暴露这两个持久 ID。

## AC-2：无资料、不足与截断如实可见

结果：通过。

- 候选总数无法由有界查询确定时 `candidates_discovered` 与 `unexamined_count` 保存为
  `null`，不填零；预算命中上限才标记 `retrieval_truncated`，查询本身触顶标记
  `query_truncated`。
- 每条 Query 单独记录返回数、截断与缺口原因；包级缺口由这些原因以及
  直接/背景无匹配、无选中派生，缺少某类背景 Query 不再被误报为缺口。
- 空包仍然持久化并如实说明，不虚构统计；条件不满足时 Agent 步骤保存为 `skipped`，
  Run 成功；无可用证据且步骤未跳过时保存失败 StepResult 与失败 Run。
- 未显式配置 `queries` 时按 `run.scope` 派生直接公司/代码查询与独立背景主题查询，
  不把 Scope 各维度合成一个 AND 过滤。

## AC-3：非法引用拒绝且历史结果不被破坏

结果：通过。

- 引用必须属于本次包的选中 Chunk；越界引用在模型路由内有限重试或回退，耗尽后记录
  `MODEL_RESPONSE_INVALID`，步骤失败且不写入 Analysis/Report。
- Document 版本、Chunk 或当前解析批次不匹配（含被后续重处理批次取代）时，
  `validate_selected_versions` 拒绝，旧引用无法冒充当前证据。
- 失败 Run 保留自己的 Evidence Package 与选中计数，此前成功 Run 的输出、Analysis
  与 Report 均保持原样，未被覆盖或替换。
- 同一 Run 重试沿用已保存的包，不重新取材改写快照。

## 实际执行

```text
Python 3.12.15 + PostgreSQL 17.11/pgvector 0.8.7（任务隔离数据库）:
TEST_DATABASE_URL=<isolated-test-db> \
  .venv/bin/python -m pytest -q tests/test_research.py tests/test_execution.py
exit 0
59 passed

TEST_DATABASE_URL=<isolated-test-db> \
  .venv/bin/python -m pytest -q tests/test_research.py
exit 0
7 passed

TEST_DATABASE_URL=<isolated-test-db> \
  .venv/bin/python -m pytest -q tests/test_execution.py
exit 0
52 passed

受步骤类型重命名与引用契约影响的既有套件:
TEST_DATABASE_URL=<isolated-test-db> \
  .venv/bin/python -m pytest -q \
  tests/test_embeddings.py tests/test_search_cli.py tests/test_repository.py \
  tests/test_processing.py tests/test_api.py tests/test_collection.py \
  tests/test_providers.py tests/test_s3.py
exit 0
102 passed

全新空库结构检查（本任务独立新建数据库）:
.venv/bin/alembic upgrade head          exit 0
.venv/bin/alembic check
No new upgrade operations detected.
public 表数 16；uq_evidence_packages_run / uq_analyses_run / uq_reports_run 均存在
验证后删除该临时数据库

.venv/bin/python -m compileall -q src tests
exit 0

配置与 CLI:
load_task_configuration("../config/tasks.yaml") -> 3 tasks 1 agent，步骤类型
[select_evidence, research_agent, ...]
python -m stocks.execution --help -> exit 0
```

合计远程 `161 passed`。过程中先前遗留的 2 个失败用例（步骤证据引用含完整正文、
非法引用错误码被通用异常吞掉）在把引用投影与输出校验归属 Research 模块后复跑通过；
未把失败中间结果计为通过。

## 未验证与限制

- 没有调用真实外部模型 Provider，实际供应商兼容性、凭据、网络与结构化输出支持尚未
  验收；MockTransport 与真实数据库测试不能替代真实研究调用。
- 没有用真实来源采集的正文完成一次人工复核报告：V0-06 范围内只做 Repository 到
  Analysis/Report 的实现闭环，真实端到端（采集 → 解析 → 索引 → 研究 → 阅读）由
  V0-07 阶段验收，读取 API 亦属 V0-07。
- 报告文本质量、finding 是否真的代表证据含义无法由自动测试判定；确定性检查只保证
  结构、引用归属与版本一致。
- `ruff` 与 `mypy` 未安装在项目约定持久环境中，因此未运行；已运行 `compileall` 与
  结构一致性检查。
- 开发阶段不维护增量迁移，验收只使用全新数据库；未验证任何旧库升级路径。
- 本任务容器与远程目录暂保留（`stocks-v0-03-accept-postgres`、
  `/tmp/stocks-v0-06-<run>`）以便 V0-07 直接沿用；该容器属于 V0-03 验收遗留资源，
  删除会影响其他任务，不在本次清理。
