# MP-01 配置一致性设计

## 边界与现状

用户已于本轮明确授权开始实现既有任务。API 使用 Settings 中 sources/tasks/models/tools 路径；WorkerContext.from_settings、Worker CLI 和 Compose command 却重复写死前三项默认路径。手动 collection/execution/processing/indexing/search CLI 存在相同路径默认值问题。问题发生在入口参数解析与上下文构造，不在 TaskRun 快照或 Repository。

## 方案

- Settings 是部署默认值的唯一来源，优先级为显式 CLI 参数 > Settings（环境变量/.env）> Settings 内置 config/*.yaml 默认值。WorkerContext.from_settings 保留显式 overrides，未提供路径时采用本次 Settings 值。
- CLI 仅对已支持的路径参数应用 Settings 默认值，保留既有参数名称、错误输出与按需加载；help/纯读取命令不应因为提前解析 Settings 而新增凭据要求。processing status 的 registry 使用 Settings sources 路径，retry/reprocess/index 使用所选 models/sources 路径。
- Compose Worker 仅运行 python -m stocks.worker，让 api/worker 共享 env_file 与 config 挂载；不在 command 中覆盖环境设置。自定义文件必须可从两个容器内同一路径读取。
- tools 已由 Worker context.settings.tools_config_path 传给调度与执行；execution CLI 默认值同步 Settings。分类词表目前没有 Settings 字段，继续共享 config/taxonomy.yaml 与现有显式内部 taxonomy_path 注入，不另加配置机制。
- 不改变模型快照核对、配置错误、凭据惰性解析、队列和调度、数据库模型，也不涉及 MP-02。

## 验证

在 100.120.14.84 的独立任务目录记录旧 Worker 路径与自定义 Settings 不一致；修复后以与默认来源/任务/模型明显不同的文件，通过已认证 API 入队再由真实 Worker 服务执行，核对 Run/ModelCall/来源身份。外部 HTTP 与存储可使用受控 transport，不能宣称真实供应商或真实来源验收。补充默认、显式覆盖、缺失/非法配置定向回归，并在同机解析 Compose 默认与自定义配置。无迁移与兼容逻辑；回退仅撤销本任务代码/部署变更，不改写运行证据。
