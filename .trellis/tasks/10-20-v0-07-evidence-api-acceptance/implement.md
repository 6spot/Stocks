# V0-07 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务已归档：V0-02、V0-06、V0-08。
- [x] 重新检查现有代码及测试，落实实际落点：仅 `api.py` 新增只读路由，无新表/字段/迁移（详见 design.md 开工复核段）。
- [x] 用户已确认按任务列表顺序实施本子任务，已运行 task.py start。
- [x] implement.jsonl / check.jsonl 已含真实规范与基线，本轮未新增文件归属层。

## 执行顺序

- [x] 补齐只读报告/证据 API 及安全原件响应。
- [x] quickstart 已记录实际可运行阅读命令与安全语义；`compose.yaml` 未改（本轮无部署变更）。
- [ ] 执行 V0 全链路与版本/失败演示：版本/失败演示已完成，**真实模型全链因缺凭据未验收**。

## 最小必要验证（实际结果）

均在测试服务器 `100.120.14.84` 的 `/tmp/stocks-v0-07-evidence-api-01` 执行，详见
[验证记录](research/verification-2026-10-20.md)：

- `pytest -q tests/test_report_api.py` → 7 passed（复核后补强为 9 passed 与 test_api.py 合跑）
- `pytest -q tests/test_api.py tests/test_repository.py` → 10 passed（`api.py` 属共享文件，防回归）
- `alembic upgrade head` + `alembic check` → 全新库无漂移
- 真实 S3 `store_snapshot` 写入 + uvicorn `127.0.0.1:8123/8124` 全路由 smoke → 两次均通过（含安全头、404/422 分支、UUID 归一化）
- 未执行：真实模型研究 + 真实 SEC 采集入库（缺 `MODEL_API_KEY`/`MODEL_NAME`/可用 embeddings 端点）

按风险验证关键行为，未跑与本次改动无关的全量套件。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制（prd.md + research/verification-2026-10-20.md）。
- [x] 契约未改字段语义；只更新归属文档中的实现状态叙述与 quickstart 运行能力。
- [x] 已核对后续依赖：V0 仅剩本任务；V1-01…V1-09 不依赖未实现的阅读 API 能力。
- [x] 质量检查与验证已跑；按用户指示不提前归档，待真实闭环凭据到位后补 AC-1。
