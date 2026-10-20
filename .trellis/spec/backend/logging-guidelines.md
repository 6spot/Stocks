# 日志规范

应用日志使用 Python 标准 `logging`，按模块取得 logger。记录操作失败时，给出定位所需的操作名、对象标识或请求关联信息，并保留异常栈；避免无意义的逐条成功日志和重复记录同一异常。

日志不记录密码、API key、Authorization 头或完整外部响应正文。必要的运行结果应写入相应持久化记录，不能只留在日志中。`backend/alembic/env.py` 使用 `logging.config.fileConfig` 和 `backend/alembic.ini` 管理 Alembic 日志；应用代码不要依赖 Alembic 的日志配置。

不预设统一 JSON 字段、额外日志服务或固定等级表。引入这些机制时，以实际部署和排障需求为依据，并更新此规范。
