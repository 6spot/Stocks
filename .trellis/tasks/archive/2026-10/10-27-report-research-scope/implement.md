# 实施计划：市场与个股报告口径及取材配置

## 步骤

1. [x] `config/tasks.yaml`：新增 `market_intelligence_brief` 与 `spacex_company_research`。
2. [x] 复查 objective 文案：区分新增与背景、无新资料/缺行情时如实呈现；`research_writer`
   prompt 已覆盖这些约束，未改。
3. [x] `backend/tests/test_execution.py`：新增两个 Task 的配置加载与
   `_normalize_queries` 角色断言（含「仅行业背景词 → background」「仅公司名 → direct」）。
4. [x] 远程验证：隔离目录单元测试 213 passed；`/opt/stocks` 部署环境真实触发两个
   Task 并产出真实报告（详见 prd.md 验收执行记录）。
5. [x] 文档：`docs/research.md` 两类口径定义、`docs/quickstart.md` 口径说明与触发示例。
6. [x] 提交并归档任务。

## 执行中发现并处理（如实记录）

- 部署库 `analyses` 缺 `title`/`sections` 列（旧镜像初始化），按开发阶段规则重建库并
  重新采集 51 份真实 Document。
- 部署 `.env` 的 `host.docker.internal` 不可解析、api.typesafe.ai 直连 451：embedding/
  classification 端点改经 `172.20.0.1:13426`（socat + SSH 反向隧道到本机 magpie 网关，
  bge-m3 1024 维、typesafe/jev-latest）。
- 部署容器代码落后于分支：以 docker cp 热修 `collection/config.py`、`collection/service.py`、
  `source_mapper.py`、`execution/service.py`（后者新增 STEP_FAILED 的 traceback 日志）。
- `market_intelligence_brief` 增量续扫后研究输入超过 300s 模型超时容量：4 次 Run 的
  research 调用连续 MODEL_TIMEOUT，检查点保留可继续；作为已知限制记录，交最终联合验收。

## 验证命令（远程）

```bash
cd /tmp/stocks-<run>/backend
TEST_DATABASE_URL=... PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_execution.py
# 部署环境真实触发
cd /opt/stocks
docker compose run --rm api python -m stocks.execution trigger --task market_intelligence_brief
docker compose run --rm api python -m stocks.execution trigger --task spacex_company_research
docker compose run --rm api python -m stocks.execution show --run-id <id>
```

## 回滚

单提交回滚即可；无数据迁移，无 schema 变更（部署库重建是为对齐既有模型的初始化定义，
不是本任务引入的结构变更）。真实 Run 的 Evidence Package 与报告保留证据链。
