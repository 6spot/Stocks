# V1-09 技术方案

## 边界与数据流

- 保持单机和现有服务边界；部署工作不新建 Source Monitor、队列或数据库。
- CI 选择受影响的构建/Compose/新库/API 检查，真实目标部署验收单独留痕；无目标环境明确未完成。

## 建议代码落点

- `compose.yaml`
- `backend/Dockerfile`
- `frontend/（已有）`
- `deploy/（Nginx 配置拟新增）`
- `docs/quickstart.md`

标注拟新增或随前置任务确定的路径是建议，不代表代码已存在；后续以实际模块组织为准。
仓库不使用 GitHub Runner CI；Compose、镜像和部署验收只在测试服务器执行，已有说明见
`docs/quickstart.md` 与后端的远程测试规范。

## 输入输出与契约

`docs/architecture.md`, `docs/execution.md`, `docs/interface.md`, `docs/validation.md`, `docs/quickstart.md` 是唯一业务契约来源。输入引用前置任务的持久记录，输出满足 PRD 各 AC；不把本任务文档变成另一套字段规范。跨层 Schema、事务及失败语义在开工复核中结合已落地代码确定。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。外部服务凭据/目标环境缺失时保留验证限制，不标记实际接通。后续任务不得把本任务未完成能力当已实现。
