# V1-01 提交计划（已获确认）

建议一个工作提交：

`feat(execution): add scheduled workers and owner-confirmed run recovery`

## 本提交文件

### 代码、初始化、测试与运行配置

- `.env.example`
- `backend/alembic/versions/0001_initial.py`
- `backend/pyproject.toml`
- `backend/src/stocks/api.py`
- `backend/src/stocks/execution/__init__.py`
- `backend/src/stocks/execution/__main__.py`
- `backend/src/stocks/execution/config.py`
- `backend/src/stocks/execution/scheduler.py`
- `backend/src/stocks/execution/service.py`
- `backend/src/stocks/models.py`
- `backend/src/stocks/settings.py`
- `backend/src/stocks/worker.py`
- `backend/tests/ac3_drill.py`
- `backend/tests/test_execution.py`
- `backend/tests/test_scheduler.py`
- `compose.yaml`
- `config/tasks.yaml`

### 归属文档与工程规范

- `docs/data.md`
- `docs/execution.md`
- `docs/quickstart.md`
- `docs/validation.md`
- `.trellis/spec/backend/index.md`
- `.trellis/spec/backend/testing-guidelines.md`
- `.trellis/spec/backend/worker-guidelines.md`

### 本任务规划、验收与记录

- `.trellis/tasks/10-20-v1-01-worker-scheduling/check.jsonl`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/design.md`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/implement.jsonl`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/implement.md`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/prd.md`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/task.json`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/acceptance.md`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/review.md`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/research/kickoff-review.md`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/research/remote-verification.md`
- `.trellis/tasks/10-20-v1-01-worker-scheduling/commit-plan.md`

## 现有无关工作（排除）

以下是本任务开始前已存在或其他窗口的 Pi/Trellis 配置改动，本提交全部排除：

- `.agents/skills/trellis-continue/SKILL.md`
- `.agents/skills/trellis-start/SKILL.md`
- `.pi/agents/trellis-check.md`
- `.pi/agents/trellis-implement.md`
- `.pi/extensions/trellis/index.ts`
- `.pi/prompts/trellis-continue.md`
- `.pi/settings.json`
- `.trellis/model-routing.md`
- `.trellis/tasks/10-21-pi-trellis-magpie/`

若确认后出现清单外的新改动，先归类，不能静默纳入。

## 验证与限制

- 唯一测试服务器的隔离环境：142 项相关 pytest 通过；全新空库 upgrade/check 通过。
- Compose 配置通过，测试专用 pip 镜像副本构建通过；仓库默认 PyPI 构建在180秒内未完成下载。
- 容器 CLI/Worker smoke 9 阶段通过；最终演练脚本真实 SIGKILL/end/retry 18/18检查通过。
- 本任务远程容器、镜像、目录及演练文件已清理，未动其他任务资源。
- 不将本轮 mock 模型演练写成 PA-1 真实模型闭环通过；ARM/长期部署仍属后续任务。

Owner 已明确授权“提交并推送，然后开始下一个任务”。只提交上述工作文件并推送当前分支；归档与日志按 Trellis 后续流程单独记录。此前“不 push”的计划由本次授权替代。
