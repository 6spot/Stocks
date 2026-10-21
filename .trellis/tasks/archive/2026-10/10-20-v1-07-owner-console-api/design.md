# V1-07 技术方案

## 边界与数据流

- 单 Owner 使用独立密码与签名密钥，登录后签发的短期 Cookie 是唯一的身份凭据，
  不建立用户表、角色系统或兼容会话格式。所有非公开 HTTP 路由统一经过应用级中间件；
  原件下载不能绕过认证，所有变更请求要求与会话绑定的 CSRF token。
- API 只组织现有 Repository/Task 能力并返回已持久化的安全字段；不在读取路由中采集、
  解析、索引或调用模型。恢复路由只排队或委托既有服务，Worker 实际执行。
- 查询响应使用有界 `limit` 与稳定游标。来源最近采集 Run 使用每页 Source 的有界查询，
  不以展开全部历史 Run 的窗口排名代替分页。`task_runs.scope` 的 GIN 索引为新查询
  提供可观察的数据库结构支持；当前新库初始化定义直接包含该索引。
- 来源三类状态分离：采集结果由适用的 Collection Run 与持久采集状态决定；更新预期
  按 `source_updated_at`（缺失时 `published_at`）判断，采集时间单列；后处理积压来自
  Document 阶段状态，不推断为采集失败。

## 建议代码落点

- `backend/src/stocks/auth.py`：会话签发、验证、失败限流。
- `backend/src/stocks/api.py`：应用级访问边界、查询和恢复路由、安全响应投影。
- `backend/src/stocks/collection/config.py`：可选 `expected_freshness` 配置校验。
- `backend/src/stocks/models.py`、`backend/alembic/versions/0001_initial.py`：当前新库
  的 Task Run Source 范围索引。
- `backend/src/stocks/facts.py`、`repository.py`：有界查询复用。
- `backend/src/stocks/settings.py`、`.env.example`：Owner 凭据与配置路径。

标注拟新增或随前置任务确定的路径是建议，不代表代码已存在；后续以实际模块组织为准。

## 输入输出与契约

`docs/interface.md`, `docs/execution.md`, `docs/data.md`, `docs/validation.md` 是唯一业务契约来源。输入引用前置任务的持久记录，输出满足 PRD 各 AC；不把本任务文档变成另一套字段规范。跨层 Schema、事务及失败语义在开工复核中结合已落地代码确定。

列表统一返回 `items`、`next_cursor`、`has_more`、`limit`；报告与 Document 的详情
按证据包引用版本读取，原件入口是受保护的相对路径，不跟随最新采集指针。Run、步骤、
模型调用、快照、错误及范围对象经同一递归投影过滤凭据、Provider 端点与 URL query/
fragment；错误字段另外去除响应正文、堆栈和嵌套细节。公开登录和 `/health` 以外的
路由不提供 OpenAPI 文档。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。外部服务凭据/目标环境缺失时保留验证限制，不标记实际接通。后续任务不得把本任务未完成能力当已实现。

会话签名为无状态 HMAC 短期令牌，密钥轮换会使现有会话失效；部署必须同时配置密码与
至少 32 字符的独立签名密钥，HTTPS 环境启用安全 Cookie。失败登录限流是单进程内存
边界，当前单 API 进程部署适用；若未来扩展多实例，需要重新设计共享限流状态。
