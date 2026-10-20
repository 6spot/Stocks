# V0-05 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；确认依赖已归档的 V0-04 Chunk/检索能力。
- [x] 重新检查现有代码及测试，落实建议路径、具体 Schema 和必要技术决策。
- [x] 用户已授权顺序实施；已建立 `feat/v0-05-manual-task-agent` 并运行 task.py start。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线，派发前按实际改动裁剪复核。

## 执行顺序

- [x] 设计最小 Task/Run Schema、当前模型和初始化定义。
- [x] 实现配置校验、manual 顺序执行及状态持久化。
- [x] 接入 Provider 输出/调用归一化与错误边界并给出可运行示例。

## 最小必要验证

- 在测试服务器 `100.120.14.84` 的任务隔离目录中运行相关测试与代码检查；本机不执行任何测试或运行时验证。
- 用测试服务器的隔离新库验证运行互斥和初始化结构；mock Provider 验证故障分类。

新增测试名为计划，文件存在后才执行；不得把本文件命令写成已通过。按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制。
- [x] 契约变更只更新唯一归属文档，运行能力更新 quickstart。
- [x] 检查后续依赖与阶段验收；保留原始证据、输入快照及最后成功结果。
- [x] 完成实现/质量检查后按 Trellis 工作流交付。

## 实际验证

- 测试服务器：`100.120.14.84`；Python 3.12.15；事务隔离代码目录和独立数据库。
- 复用了已存在的 V0-03 pgvector 容器，只创建本任务独立的测试角色和数据库；所有测试在
  `test_execution.py`、`test_providers.py`、`test_embeddings.py`、
  `test_search_cli.py`、`test_repository.py`、`test_processing.py`、`test_api.py`、
  `test_collection.py`、`test_s3.py` 中通过，合计 `153 passed`。
- 全新空库的 `alembic upgrade head` 与 `alembic check` 通过；
  `python -m compileall -q src tests` 通过。
- 失败与限制记录见 [acceptance.md](acceptance.md)。
