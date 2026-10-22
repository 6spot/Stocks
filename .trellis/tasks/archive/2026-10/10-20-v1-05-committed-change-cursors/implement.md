# V1-05 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务：[V1-04](../10-20-v1-04-facts-api-mapping/prd.md)
- [x] 重新检查现有代码及测试，落实建议路径、具体 Schema 和必要技术决策。
- [x] 用户已授权“继续任务”；Trellis 当前任务已启动。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线，派发前按实际改动裁剪复核。

## 执行顺序

- [x] 列出所有研究可见写入点和统一提交协议。
- [x] 修正原始变更页查询：SQL 先过滤后分页，返回原始续点与真实完成状态。
- [x] 增加 Run 扫描进度、窗口展示时间和 Evidence Package 扫描快照。
- [x] 接入 Run 开始扫描、未完成窗口恢复、retry 复制与逐页 checkpoint。
- [x] 调整最终事务，使最终 Step、Analysis、Report、成功游标与 Run 成功状态原子提交；扫描未完或报告缺失不推进。
- [x] 使用真实 PostgreSQL 双连接/事务并发测试 AC-1；使用 Execute Run 集成测试验证 AC-2 至 AC-4、续扫、retry 和失败回滚。
- [x] 同步 `docs/data.md`、`docs/execution.md`、`docs/validation.md` 与 `docs/quickstart.md` 的实际能力边界。

## 最小必要验证

- 在测试服务器任务隔离目录设置 `TEST_DATABASE_URL` 后运行
  `backend/.venv/bin/python -m pytest -q tests/test_change_watermarks.py`。
- 运行受影响的 `tests/test_execution.py`、`tests/test_research.py`、`tests/test_collection.py`、
  `tests/test_classification.py`、`tests/test_facts.py` 相关用例或文件，覆盖改动到的 Step/事务路径。
- 结构变更后使用全新空库执行 `alembic upgrade head` 与 `alembic check`。
- 必须用真实 PostgreSQL 双连接/事务并发测试；不得仅用单连接、mock 或时间字段排序证明不漏数。
- pytest 若因缺少 `TEST_DATABASE_URL` 跳过数据库用例，不得记作通过。

新增测试名为计划，文件存在后才执行；不得把本文件命令写成已通过。按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制。
- [x] 契约变更只更新唯一归属文档，运行能力更新 quickstart（据上方文档同步记录及归档 notes 补齐）。
- [x] 检查后续依赖与阶段验收；保留原始证据、输入快照及最后成功结果（既有 AC-1 至 AC-4 验证和后续任务已记录，跨阶段待验收项仍保留）。
- [x] 已完成实现/质量检查并按 Trellis 工作流交付归档；本项据既有归档记录补齐。

## 上下文读取注意

`docs/data.md` 当前为 34,267 字节，超过默认单文件注入上限 32,768 字节。自动注入可能截断末尾；派发/开工时须完整读取原文件，尤其末尾检索与来源安全契约。不要把已注入前缀当成全文；无需为此修改领域文档或 Trellis 全局配置。
