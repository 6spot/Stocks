# 日志规范

应用日志使用 Python 标准 `logging`，按模块取得 logger。记录操作失败时，给出定位所需的操作名、对象标识或请求关联信息；内部开发诊断默认保留异常栈，但日志可能进入长期运行环境或第三方收集器时，只记录脱敏的异常类型和稳定对象标识，完整异常通过本地调试复现。避免无意义的逐条成功日志和重复记录同一异常。

日志不记录密码、API key、Authorization 头或完整外部响应正文。必要的运行结果应写入相应持久化记录，不能只留在日志中。`backend/alembic/env.py` 使用 `logging.config.fileConfig` 和 `backend/alembic.ini` 管理 Alembic 日志；应用代码不要依赖 Alembic 的日志配置。

采集入库失败可记录 `source_id`、`dedupe_key` 与异常类型，但不能记录完整 URL 查询串、请求头、外部响应正文或对象存储凭据。CLI 的逐条结果和持久化 `fetch_error` 同样只使用稳定错误字段。

入口进程必须显式配置根 logger，否则容器内 INFO 级日志不可见。`backend/src/stocks/worker.py` 与 `backend/src/stocks/processing/__main__.py` 的 `main()` 已调用 `logging.basicConfig(level=logging.INFO, ...)`；新增独立入口进程时同样遵守，库模块不调用 `basicConfig`。

不预设统一 JSON 字段、额外日志服务或固定等级表。引入这些机制时，以实际部署和排障需求为依据，并更新此规范。
