# V1-02 精确提交计划（已获授权）

建议一个工作提交：

`feat(processing): add resumable document stages and backlog recovery`

当前功能已实现并通过最终 Check。Owner 已明确要求“提交推送归档”，批准按以下范围提交、推送当前分支并归档本任务。归档与会话日志在工作提交后分别记录。

## 代码、初始化、配置与测试

- `.env.example`
- `backend/alembic/versions/0001_initial.py`
- `backend/src/stocks/models.py`
- `backend/src/stocks/repository.py`
- `backend/src/stocks/settings.py`
- `backend/src/stocks/worker.py`
- `backend/src/stocks/processing/__main__.py`
- `backend/src/stocks/processing/service.py`
- `backend/src/stocks/processing/stages.py`
- `backend/src/stocks/processing/runner.py`
- `backend/src/stocks/processing/processors.py`
- `backend/src/stocks/indexing/__main__.py`
- `backend/src/stocks/indexing/service.py`
- `backend/tests/test_collection.py`
- `backend/tests/test_processing.py`
- `backend/tests/test_processing_retry.py`
- `backend/tests/test_worker_processing.py`
- `backend/tests/v1_02_processing_smoke.py`

## 归属文档与工程规范

- `docs/data.md`
- `docs/execution.md`
- `docs/quickstart.md`
- `docs/validation.md`
- `.trellis/spec/backend/index.md`
- `.trellis/spec/backend/worker-guidelines.md`

## 本任务规划、验收及研究记录

- `.trellis/tasks/10-20-v1-02-processing-recovery/acceptance.md`
- `.trellis/tasks/10-20-v1-02-processing-recovery/check.jsonl`
- `.trellis/tasks/10-20-v1-02-processing-recovery/design.md`
- `.trellis/tasks/10-20-v1-02-processing-recovery/implement.jsonl`
- `.trellis/tasks/10-20-v1-02-processing-recovery/implement.md`
- `.trellis/tasks/10-20-v1-02-processing-recovery/prd.md`
- `.trellis/tasks/10-20-v1-02-processing-recovery/research/batch-a-notes.md`
- `.trellis/tasks/10-20-v1-02-processing-recovery/research/batch-b-notes.md`
- `.trellis/tasks/10-20-v1-02-processing-recovery/research/kickoff-review.md`
- `.trellis/tasks/10-20-v1-02-processing-recovery/research/review.md`
- `.trellis/tasks/10-20-v1-02-processing-recovery/task.json`
- `.trellis/tasks/10-20-v1-02-processing-recovery/commit-plan.md`

## 无关并行工作（全部排除）

- `.agents/skills/trellis-continue/SKILL.md`
- `.agents/skills/trellis-start/SKILL.md`
- `.pi/agents/trellis-check.md`
- `.pi/agents/trellis-implement.md`
- `.pi/extensions/trellis/index.ts`
- `.pi/prompts/trellis-continue.md`
- `.pi/prompts/trellis-start.md`
- `.trellis/model-routing.md`
- `.trellis/tasks/10-21-pi-trellis-inline-workflow/acceptance.md`
- `.trellis/tasks/10-21-pi-trellis-inline-workflow/check.jsonl`
- `.trellis/tasks/10-21-pi-trellis-inline-workflow/design.md`
- `.trellis/tasks/10-21-pi-trellis-inline-workflow/implement.jsonl`
- `.trellis/tasks/10-21-pi-trellis-inline-workflow/implement.md`
- `.trellis/tasks/10-21-pi-trellis-inline-workflow/prd.md`
- `.trellis/tasks/10-21-pi-trellis-inline-workflow/research/init-reinitialization.md`
- `.trellis/tasks/10-21-pi-trellis-inline-workflow/task.json`
- `.trellis/workflow.md`
- `.opencode/` 下全部删除项（另一窗口的工具集成移除）

新增清单外改动不得静默纳入。归档与日志应在工作提交之后按授权分别记录，不在此批混入其他任务。

## 实际验证与限制

- 最终源码：测试服务器 `100.120.14.84` 的本任务隔离环境，五个相关文件 **106 passed**，真实 Worker/CLI 子进程 smoke **34/34 PASS**。
- 并发修复两个用例在修复前均失败、修复后通过；全新数据库 `alembic upgrade head` / `alembic check` 通过。
- 较早批次全量 258 passed、首轮 Check 185 passed 是分阶段证据，不与末轮数量相加，也不冒充最终源码全量重跑。
- 分类/Fact 算法与真实 Source Mapper 尚未实现；PA-1、PA-2、Compose/ARM、Web、变化水位继续保持原状态。
- 本任务远程容器、专用数据库卷、a1/b1/c1 目录、S3 数据目录及 17 份归档已清理；公共工具环境和其他任务资源保留。
- 详见 acceptance.md 与 research/review.md。
