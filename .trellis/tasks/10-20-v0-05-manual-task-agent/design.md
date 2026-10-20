# V0-05 技术方案

## 边界与数据流

- 一个轻量执行入口与受控步骤注册，不建 DAG 或通用工作流平台。
- PostgreSQL 保存 Run/StepResult/占用；V0 手动执行流程，V1 扩展独立 Worker，不预建定时或租约系统。

## 建议代码落点

- `backend/src/stocks/providers.py`
- `backend/src/stocks/models.py`
- `backend/alembic/versions/0001_initial.py`
- `config/（Task、Agent、Prompt 拟新增）`
- `backend/src/stocks/execution/（拟新增）`

标注拟新增或随前置任务确定的路径是建议，不代表代码已存在；后续以实际模块组织为准。

## 输入输出与契约

`docs/execution.md`, `docs/research.md`, `docs/validation.md` 是唯一业务契约来源。输入引用前置任务的持久记录，输出满足 PRD 各 AC；不把本任务文档变成另一套字段规范。跨层 Schema、事务及失败语义在开工复核中结合已落地代码确定。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。外部服务凭据/目标环境缺失时保留验证限制，不标记实际接通。后续任务不得把本任务未完成能力当已实现。
