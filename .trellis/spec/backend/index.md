# 后端开发规范

本目录只规定工程做法：代码组织、持久化、错误、日志和验证。产品语义与字段契约由 `docs/` 中的归属文档维护，不在开发规范中重述。

| 主题 | 适用范围 |
| --- | --- |
| [目录与模块](directory-structure.md) | 放置新代码、拆分职责 |
| [数据库](database-guidelines.md) | 模型、查询、事务、初始化结构 |
| [Worker 与调度](worker-guidelines.md) | Worker/调度/文档阶段领取、事务与 Session 新鲜度、所有权 fence、空闲等待与重试 |
| [错误处理](error-handling.md) | 配置、外部请求、API 失败路径 |
| [HTTP 读取接口](api-guidelines.md) | 报告/证据/原件只读路由、按引用版本读取、安全响应头与状态码 |
| [日志](logging-guidelines.md) | 运行诊断与敏感信息处理 |
| [质量与验证](quality-guidelines.md) | 代码审查与测试选择 |
| [测试环境](testing-guidelines.md) | 仅限测试服务器、隔离数据库与资源清理 |

## Quality Check

按[质量与验证](quality-guidelines.md)选与改动对应的最小检查；数据库结构变更再按[数据库规范](database-guidelines.md)检查新库初始化。检查代码、配置、测试与归属文档是否同步。
