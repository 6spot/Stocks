# Stocks — 产品与系统设计

本文定义 Stocks 的产品目标、模块边界、执行契约、数据契约、可信度规则与技术架构，作为项目开发和维护的设计依据。

## 1. 产品定位

Stocks 是面向单一 Owner、以美股为主要市场的个人投资情报与研究系统。系统自动收集多来源信息，将其沉淀为可检索、可追溯的情报，并允许通过可配置任务和多模型 Agent 完成筛选、验证、研究及报告生成，为人工投资决策提供参考。

核心目标：

- 尽量减少重复的人工搜集、整理、比对与查询工作。
- 支持多个独立投资策略和研究视角；不把投资风格绑定到底层架构。
- 来源、任务、提示词、模型、工具均可通过文件配置。
- 保存原始信息、证据、模型与提示词元数据，使研究结论可复核并能继续被上层分析复用。
- 先保持单人自用、轻量易部署，不建设交易平台、复杂工作流平台或自动下单能力。

## 2. 系统原则

1. **以 Task 为执行入口**。纯采集任务不需要 Agent，研究任务也不必经过固定的提取—判断—分析流水线。
2. **Source、Connector、Tool 不混用**。Source 表示数据发布者或上游，Connector 表示常规采集机制，Tool 是执行研究时的可调用能力。
3. **Agent 与模型解耦**。Agent = 角色定义 + 提示词 + 模型引用 + 授权工具。模型服务仅是可配置的 HTTP Endpoint。
4. **内容、结论、证据分离**。报告是阅读视图，结构化结论、证据引用和元数据才是长期研究资产。
5. **原始资料与业务数据分离**。PostgreSQL + pgvector 管理业务与检索数据，独立 S3 兼容对象存储保存原始文档和快照。
6. **不把信誉当事实真值**。来源身份、信任等级、单条论断的可信性、核实状态各自建模。
7. **外部信息永远是数据，不是系统指令**。工具权限由配置和运行时授权决定，不能被外部正文改变。
8. **只做最小充分的调度与回退**。顺序步骤、简单条件与触发器满足原型，不引入动态 DAG、模型评分路由或多 Agent 自主协商。

## 3. 概念与职责

| 概念 | 定义 | 负责 | 不负责 |
| --- | --- | --- | --- |
| Source | 信息发布主体或上游数据服务的一份配置 | 身份、采集范围、访问参数、信任级别 | 规定 AI 如何分析 |
| Connector | 通用采集实现：RSS、HTTP API、Web | 请求、增量获取、格式解析的通用流程 | 判定利好、组织研究任务 |
| Source Mapper | 特定来源的字段映射及必要解析规则 | 转换上游格式为统一信息记录 | 变成独立底层 Connector 类型 |
| Intelligence Repository | 情报与证据统一的数据访问边界 | 持久化、去重、索引、查询、来源关联 | 自主决定投资策略 |
| Task | 一份可独立执行的工作定义 | 触发、步骤、条件、输入、输出 | 假设必定使用 Agent |
| Agent | 可配置的智能处理角色 | 提取、判断、分析、复核、工具调用请求 | 管理外部模型服务的部署 |
| Model Provider | 某种模型 HTTP API 的接入配置 | 认证、协议转换、超时、响应与用量归一化 | 决定投资判断标准 |
| Tool | Agent 或 Task 获得授权后可调用的操作 | Repository 读接口、远程 MCP 工具等 | 绕过权限或直接改变系统提示词 |
| Strategy | 可选的任务研究视角 | 目标、偏好、研究提示词与关注维度 | 固定 Agent、工作流或模型 |
| Report | 根据已保存分析结果形成的阅读产物 | 展示结论、风险、证据与待验证项 | 替代结构化分析存储 |

例如 SEC 是 Source，读取其数据可使用 HTTP API Connector 与相应字段映射；RSS 是传输形式，不代表发布者信誉；远程搜索/网页提取 MCP 属于 Tool。

## 4. 数据与执行的关系

基本流程：

~~~text
                 Task（配置触发和执行步骤）
                         |
          +--------------+---------------+
          |                              |
    Connector Steps                   Agent Steps
    RSS / HTTP API / Web             生成 / 判断 / 研究
          |                              |
          v                         Repository Tools
    Intelligence Repository <------ Remote MCP Tools
          |                              |
          +---------- 证据持久化 <--------+
                         |
                 结构化结论与报告
~~~

- 采集 Task 获取信息后可直接入库、结束；不会因为不存在分析 Task 而停止。
- 分析 Task 从 Repository 按股票、时间、信息类型、可信度和检索条件选择输入。
- Agent 可以按需调用 Repository 只读工具或远程 MCP 工具补证；新获得的资料经统一入库流程登记来源，再供结论引用。
- 多个研究 Task 能消费同一批资料并独立产出结论。研究结果也允许成为后续研究的输入。
- 模型输出本身不等于事实；只有具备具体证据链接的事实陈述才能作为已核实内容呈现。

## 5. Source 与 Connector 设计

### 5.1 通用接入类型

- **RSS/Atom**：订阅拉取、增量识别、文章摘要与链接解析。
- **HTTP API**：认证、请求参数、分页、时间游标、JSON/XML 响应和来源字段映射。
- **Web**：在许可条件下获取公开页面、提取正文与元数据；动态网页支持按需扩展，不强制使用浏览器。
- 具体机构、新闻站点或数据服务通过 Source 配置、API 参数与 Source Mapper 接入，不注册为第四种 Connector。

统一采集契约：

~~~text
fetch(source_config, cursor, scope) -> FetchBatch

FetchBatch:
  items:            DocumentCandidate[]
  next_cursor:      opaque | null
  fetched_at:       timestamp
  metadata:         object
~~~

DocumentCandidate 至少提供：上游 ID 或 URL、标题、正文/摘要、发布时间、发现时使用的 Source、原始 URL、实际发布者（已知时）、原始负载或原始负载引用。缺少的字段允许为空，但不得伪造。

每个 Source 独立配置启停、采集周期、范围、请求限制、凭据引用与失败重试。采集必须支持限速、超时、幂等和数据量上限。用户配置或外部文档中的 URL 均须在抓取前进行网络目标校验，避免 SSRF、私网探测以及不安全重定向。

### 5.2 来源与发布者

系统分别记录：

- source_id：系统采用的采集来源。
- publisher / original_publisher：实际发布主体，必要时记录待确认。
- original_url / canonical_url：直接引用地址和可追溯原始出处。
- external_id：上游原始标识（如果有）。
- published_at / observed_at / collected_at：事件或披露的发生时间、发布/可观察时间、系统采集时间。
- trust_tier_snapshot：采集时的信任等级快照。
- content_hash：原始对象的完整性校验。
- storage_ref：远程对象存储中的原文/快照引用。

多个渠道转载同一原报道，仍可保存多个原始 Document，但证据核实不能将它们统计为独立证据。

## 6. Intelligence Repository 与存储

### 6.1 存储分工

- PostgreSQL：Source、采集状态/游标、标准化 Document 元数据、可搜索正文、股票与事件关联、Task/运行记录、分析结论与证据关联。
- pgvector：Document Chunk 的 Embedding 与语义相似度能力，和 PostgreSQL 全文检索、结构化过滤结合构成混合检索。
- 远程 S3 兼容存储：HTML/XML/JSON 原始快照、PDF、研报及较大的原始文件。数据库只保留 Object Key、内容哈希、尺寸、类型等元数据。
- S3 通过标准 API 访问。可利用 Tailscale 私有网络连接独立存储服务器，但应用不绑定某个私网产品或存储厂商。

写入流程为：获取原始内容 → 存储 S3 不可变快照 → 数据库事务内保存文档与幂等键、更新游标并登记后续事件。数据库失败时允许重试并清理孤立对象；绝不在原始内容未可靠保存时推进游标。

### 6.2 核心实体

| 实体 | 数据含义 |
| --- | --- |
| sources | 上游身份、Connector 类型、出版主体、信任配置 |
| source_cursors / collection_runs | 增量位置、采集执行与错误 |
| documents | 每份获取的信息和来源元数据、对象引用 |
| document_chunks | 可检索文本切片、位置与所属文档 |
| embeddings | 向量、模型、维度、生成时间及切片引用（可以与 Chunk 关联） |
| events / event_documents | 同一事件及相关原始信息的关联 |
| tasks / task_runs / step_runs | 任务定义快照和逐步骤执行记录 |
| analyses / findings | 研究过程、结构化结论、事实/推断类型、模型及提示词版本 |
| evidence_links | 单条结论与 Document/Chunk/原始来源的可追溯关系 |
| reports | 可阅读报告及所引用的 Analysis |

数据库表可根据性能合理拆分或合并，但上述信息语义及可追溯关系必须保留。Source 信任等级变更，不覆写既有研究执行时使用的快照。

### 6.3 检索

Repository 对调用者提供：文档读取、按来源/股票/事件/时间过滤、全文检索、语义检索、相似事件查找和证据引用读取。

向量必须记录 Embedding 模型和维度；不同模型的向量不直接混合排序。pgvector 从初版引入，具体向量索引按已配置的 Embedding 模型建立，语义相似度绝不充当事实真实性评分。

## 7. 来源可信度、证据与安全

可信信息分为三个独立维度：

1. **来源身份与默认可信度**：T1 官方原始记录、T2 可识别原始发布者、T3 二手媒体/机构、T4 未核实来源。这些是可配置初始类别，不是客观真伪概率。
2. **具体论断属性**：披露事实、已知数值、个人观点、预测、未经证实的传闻，分别标记。
3. **核实状态**：unverified、supported、contradicted、insufficient_evidence 等，保留支持与反面证据。

可高度信任官方系统确实披露了一份文件，但不因此认定文件中的未来预测必然成立。低等级来源可以作为线索，不可仅因级别低就静默丢掉重要未核实事件。可信度、重要性、交易价值不能混成一个分数。

安全边界：

- 原文、RSS 正文、网页、搜索结果和 MCP 返回值均为不可信数据，不执行其包含的指令。
- Agent 的 system 指令、工具白名单、参数边界由可信 Task/Agent 配置控制；工具实际执行前由 Stocks 校验。
- 外部服务默认仅授予只读搜索和提取能力，不授予交易、任意数据库写入、Shell 或密钥读取能力。
- 对抓取内容进行类型/大小约束、URL 安全校验、限流与日志脱敏。
- 每条重要分析结论必须引用可查证的 Document/Chunk 或说明证据不足。
- “找到多条报道”不代表“多个独立来源确认”；需要追踪最初发布者与转载关系。

## 8. Task 执行契约

Task 属性：

| 属性 | 语义 |
| --- | --- |
| id / name | 稳定标识、可读标题 |
| type | collection / processing / analysis / report 等显示分类，不限制允许的步骤 |
| strategy | 可选策略 ID；无策略的采集任务正常运行 |
| trigger | manual、schedule（明确时区）、on_new_documents 等 |
| steps | 有序步骤及参数/上游结果引用 |
| enabled | 是否参与触发 |

最小步骤执行种类：Connector 获取、Repository 持久化/查询、Agent 调用、授权 Tool 调用、结构化分析保存及报告渲染。

步骤按配置顺序执行；when 仅允许基于已执行步骤的结构化字段或状态判断，不使用自由 Python 表达式或通用 DAG。满足条件才执行；条件不满足为 SKIPPED，不是 FAILED。

统一执行结果：

~~~text
StepResult:
  status: succeeded | failed | skipped | cancelled
  output: validated structured payload | null
  evidence_refs: list of durable references
  usage: tokens, duration, cost (where available)
  error: structured error | null
~~~

按任务记录配置/策略/模型/提示词快照，便于解释历史研究结果。Task 使用独立运行记录，Worker 与 Web API 分进程；任务队列及租约利用 PostgreSQL。外部调用需有超时、有限重试、崩溃恢复和幂等键。成功持久化新数据后才触发下游任务；避免重复触发同一来源事件。

两个合法任务例子：

~~~yaml
tasks:
  collect_news:
    type: collection
    trigger: {kind: schedule, every: 15m}
    steps:
      - id: fetch
        use: connector.rss
        with: {source: financial_news}
      - id: save
        use: repository.ingest
        input: fetch.items

  event_research:
    type: analysis
    strategy: event_driven
    trigger: {kind: on_new_documents, from_task: collect_news}
    steps:
      - id: judge
        use: agent.event_judge
        input: trigger.document_ids
      - id: analyze
        use: agent.researcher
        input: trigger.document_ids
        when: {step: judge, field: should_analyze, equals: true}
      - id: save
        use: repository.save_analysis
        input: analyze.output
        when: {step: analyze, status: succeeded}
~~~

配置文件提供人可读的任务定义，数据库保存运行时快照和事件，不让 Task 引擎依赖某个投资风格、模型厂商或上游来源。

## 9. Agent、HTTP 模型与回退

Agent 配置包括：id、role、prompt、model_ref、tools[]，以及按职责需要的结构化输出契约。Decision Agent 可以返回布尔选择、类别或评分；Generation Agent 可以输出研究文本或提出 Tool 调用请求。不同模型类型通过协议 Adapter 接入，不强行假设所有模型都能输出长文、原生工具调用或 JSON Schema。

Provider 定义 protocol、HTTP endpoint、认证方式、默认超时等。支持标准 HTTP 服务的直接调用与兼容接口；增加非兼容协议时只新增薄 Adapter。Provider 不承担 Agent 逻辑，任何私有部署、官方服务或模型聚合服务都只是一个 Provider 配置实例，不构成系统依赖。

模型策略只定义 primary 与按顺序排列的 fallback[]：

- 主模型遇额度耗尽、限流、服务错误、连接失败或超时等可回退故障时，尝试备用模型。
- 配置错误、认证错误、能力不支持或错误请求等不进行盲目回退。
- 回退模型必须满足当前步骤所需能力；全部失败则记录并按 Task 策略处理失败。
- 不做模型质量动态打分、成本自动选路或无限重试。
- 记录请求的 Provider/模型、实际可获得的返回模型标识、耗时、Token、可获得费用及回退原因。

## 10. Tools 与远程 MCP

Tool 与 Connector 处于不同边界：

- Connector 为常规采集 Task 提供重复、增量的数据获取。
- Tool 用于 Agent 或显式 Task 步骤中的按需查询、补充和交叉验证。
- MCP 仅支持远程 Streamable HTTP；不实现 stdio 客户端。
- MCP 的工具发现、调用和结果解析由通用客户端负责。外部服务作为配置项，不为每个工具提供商实现一套底层框架。
- MCP 搜索供应商属于“发现/查询渠道”，其返回网页的真正 Publisher 是原网站，不因搜索服务本身而提升可信等级。
- 如研究产生新的证据，先经过统一 Source/Document 入库与来源记录，再由 Analysis 引用。
- 工具调用由 Stocks 授权后执行，模型只能提出结构化请求。

## 11. 分析与研究结果

分析结果以结构化记录优先，Markdown 报告为其展现形式。单条 Finding 至少包含：claim、kind（fact/inference/opinion 等）、verification_status、支持和反面 evidence_refs、关键假设、时间边界与来源说明。

每次 Analysis 保存 Task Run、Agent/模型、实际提示词版本或哈希、输入文档引用、输出 Finding、生成时间和用量。Report 引用 Analysis，可被其他研究任务检索，而不是把报告正文当作无法拆分的唯一数据。

研究结果鼓励明确“尚需验证的问题”，不输出无证据的确定性涨跌概率或自动交易指令。

## 12. 技术与部署

| 部分 | 选型 |
| --- | --- |
| 后端 | Python 3.12、FastAPI、Pydantic 2 |
| 数据库 | PostgreSQL + pgvector；SQLAlchemy 2、psycopg 3、Alembic |
| 采集 | httpx、RSS 解析、HTML 提取；按需增加解析器 |
| S3 | 标准 S3 兼容 HTTP API |
| MCP | Python MCP SDK，远程 Streamable HTTP |
| AI | 配置驱动的 HTTP Provider/协议 Adapter |
| 调度 | 独立 Worker + PostgreSQL 任务记录/租约 |
| 前端 | React + TypeScript + Vite |
| UI | shadcn/ui + Base UI + Tailwind CSS |
| 表格/图表 | TanStack Table、Recharts |
| 部署 | Docker Compose、Nginx；API 与 Worker 共享后端代码，分进程运行 |

单台 ARM 服务器运行 API、Worker、PostgreSQL 等应用组件；原始文件由独立 S3 兼容存储提供，私有网络负责连通。静态前端由 Nginx 提供；Node 构建工具不需要常驻生产进程。数据库使用持久化卷，不挂载到 S3。服务凭据通过环境变量或密钥管理提供，不能写进 Git。

自用系统仍必须为 Web/API 入口提供认证和访问控制。部署配置不能把数据库、模型管理接口等暴露到公网。

## 13. Web 产品界面

V0 采用响应式单人研究工作台，不开发 UI 组件库，不制作图形化 Agent/Task 工作流编辑器。布局由 Stocks 自己定义，基础按钮、弹窗、输入与交互原语使用 shadcn/ui + Base UI。

主要视图：

- Overview：采集概况、重要事件、任务执行与近期研究。
- Intelligence：来源、股票、时间、信任级别与全文/语义检索。
- Tasks：任务名称、类型、可选策略、上次执行、状态和手动触发。
- Research：报告、结构化结论、证据来源与历史观点。
- Runs：任务执行步骤、模型调用、费用、错误与回退信息。

第一版的来源、模型、Agent、策略、任务和工具均以 YAML/Markdown/.env 管理；Web 只提供必要的查看与触发能力，不重复开发配置编辑系统。

## 14. 原型验证目标与边界

原型应完整覆盖一条从“真实来源 → 采集 → S3/数据库持久化 → 检索 → 可选研究 → 保存证据与报告 → Web 查看”的端到端路径，且允许纯采集任务独立运行。

验证重点：增量幂等、原始证据追溯、任务/Agent 解耦、简单主备回退、外部内容隔离、配置扩展成本、可观察模型消耗、ARM + Docker 部署可用性。

不纳入此产品基线：自动交易、行情高频撮合、复杂量化回测、多租户权限、分布式消息平台、复杂 DAG 可视化、多 Agent 自主协商和以模型为中心的自动路由优化。
