# V0-01 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务：无业务任务前置；可先行细化。
- [x] 重新检查现有代码及测试，落实建议路径、具体 Schema 和必要技术决策。
- [x] 用户确认本子任务的最终实施范围；之后才运行 task.py start。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线，派发前按实际改动裁剪复核。

## 执行顺序

- [x] 调整当前模型与初始化定义，删除被替代约束或字段。
- [x] 实现统一输入、版本判断、事务和原件读取基础。
- [x] 增加版本/失败边界集成验证并同步 data.md 与 quickstart.md 的实际能力。

## 最小必要验证

- cd backend && python -m pytest -q tests/test_repository.py tests/test_s3.py
- 隔离的新开发库：`alembic upgrade head`；随后 `alembic check`；未使用或清理已有证据库。
- 本机缺少 Python 3.12/Docker 或 PostgreSQL 时，按[测试环境规范](../../../../spec/backend/testing-guidelines.md)使用远程 Docker 测试机；不得用 Python 3.9 结果替代。

实际验证：远程 Python 3.12 容器执行全新 pgvector 数据库
`alembic upgrade head` 成功，`alembic check` 无差异；后端全量测试
`11 passed`。远程测试容器结束后清理，不保留测试凭据。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制。
- [x] 契约变更只更新唯一归属文档，运行能力更新 quickstart。
- [x] 检查后续依赖与阶段验收；保留原始证据、输入快照及最后成功结果。
- [ ] 完成实现/质量检查后按 Trellis 工作流交付，不在本轮提前归档或标完成。

## 上下文读取注意

`docs/data.md` 当前为 34,267 字节，超过默认单文件注入上限 32,768 字节。自动注入可能截断末尾；派发/开工时须完整读取原文件，尤其末尾检索与来源安全契约。不要把已注入前缀当成全文；无需为此修改领域文档或 Trellis 全局配置。
