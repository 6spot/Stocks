# Stocks — 产品与系统设计

本文定义 Stocks 的产品目标、模块边界、执行契约、数据契约、可信度规则与技术架构，作为项目开发和维护的设计依据。

## 1. 产品定位

Stocks 是面向单一 Owner、以美股为主要市场的个人投资情报与研究系统。系统按关注的股票、行业和宏观政策主题持续采集市场情绪、财经资讯、政策法规、公司披露及结构化金融数据，形成可检索、可追溯的情报积累；独立的研究任务再按设定周期或手动需要综合多来源资料，生成支持人工投资决策的研究报告。**产品交付的是阶段性综合研判，不是对每一篇新闻生成解读。**

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
8. **只做最小充分的调度与回退**。顺序步骤、简单条件、明确的按证券 fan-out 和触发器满足原型，不引入动态 DAG、模型评分路由或多 Agent 自主协商。
9. **证券身份与金融数据是一等公民**。公司、证券、交易代码分开管理；结构化行情和财务数据不强行转换为 Document。
10. **自动分类不依赖人工确认**。文档即使没有直接证券关联，也能通过宏观、政策、行业、事件与主题标签进入检索和研究；主题相关性与证券事实关联必须区分。
11. **持续采集与综合研究独立运行**。采集任务持续更新情报，轻量分类与实体解析不等于研究报告；研究默认按时间窗口定期/手动执行，事件触发深度分析仅在明确配置时运行。
12. **围绕 Research Scope 采集**。优先获取与自选股、关注行业、宏观及金融政策相关的资料，不将全市场无差别归档作为目标；同时避免仅按 Ticker 过滤而漏掉间接影响研究标的的市场事件。

## 3. 概念与职责

| 概念 | 定义 | 负责 | 不负责 |
| --- | --- | --- | --- |
| Source | 信息发布主体或上游数据服务的一份配置 | 身份、采集范围、访问参数、信任级别 | 规定 AI 如何分析 |
| Connector | 通用采集实现：RSS、HTTP API、Web | 请求、增量获取、格式解析的通用流程 | 判定利好、组织研究任务 |
| Source Mapper | 特定来源的字段映射及必要解析规则 | 转换上游格式为统一信息记录 | 变成独立底层 Connector 类型 |
| Intelligence Repository | 情报、证券实体、金融事实与证据的统一数据访问边界 | 持久化、去重、索引、查询、来源关联 | 自主决定投资策略 |
| Issuer / Security | 发行主体与可交易证券 | 稳定身份、历史代码、文档和数据关联 | 以当前 Ticker 替代永久身份 |
| Market Data | 行情、财务及公司行为等结构化数据 | 带时间口径的数值存储与追溯 | 用文本切片替代数值序列 |
| Classification | 文档的自动类别、主题、行业与事件标签 | 低成本结构化判断、筛选及主题研究入口 | 把行业相关性当作直接提及的证券或事实验证 |
| Task | 一份可独立执行的工作定义 | 触发、步骤、条件、输入、输出 | 假设必定使用 Agent |
| Agent | 可配置的智能处理角色 | 提取、判断、分析、复核、工具调用请求 | 管理外部模型服务的部署 |
| Model Provider | 某种模型 HTTP API 的接入配置 | 认证、协议转换、超时、响应与用量归一化 | 决定投资判断标准 |
| Tool | Agent 或 Task 获得授权后可调用的操作 | Repository 读接口、远程 MCP 工具等 | 绕过权限或直接改变系统提示词 |
| Strategy | 可选的任务研究视角 | 目标、偏好、研究提示词与关注维度 | 固定 Agent、工作流或模型 |
| Report | 根据已保存分析结果形成的阅读产物 | 展示结论、风险、证据与待验证项 | 替代结构化分析存储 |

例如 SEC 是 Source，读取其数据可使用 HTTP API Connector 与相应字段映射；RSS 是传输形式，不代表发布者信誉；远程搜索/网页提取 MCP 属于 Tool。

## 4. 数据与执行的关系

**默认执行模式：持续采集 → 情报沉淀 → 周期性综合研究。** 采集与研究是两个独立的 Task，Repository 是共享数据基础；二者不应形成“每收一篇新闻就调用研究模型”的同步链路。

~~~text
采集任务（依 Source 更新周期独立调度）
  RSS / HTTP API / Web
    → Research Scope：上游筛选 + 本地相关性过滤
    → 原始内容快照、标准化记录、去重
    → 轻量分类、实体解析、来源/时间关联
    → Intelligence Repository（持续积累）
                    |
                    | 读取指定研究范围、时间窗口和历史结论
                    v
研究任务（按日/周定时，或手动触发）
    → 汇总资讯、市场情绪、宏观/政策、行业、行情和财务事实
    → 跨来源对照、识别变化与冲突、证据验证
    → 结构化 Findings 与综合研究报告
~~~

- 采集任务获取信息、持久化并完成必要的相关性判断、分类与实体消歧后即可结束，**不要求对每条记录进行投资研判**。分类可使用规则或低成本 Decision Model，但仅生成可复用的元数据。
- 数据在数据库和独立 S3 对象存储中持续累积；同一 Document、事件或指标可以被日报、周报、行业研究和个股研究反复引用。研究任务从 Repository 按证券、行业、宏观主题、政策类别、市场情绪和证据可靠性组合查询输入，而不是只阅读最新的一篇资料。
- **研究窗口用于识别本次新增或新变更的信息，不用于限制研究可参考的历史资料。** 增量窗口独立保存在 Task Run / 研究任务游标中；历史背景、旧财报、此前政策与历史 Findings 可按研究时点在窗口外读取并标明为背景。资料不记录“属于某次日报”“已被某 Task 消费”这类任务专属属性。
- 采集故障不阻塞研究读取现有资料；研究 Agent、模型额度或报告生成故障也不阻塞新数据继续采集。研究报告必须说明资料覆盖区间、数据更新状态和已知缺失，不能把暂时未采集到数据解释为没有市场变化。
- 报告从多个维度识别**一段时间发生的变化**，可与历史报告/结构化结论比较；既不只是新闻标题列表，也不是逐篇正文的改写。
- 一个 Task 可以按证券范围产生多个独立 Task Run；Repository 同时支持不绑定具体证券的宏观与行业研究。
- Agent 可以根据明确授权调用 Repository 只读工具或远程 MCP Tool 补证；新证据先按来源与时间边界统一登记，再允许报告引用。
- 事件触发的研究保留为**显式启用的可选模式**，用于指定重要事件或特定策略；系统默认不对每次 `on_entities_resolved` / `on_document_classified` 触发研究报告。
- 模型文本和新闻观点本身不是已经核实的投资事实。报告中的关键事实、数值和结论分别引用 Document、结构化金融数据和核查记录。

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
  items:            IngestCandidate[]
  next_cursor:      opaque | null
  fetched_at:       timestamp
  metadata:         object
~~~

IngestCandidate 是带类型标识的输入记录，可为 DocumentCandidate（非结构化资料）、MarketBarCandidate（行情）、FinancialFactCandidate（财务数值）或 CorporateActionCandidate（公司行为）。不同类型有各自的字段校验与入库映射，但复用 Source、采集状态、幂等、原始负载留存和追溯机制。

DocumentCandidate 至少提供：上游 ID 或 URL、标题、正文/摘要、发布时间、Source、原始 URL、实际发布者（已知时）、原始负载或引用。结构化候选记录至少提供关联实体标识、指标/数值/单位、数据时间口径与来源引用。未知字段允许为空，但不得伪造；尚未消歧的证券先保存原始标识而不强行匹配。

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

### 5.3 Research Scope 与有界采集

Research Scope 定义“**值得采集和后续研究的内容范围**”，与 Source（从哪里获取）、Connector（如何获取）及 Strategy（如何研究）分离。一个 Scope 可以由 Watchlist、行业、宏观主题、金融/监管政策主题构成；它不要求每篇资料都直接提到某个股票代码。

| Scope 内容 | 示例 | 采集原则 |
| --- | --- | --- |
| 关注证券 | Watchlist 内的 Security / Issuer | 有精确证券/公司 ID 的 API 优先按上游参数查询 |
| 关注行业 | 半导体、能源、银行业 | 采集产业链、竞争格局、供应链及影响该行业的政策 |
| 宏观与市场 | 利率、通胀、就业、流动性、波动率 | 采集可观察的指标、官方发布以及相关金融资讯 |
| 金融政策与法规 | 货币政策、监管、出口管制 | 不要求公告直接提及 Watchlist 证券 |

**采集两层过滤：**

1. **上游过滤**：HTTP API 能按发行主体、时间、事件类型或主题查询时直接限定范围；RSS 优先订阅对应行业/市场频道，并按发布时间或源游标增量获取。不能假设每个 RSS 提供按股票和关键词查询能力。
2. **本地相关性过滤**：对于上游不能缩小范围的条目，先看标题、摘要、元数据、已知规则；必要时再用配置的低成本分类模型判断是否属于 Scope。优先在下载全文、生成 Embedding 和深度研究之前完成过滤；无关条目仅保留采集游标、幂等和过滤统计所需信息，不必作为正式情报文档沉淀。

过滤应**适度宽松**：不能只匹配 Ticker；与关注行业、公司经营环境、宏观政策间接相关的内容也属于研究候选。判断不确定且潜在影响较大时，可保留为待分类情报，而不是悄悄丢弃。每个 Source 的抓取数量、筛除数量、失败和最后成功采集时间应可查看，以便发现筛选过严导致的数据盲区。

采集范围从 Research Scope 读取，不在每个 Connector 内重复维护一套投资偏好。Source 的访问权限、实际 API 查询能力及频率限制仍独立生效。

## 6. Intelligence Repository 与存储

### 6.1 存储分工

- PostgreSQL：Source、采集状态/游标、发行主体与证券身份、结构化行情与财务数据、标准化 Document 元数据、可搜索正文、股票与事件关联、Task/运行记录、分析结论与证据关联。
- pgvector：Document Chunk 的 Embedding 与语义相似度能力，和 PostgreSQL 全文检索、结构化过滤结合构成混合检索。
- 远程 S3 兼容存储：HTML/XML/JSON 原始快照、PDF、研报及较大的原始文件。数据库只保留 Object Key、内容哈希、尺寸、类型等元数据。
- S3 通过标准 API 访问。可利用 Tailscale 私有网络连接独立存储服务器，但应用不绑定某个私网产品或存储厂商。

写入流程为：获取原始内容 → 存储 S3 不可变快照 → 数据库事务内保存文档与幂等键、更新游标并登记后续事件。数据库失败时允许重试并清理孤立对象；绝不在原始内容未可靠保存时推进游标。

### 6.2 核心实体

| 实体 | 数据含义 |
| --- | --- |
| sources | 上游身份、Connector 类型、出版主体、信任配置 |
| source_cursors / collection_runs | 增量位置、采集执行与错误 |
| issuers | 公司或其他发行主体的稳定内部 ID、名称历史和外部标识 |
| securities | 具体证券（含不同股类、ADR 等）及其发行主体关系 |
| security_identifiers / listings | Ticker、交易所、外部标识及有效期 |
| entity_mentions / document_entities | 文档中直接提及的主体与证券、消歧证据和状态 |
| document_classifications | 文档的类别、主题/行业/地域标签、分类修订与来源依据 |
| watchlists / watchlist_securities | 研究标的列表及成员证券 ID |
| documents | 每份获取的信息、来源元数据、对象引用及实体处理状态 |
| document_relations | 文档间去重、转载和引用关系及其判定依据 |
| document_chunks | 可检索文本切片、位置与所属文档 |
| embeddings | 向量、模型、维度、生成时间及切片引用（可以与 Chunk 关联） |
| market_bars | 证券行情 OHLCV、数据时间、交易场所、调整口径及来源 |
| financial_facts | 财务指标、报告期间、单位、披露时间、修订与原始申报引用 |
| corporate_actions | 拆股、股息、合并、证券变更等事件 |
| events / event_documents | 同一事件与文档的候选/已确认关系及归并依据 |
| tasks / task_runs / step_runs | 任务定义快照、独立研究范围与增量窗口、按证券独立的运行及逐步骤执行记录 |
| research_cursors / research_inputs | 每个任务/范围的成功增量位置，以及每次研究实际选用的文档、结构化数据与分类版本引用；逻辑数据契约可由既有运行记录承载 |
| evidence_packages | 某次研究的取材查询条件、候选覆盖统计、入选证据及其版本、排除/缺口说明的持久化快照；可直接作为 Task Run 的结构化输出存储，无需独立服务或专属物理表 |
| analyses / findings | 研究过程、结构化结论、事实/推断类型、模型及提示词版本 |
| verification_runs / finding_verifications | 独立核查执行、判断、依据与核查模型信息 |
| evidence_links | 单条结论与 Document/Chunk/结构化金融数据的可追溯关系 |
| reports | 可阅读报告及所引用的 Analysis |

数据库表可根据性能合理拆分或合并，但上述信息语义及可追溯关系必须保留。Source 信任等级变更，不覆写既有研究执行时使用的快照。**资料只持有自身的发布时间、采集/入库时间、有效版本、可研究状态与关联元数据；研究进度、使用记录及时间范围均属于独立的 Task/Run 数据。**


### 6.3 发行主体、证券身份与消歧

**永久身份不能依赖股票代码。** Issuer（公司或发行主体）使用内部 `issuer_id`；Security（具体证券，如不同股类或 ADR）使用内部 `security_id`，同一 Issuer 可以关联多种 Security。Ticker、交易所代码、CIK、FIGI 等均是有来源和适用范围的外部标识，而非内部主键。Watchlist 保存 `security_id`；公司层面研究通过 `issuer_id` 聚合相关证券。

**历史身份字段与约束：**

| 记录 | 关键字段 | 约束与语义 |
| --- | --- | --- |
| `issuers` | `issuer_id`、名称、外部身份引用 | 公司改名不重建内部 ID；公司合并或实体变化保留关系历史 |
| `securities` | `security_id`、`issuer_id`、证券类别 | 股票类别、ADR 等不因为公司相同就合并 |
| `security_identifiers` | `security_id`、`type`（ticker/FIGI 等）、`value`、`exchange`、`valid_from`、`valid_to`、`source_ref` | 有效期采用 `[valid_from, valid_to)`，允许空上界；同一命名空间、交易场所和时间点出现多个候选时不得猜测 |
| `entity_mentions` | `mention_id`、`document_id`、原文词语、位置或文本引用、识别到的实体类型、`reference_time`、`time_basis`、`status` | 每处提及可独立消歧；`unresolved` 必须可查询 |
| `document_entities` | `document_id`、`issuer_id` 或 `security_id`、`resolution_status`、`resolved_as_of`、`method`、`evidence_ref`、`resolution_revision` | 支持多对多关系；只有 `confirmed` 的 `security_id` 能驱动证券级任务 |
| `documents` | `entity_resolution_status`、`entity_resolution_revision`、`entity_resolved_at`、`entity_attempts`、`entity_last_attempt_at`、`entity_last_error`；`classification_status`、`classification_revision` | 文档入库后，两类异步结果分别落库与标识状态；实体识别和自动分类均不阻塞原文持久化，复用现有 PostgreSQL Worker 任务租约机制 |

以上列出要求保留的**数据语义和字段契约**；具体索引、主键类型或部分辅助字段的物理拆分由数据库实现决定，不得丢失历史时间和确认依据。

**实体解析时点（as-of）规则：**

1. 一个提及若明确指代某个历史时间，以该提及的 `reference_time` 为准，并保存原文依据（例如“该公司在 2023 年仍使用代码 X”）；否则默认使用文档的 `published_at`。
2. 选择在解析时点有效的标识，即 `valid_from <= reference_time < valid_to`（空上界视为持续有效），结合交易所、证券类别和原文上下文进行匹配；优先采用可核对的身份标识。
3. 不能用当前 Ticker 映射替代历史匹配，也不能用采集时间 `collected_at` 充当文章的历史指代时间。若缺少可靠时点或匹配不唯一，保存 unresolved 与候选依据，不创建猜测性的已确认关系。
4. 解析结果应记录采用的 `resolved_as_of`、`time_basis`（explicit_mention / document_published）、匹配方法与来源依据。新规则重新解析只修正当前关联，不覆盖原文或抹去历史解析结果。

**异步解析与文档状态：**

- 采集先完成 S3 原文/快照与 Document 入库，记录 `pending`；不得在采集事务内等待模型或联网消歧。后台 Worker 随后进行异步后处理：识别提及、规则匹配及必要时的 Agent 消歧。
- `pending`：等待或正在解析；`resolved`：本轮处理完成且没有未解决的实体提及（允许识别结果为空）；`partial`：本轮处理完成但仍存在未解决的提及；`failed`：处理过程失败，保存错误，允许按重试策略重做。
- 每轮解析只生成一组可确认的关联及一组仍未确认的提及。**状态 `partial` 不妨碍已确认证券参与后续研究**，但未确认候选绝不触发证券任务。
- Worker 先计算本轮关联集合与当前已确认集合之间的差异。只有首次完成或发生实质变更时才增加 `entity_resolution_revision`；相同数据重复处理保持修订号不变。
- 将解析结果、文档状态和后续触发记录放在同一数据库事务提交。失败时不发布证券触发；未确认提及可由之后的规则升级或补充资料重新解析。
- 后续纠正历史身份映射时，保留旧研究的输入快照；对依赖错误映射的现有结果标记需重新评估，当前查询不再将其当作正确证券的有效研究结果，必要时按新的确认关系重新发起任务。无需引入全量依赖图。

**解析积压的发现和恢复：**

- `pending` 不意味着一定正常执行。按照文档采集/入队时间与当前时间判断等待时长，并检查 Worker 任务的租约到期情况。设置一个统一可配置的 `entity_resolution_stale_after`（默认 30 分钟）；超过阈值的文档计入“解析积压”，不需要增加新的文档状态。
- Worker 从任务表领取解析任务，记录开始时间和尝试次数。进程异常退出或租约过期后，同一任务可被重新领取；失败记录错误、按已有的有限重试策略重新入队。重复处理应使用同一文档/解析修订的幂等规则，不重复发布证券事件。
- 达到重试上限后保留 `failed` 和最后错误，等待人工处理或主动重新入队；**不能把 failed 当作已处理成功**。已达到阈值的 `pending`、全部 `failed` 都必须能按文档 ID/来源查询，供界面展示与定位。
- Overview 显示“解析超时未完成数”和“解析失败数”，并能进入 Runs 查看具体文档、入队/最后尝试时间、尝试次数、最近错误及可否重试。阈值变化只影响告警统计，不改变数据状态或跳过研究。
- 晚到的解析结果仍正常保存并发布相应的证券就绪事件；首版不增加 `max_delay`、过期触发拦截或自动丢弃历史研究。是否继续深入分析，由原有 Task 条件与 Agent 结合发布时间、研究策略决定。

### 6.4 自动分类、标签与关联边界

分类属于 Document 的独立、可复用情报属性，是采集后的轻量整理，不是要求逐篇生成分析报告；不要求文档必须识别出直接证券关联。宏观经济、行业政策、市场制度、行业动态、企业披露等资料，即使 `confirmed_security_ids=[]`，也应可以完成分类、检索，并被独立的定期研究任务读取；若明确启用了事件驱动研究任务，也可以按分类触发。

**统一分类结果由 Stocks 定义，与模型厂商无关：**

| 字段 | 语义与约束 |
| --- | --- |
| `document_id`、`classification_revision` | 文档及本次有效分类修订；重复处理无变化时不新建修订 |
| `category` | 单个主要类别，来自受控词表，例如 `macro`、`policy`、`industry`、`company`、`other` |
| `event_type` | 可选的事件类型，如 `monetary_policy`、`earnings`；不适用时为空 |
| `topics[]` | 可多选的主题标签，例如 `interest_rates`、`inflation`、`liquidity` |
| `related_industries[]` | 文章讨论或可能影响的行业分类标识，属于**主题相关性**，不表示直接提及某一证券 |
| `regions[]` | 所涉及的地域或市场；不明确时为空，不根据语言猜测 |
| `method`、`model_ref`、`taxonomy_version` | 分类方式、实际使用的模型/规则引用及词表版本 |
| `classified_at`、`reason_or_evidence_ref` | 分类发生时间及可追溯依据；不能把模型分类标记成已经核实的事实 |

标签及类别使用受控词表（配置文件可维护），前端与 Task 使用**稳定标签 ID** 查询而非依赖自由文本字符串。词表允许自然扩展与修订，但保留分类时的词表版本；初版可以将分类作为一条结构化 JSON/JSONB 记录存储，不强制建立独立的标签服务或多张关联表。

**自动分类流程与失败边界：**

1. Document 入库后 Worker 执行异步分类。可先用已知元数据/明确规则，再调用配置的低成本 Decision Model，完成多选标签的逐项 yes/no、单选类别的 choice 等结构化判断；复杂文本再按需使用支持生成的 LLM。模型接口通过第 9 节的 Provider/Adapter 调用，不绑定任何特定厂商。
2. 分类必须符合固定输出 Schema 与受控词表，超出词表、模型故障或无效结构时按原有有限重试处理，绝不把伪造标签写入正式分类。完成一次有效分类即为 `completed`；分类为空标签或类别 `other` 也可以是有效结果。
3. 分类状态与实体解析状态**独立**：`classification_status = pending / completed / failed`；一方完成无需等待另一方。已完成的结果与其修订记录持久化，重新分类保留历史，更新当前结果并依据实质变化发布新事件。
4. 分类结果、状态/修订号及 `on_document_classified` 持久化事件在同一数据库事务写入。Worker 仅消费已提交事件；失败保留错误、重试与 Runs 可查询记录，防止分类失败造成无声遗漏。
5. 主题或行业相关性不自动扩展成 `document_entities` 的 `confirmed` 证券。只有原文真实提及且能够消歧的证券才产生**直接实体关联**；由政策或宏观事件推测可能受影响的公司，应标记为研究候选，并由研究 Task 使用其他证据独立验证。

**示例：**

~~~yaml
document_classification:
  category: macro
  event_type: monetary_policy
  topics: [interest_rates, liquidity]
  related_industries: [banking, real_estate]
  regions: [US]
  direct_security_ids: []
~~~

此处 `direct_security_ids` 是从独立的已确认 `document_entities` 查询得到的阅读视图字段，**不由分类模型直接决定**；真实持久化时由分类记录与实体关联组合展示。该文档没有直接提及的证券，仍然可以被宏观、行业研究 Task 使用。模型所输出的“可能影响”标签只代表待研究的主题关联，绝非确定的股票涨跌判断或投资建议。

#### 市场情绪的数据含义

市场情绪是综合研究的一个输入维度，而不是一条新闻的“乐观/悲观”标签或模型自报的全市场分数。

- **可观察指标**：波动率、成交量、资金流、期权/调查等可获得的情绪相关数据，以结构化金融指标形式记录数值、单位、市场/证券范围、时间与来源。指标的解释依赖研究策略，不将任何单个指标直接定义为“市场情绪真值”。
- **信息叙事/观点**：媒体、研究机构、企业及政策发布的信息可按主题和可选的情绪倾向归类，保留原始出处与发布时间。模型给出的倾向属于来源文本的分类结果，不能自动代表整体投资者观点。
- **阶段性研判**：报告可以综合指标变化和多来源信息，得出带有证据、时间范围及不确定性的市场氛围判断；数据样本偏差、转载和观点分歧需要明确说明，不将新闻条数简单当作市场情绪统计。

可配置的分类模型只负责必要标签及相关性判断；“市场情绪如何变化、对哪些研究范围有意义”由定期研究任务在多源上下文中形成，而非每篇文档单独生成研究结论。

### 6.5 结构化金融数据与时间语义

Market Data 通过同一 Source / Connector / Source Mapper 接入，但按数值事实存储，不强行写入 Document Chunk。Agent 通过 Repository 的只读 Tool 获取指定证券、发行主体、时间范围和指标的数据。能够定期获取的数据应沉淀并复用，不能仅依赖 Agent 每次临时查询外部服务。

- market_bars：security_id、时间粒度/时间戳、交易场所、OHLCV、币种、调整口径、Source 与数据版本。
- financial_facts：issuer_id、指标及报表口径、数值、单位/币种、财务期间、公开披露时间、采集时间、修订记录和原始申报引用。
- corporate_actions：拆股、分红、并购或证券标识变动等，保存生效日期、可获知时间和原始来源。
- 研究时使用 as-of 语义：按研究时点只读取当时已公开的数据版本，不因后续财报修订或行情调整造成前视偏差；研究记录固定具体数据版本或引用。
- S3 保留结构化数据原始响应/文件快照，PostgreSQL 保存规范化数值、来源和校验标识。


### 6.6 事件归并与证据独立性

**Event 表示现实中的一件事，不等于文章来源，也不等于向量相似集合。** 同一家公司同一天发布不同公告可对应不同 Event；多家媒体报道同一公告可以对应一个 Event，但其证据可能仅来自一份原始披露。

事件归并流程：

1. 用公告/上游唯一 ID、Canonical URL、原文哈希做确定性去重；严格区分“内容相同”与“只是报道同一事件”。
2. 利用已确认的证券或发行主体、事件类型、发生与披露时间、全文及向量检索寻找候选 Event。
3. 只有满足可解释的规则或经过明确语义判断，才在 `event_documents` 中记录 `confirmed` 归属；证据不足时保留 `candidate`，不能为提高归并率而强制合并。
4. 保存关联来源、判断方法和依据；允许纠正归并但不删除 Document 和来源元数据。

**Document 来源链使用 `document_relations`，与 `event_documents` 独立。**

| 字段 | 契约 |
| --- | --- |
| `id`、`from_document_id` | 一条有方向的来源关系及当前文档 |
| `to_document_id` / `target_original_url` | 已入库的目标文档 ID，或尚未入库的原始出处 URL；至少具备一种可追溯目标 |
| `relation_type` | `duplicate_of`（同一原始内容）、`republished_from`（转载/再发布）、`cites`（引用其他报道/披露） |
| `status` | `candidate`、`confirmed`、`rejected` |
| `method` | `hash`、`canonical_url`、`link`、`rule`、`agent`、`manual` 等 |
| `evidence_ref`、`reason`、`updated_at` | 原始依据与判定说明，支持后续复核 |

约束与处理规则：

- 同一确定的“起点 + 类型 + 目标”关系应幂等；自引用无意义，系统拒绝。转载与重复关系应避免形成闭环；遍历来源链时进行去环和深度限制。
- `duplicate_of` 与 `republished_from` 用于寻找共享原始出处；`cites` **不代表失去独立性**，引用不等于全文转载。候选关系不参与自动判定。
- 不能找到原始文档时，保留其原始 URL、可识别的发布者和发现链路，不能凭空创建一个“已验证的原始来源”。
- Verifier 沿**已确认**的重复/转载来源链判断是否共享源头：同源视为一组证据；可证明各自有独立原始调查时才视为独立；无法判断时标记 `unknown`，不把“没有已知关系”当成“独立”。
- 来源是否独立、来源是否可信、结论是否得到支持是不同判断。相关来源的数量不得直接作为真实性票数；核查结果引用参与判断的具体来源关系和证据。

### 6.7 检索

Repository 对调用者提供：按 Issuer/Security、行业、主题、类别、事件类型、地域、来源、事件、时间和数据可获知时点的查询；文档全文/语义/混合检索；结构化行情财务查询；相似事件和证据读取。

分类结果与实体解析结果支持独立过滤：`confirmed_security_ids` 为空而分类完整的宏观/行业资料是**正常数据**；存在 `unresolved` 提及的文档可按需筛选，但不作为默认的人工审批队列。

向量必须记录 Embedding 模型和维度；不同模型的向量不直接混合排序。pgvector 从初版引入，具体向量索引按已配置的 Embedding 模型建立。向量相似度只表示语义相关，不表示事实真实性或证据独立性。

**取材查询接口：** `repository.search_changes` 以 Task Run 的增量窗口与 `as_of` 找到新增入库、完成分类、重要修订等可研究变化；`repository.search` 可不受增量窗口限制，按同一 `as_of`、Scope 和类型检索历史证据。两者都返回稳定引用、版本、时间、来源、关联依据、查询批次和可复查的排序/过滤信息；查询不能修改 Document 的全局状态。Evidence Selection 按第 8.3 节在这两个 Repository 查询之上执行，不另建一套取材数据库。

## 7. 来源可信度、证据与安全

可信信息分为三个独立维度：

1. **来源身份与默认可信度**：T1 官方原始记录、T2 可识别原始发布者、T3 二手媒体/机构、T4 未核实来源。这些是可配置初始类别，不是客观真伪概率。
2. **具体论断属性**：披露事实、已知数值、个人观点、预测、未经证实的传闻，分别标记。
3. **核实状态**：unverified、supported、contradicted、contested、insufficient_evidence，保留支持与反面证据；这些状态由独立核查记录按第 11.1 节的规则派生，不由生成该 Finding 的 Agent 自行宣布。

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
| trigger | manual、schedule（明确时区）、on_new_documents（文档级）、on_entities_resolved（证券级）、on_document_classified（主题/行业级）等 |
| scope | 可选的证券范围；可按 security_id 展开独立 Run |
| steps | 有序步骤及参数/上游结果引用 |
| enabled | 是否参与触发 |

最小步骤执行种类：Connector 获取、Repository 持久化/查询、Agent 调用、授权 Tool 调用、结构化分析保存及报告渲染。**默认 Task 定时执行采集、独立执行轻量分类/实体解析，并按日或周执行综合研究；单篇触发研究的 Task 必须单独启用。**

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


### 8.1 数据生命周期、可靠触发与证券 fan-out

**文档级、证券级和主题/行业级任务使用各自所需的就绪条件。** Document 入库不表示证券身份已确认，也不表示主题分类已完成。两条后处理路径互不等待。

#### 数据生命周期与触发语义

| 阶段 | 提交内容 | 可供哪些 Task 消费 |
| --- | --- | --- |
| 1. 采集入库 | S3 原文与 PostgreSQL Document、采集幂等键、采集游标；实体状态为 `pending` | `on_new_documents`：不依赖证券身份的文档处理任务 |
| 2A. 后台实体解析 | `entity_mentions`、`document_entities`、文档解析状态/修订号 | 解析结果提交后才能产生证券级触发 |
| 2B. 后台自动分类 | `document_classifications`、分类状态/修订号 | 分类结果提交后才能产生主题/行业级触发；无需等待 2A |
| 3. 就绪事件持久化 | 与对应的 2A/2B **同一 PostgreSQL 事务**分别写入就绪事件及修订 | `on_entities_resolved`：证券任务；`on_document_classified`：主题/行业任务 |
| 4. 创建 Run | Worker 根据已启用的 Task 消费事件；证券级任务按确认证券展开，主题级任务按分类条件创建 Run | 常规执行与结果入库；未启用事件研究则不产生研究 Run |

**最小持久化触发契约：**

~~~text
TaskEvent:
  event_id:              stable unique id
  kind:                  on_new_documents | on_entities_resolved | on_document_classified
  document_ids:          list[document_id]
  resolution_revision:   int | null
  confirmed_security_ids: list[security_id]  # entities_resolved 时
  classification_ref:    document_id + classification_revision | null  # classified 时
  created_at:            timestamp
  delivery_state:        pending | processing | completed
  retry_metadata:        attempts, lease_expires_at, last_error
~~~

TaskEvent 可以是 PostgreSQL 任务/事件表的一个类型，不需要部署消息中间件。事件与相应业务数据在同一事务中写入；Worker 领取并创建 Task Run 时采用唯一键和事务保护。Worker 崩溃或重启后，可以安全重新领取未完成事件，重复投递不能重复创建相同 Run。

- 第 1 步事务失败不推进游标、不创建任何新数据触发；重试先检查内容哈希及幂等键。
- 第 2 步 `failed` 时记录错误并按已有 Worker 的有限重试策略重做，不发布 `on_entities_resolved`；Worker 中断或解析任务租约过期时允许重新领取。`partial` 可以发布已确认证券集合，并保留待消歧部分。超过积压时长的 `pending` 与重试耗尽的 `failed` 进入 Overview/Runs 可见列表，绝不视为研究已经完成。
- 即使本轮没有确认任何 Security，也保存解析完成/部分完成的事实；`confirmed_security_ids` 为空时不创建证券 Run，**但绝不阻止已完成分类的主题/行业研究**。
- `on_document_classified` 只在分类完成、结果已提交时发布，携带可查询的分类引用；**仅已启用的事件型 Task** 才可按标签、行业、类别、地域过滤后建立 Run，空标签不强制触发。重新分类按分类修订与任务研究窗口幂等，不会重复创建相同 Run。
- 分类触发器的可选过滤字段使用 `category_in[]`、`topic_any[]`、`industry_any[]`、`region_any[]`：同一字段多值为“任一匹配”，不同字段同时配置时必须全部满足；不配置过滤字段表示对所有完成分类的文档生效。过滤只基于已保存的分类结果，不能以模型预测代替明确触发条件。
- 重新解析只在已确认关联**实质改变**时发布新修订事件。新增证券产生新的研究范围；撤销或改正旧关联须在 Repository 中保留更正信息，并标记依赖旧关联的研究需要重估。与旧修订输入相同的重试不得产生重复触发。

#### 触发事件不等于启动深度研究

`on_new_documents`、`on_entities_resolved`、`on_document_classified` 首先表示有一批可被查询的新情报或元数据。它们支持安排必要的后处理或显式启用的特定任务，但**不会自动要求生成单篇分析或报告**。

- 默认研究周期由独立 Task 的 `schedule` 或 `manual` 决定，研究任务查询已存储的多篇文档和多类金融数据。
- 如果启用某个事件驱动研究 Task，其过滤条件、研究目标和输出仍需明确配置；没有配置不产生研究 Run。
- 研究任务失败不回滚已持久化情报，也不阻止后续采集。报告生成应能使用现有有效数据，同时明确数据覆盖不足和尚未完成的分类/采集状态。

#### 主题/行业任务与证券任务的边界

- `on_entities_resolved` 适合分析被资料**直接确认涉及**的证券，可按 `security_id` 扇出。
- `on_document_classified` 适合宏观、政策与行业 Task。其输入是 Document ID、分类引用与标签，不要求文档有证券关联，也不默认推导全行业股票并批量 fan-out；若策略后来需要研究具体标的，由 Task 根据 Watchlist/Repository 数据明确决定证券范围。
- 两种就绪事件均来自同一 PostgreSQL 持久化机制，分别按文档+修订+任务/研究窗口幂等。分类不等于核实，不得把预测影响关系写进事实关联。

#### Fan-out 是创建 Run，不是 Task 内循环

- Task 的 `scope` 可以是单证券、Watchlist 或事件携带的已确认 `security_id` 集合；显式 `for_each: security` 才按证券展开，其余 Task 可以对整批文档只运行一次。
- 将 scope 展开为去重、稳定顺序的内部 `security_id`，校验 `max_items` 上限；每个证券产生一个 Run，携带 `security_id`、`task_batch_id`、时间窗口和输入证据引用。
- Run 的幂等身份由 `task_id`、Task 定义版本、触发事件 ID（或调度窗口）、`security_id` 与研究窗口组成；数据库层建立唯一约束。**同一个事件重投不产生新 Run，确有实体关联修订的事件则允许新 Run。**
- 每个 Run 有独立状态、步骤日志与重试；Worker 使用 Task 的 `max_concurrency` 限制并发；某证券失败不撤销其他证券的成功结果。
- 需要汇总时，汇总 Task 读取指定批次/窗口的研究结果及缺失状态，不自动创建隐式依赖屏障。
- 系统只支持上述顺序步骤、结构化 `when` 条件、触发、fan-out 和有限重试；不引入循环 DSL、通用 DAG 引擎或分布式事件总线。

**示例：** 一份公告入库后先得到 `pending`，此时不会触发“按 NVDA 研究”；解析完成并确认 NVDA 才产生证券级 Run。若 Worker 恰好在事件提交后宕机，恢复时仍能领取同一事件，但唯一约束保证不会再建第二个 NVDA Run。若后来更正该公告实际关联另一只股票，新的解析修订保留纠正记录，并据此创建新证券的研究 Run。

### 8.2 默认采集、综合研究与可选即时研究示例

#### 研究增量窗口与共享资料引用

**研究窗口是某个 Task Run 查找新增变化的范围，不是 Document 的属性或整次研究的资料访问边界。** Document 保留 `published_at`、`collected_at`、原始出处和处理版本；研究专属的窗口、游标、取材和运行状态不能写入 Document。

| 时间或引用 | 所属记录 | 作用 |
| --- | --- | --- |
| `published_at` / `collected_at` | Document | 原始发布与系统采集事实；不同研究复用 |
| `classified_at` / 分类修订、实体修订 | 文档后处理记录 | 标明新分类或新关联何时对研究可见 |
| `window_start` / `window_end` | Task Run | 本次新增/变更情报的选择范围，采用 `[start,end)` |
| `as_of` | Task Run | 研究取材的知识截止时点；所有输入在该时点必须已经可获得 |
| 成功增量游标 | Task + Research Scope | 为本任务的下一次运行确定增量起点；不同研究任务/范围互不影响 |
| `research_inputs` | Task Run / Analysis | 记录实际使用的 Document ID、分类/实体版本、数据点版本和历史 Analysis 引用 |

**窗口生成与推进规则：**

1. 周期性研究 Run 创建时，将 `window_start` 设为**同一 Task + 研究范围的上一次成功完成并提交结果的 `window_end`**；本次 `window_end` 取启动时固定的截止时点。第一次运行使用明确配置的初始起点或初始回看范围。
2. 窗口只用于发现已进入 Repository、并在本次可研究增量中首次出现或发生重要修订的资料。文章的 `published_at` 不用于判定是否“已处理”：周二才入库或完成分类的上周五公告，也会作为周二新增可研究信息进入后续 Run。保留资料自身的真实发布时间供报告解释。
3. 对采集入库、分类完成或影响研究的关联修订分别保留可追溯的新增/变更记录及其入库时点或版本，增量查询以**系统可研究的变更记录**为准；多次变化只需按引用和版本幂等去重，不重复复制 Document。研究截止时点固定后，晚于该时点提交的变化留给下一个 Run。
4. 只有要求的分析与报告结果**成功持久化**后，才原子地推进该 Task + Scope 的研究游标。失败、取消或中断不推进；下次运行重试或覆盖未成功区间。即使周末无定时运行，周一仍从周五上次成功终点开始，包含周末新增信息。
5. Agent 可以根据明确的 Research Scope、`as_of` 与检索条件访问**增量窗口之外的历史背景资料和以前的 Analysis**，并对比既有结论；这些背景不应错误计作本次新增情报。每个研究 Run 将最终采用的输入 ID、版本、选择原因或查询条件留在独立 `research_inputs` 中，以便复现。
6. 同一份资料可参与多个不同的 Task、时间窗口与策略研究，不在 Document 上设置全局 `analyzed`、`last_used_by_task`、`research_window` 或 `consumed_at`。重复性由各自 Run 和输入引用的幂等约束处理。

~~~text
Run A（日报）：增量变化窗口 [上次成功日报截止, 本次截止)
                 + 查询此前财报、历史政策及过往观点作为背景
                 → 保存本次 research_inputs → 综合报告

Run B（行业周报）：自身独立的增量变化窗口
                 + 可以复用日报引用过的同一 Document
                 → 保存自身 research_inputs → 行业报告
~~~

**两类检索必须区分**：`repository.search_changes` 选择本次新增/修订的候选证据；`repository.search` 按研究时点、Scope 与条件选择所有可用的背景/验证资料。二者均读取同一 Repository，前者不修改资料，也不把研究消费进度写回 Document。研究任务通常通过第 8.3 节的 `repository.select_evidence` 统一执行这两类查询及取材，不需要每种研究自己重写步骤。

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

  daily_market_report:
    type: report
    trigger: {kind: schedule, cron: "0 9 * * 1-5", timezone: America/New_York}
    window: {mode: since_last_success, initial_lookback: 7d}
    steps:
      - id: evidence
        use: repository.select_evidence
        with:
          scope: research_scope
          objective: "综合市场情绪、宏观与政策的近期变化"
          window: run.window
          as_of: run.as_of
          budget: {max_tokens: 30000}
      - id: research
        use: agent.researcher
        input: evidence.output
      - id: save
        use: repository.save_analysis
        input: research.output
      - id: report
        use: repository.render_report
        input: save.output

  weekly_watchlist_research:
    type: analysis
    strategy: long_term
    trigger: {kind: schedule, cron: "0 9 * * 6", timezone: America/New_York}
    window: {mode: since_last_success, initial_lookback: 30d}
    scope: {from: watchlist, for_each: security, max_items: 50}
    execution: {max_concurrency: 3}
    steps:
      - id: evidence
        use: repository.select_evidence
        with:
          scope: {security_id: run.security_id}
          objective: "研究关注证券在本周期的基本面、行业环境与风险变化"
          window: run.window
          as_of: run.as_of
          budget: {max_tokens: 30000}
      - id: research
        use: agent.researcher
        input: evidence.output
      - id: save
        use: repository.save_analysis
        input: research.output

  important_event_research:
    enabled: false
    type: analysis
    trigger: {kind: on_document_classified, category_in: [policy]}
    steps:
      - id: judge
        use: agent.event_judge
        input: trigger.classification_ref
      - id: research
        use: agent.researcher
        input: trigger.document_ids
        when: {step: judge, field: should_analyze, equals: true}
~~~

这里的 `financial_news`、Agent ID 和周期是**配置示例**，并非预设的真实数据源或固定业务策略。采集数据与研究报告都通过独立 Task 执行；`important_event_research` 默认关闭，说明事件驱动深度研判不是每篇资料的必经步骤。配置文件提供人可读定义，数据库记录执行时的配置、时间范围、实际输入与结果快照。

### 8.3 统一取材：Evidence Selection 与 Evidence Package

**一套取材逻辑服务所有研究范围，不按市场/行业/个股分别建设取材引擎。** `repository.select_evidence` 是现有 Task 中可配置的 Repository 步骤：先寻找本次可研究的新增变化，再查询必要历史背景，整理可复查的 Evidence Package 交给 Research Agent。这里的 “Evidence Selection” 表示执行能力，“Evidence Package” 表示本次结果快照；**都不代表新微服务、全局 Agent 或持久化材料副本**。

#### 取材请求与研究维度

| 输入 | 语义 |
| --- | --- |
| `run_id`、`task_id`、`task_version` | 研究所属 Task Run 与配置快照 |
| `objective` | 本次需要回答的研究问题，不限于预先固定的日报/行业/股票类型 |
| `scope` | Securities、Issuer、行业、区域、宏观主题和政策主题的任意适用组合 |
| `window_start` / `window_end`、`as_of` | 仅前者界定新增变化；`as_of` 约束所有事实和版本可获知性，沿用第 8.2 节 |
| `data_requirements[]`（可选） | 研究关注的证据类型或主题维度，例如政策、宏观指标、企业披露、行业信息、历史观点 |
| `budget`（可选） | 证据 Token 总预算、候选扫描上限、每类内容/维度的取材上限等有界限制；未配置使用可调整的默认值 |
| `selection_policy_version` | 本次取材规则或配置版本，便于解释不同运行的差异 |

- 已显式配置的 `data_requirements` 作为研究偏好；缺省时，使用 Scope 的结构化标签与 Objective 相关的关键词/主题词表生成默认维度和检索条件。必要时允许由已配置的低成本 Decision Agent 提出补充维度，但其结果须经受控词表/查询参数校验，不能修改 Task 权限、时间截止或预算。**不为市场日报、行业报告和个股报告分别硬编码固定取材数量或查询流程。**
- **精确关联和背景关联分开**：研究某 Security 时，直接关联的公告/财务等是核心候选；行业政策、供应链或宏观信息是间接背景候选。后者不能因被纳入 Evidence Package 就被写成 `document_entities.confirmed`，不能冒充公司直接证据。
- 一个维度可以在本次无适用资料；必须表示“没有找到/未能获取/尚未扫描完整”中的实际情况，不能为满足取材额度制造内容或将其他维度的资料强行改类。

#### 候选获取、去重、排序与预算

按以下顺序执行，全部为现有 Repository、Worker 和 Task 能实现的操作：

1. **新增集合**：按 `window` 与 `as_of` 调用 `search_changes`，寻找本次新入库、完成分类、实体/政策/数值等重要修订的可研究数据；按稳定数据 ID、修订和变化类型去重。旧资料晚到归入本次增量，但仍保留其真实 `published_at`。
2. **背景集合**：按 Scope + Objective 的需求调用 `search`，查询研究时点可获知的历史文档、政策、财务/宏观指标、已有 Analysis；不得被增量窗口限制。区分候选的 `role = new_information / historical_context`，同一资料可以在不同 Run 复用。
3. **来源与事件去重**：已确认的完全重复、转载记录只占一份主要证据席位，并保留所有关联文档 ID 与原始来源链。只有已确认事件关系才按 Event 聚合；没有可靠 Event 时以文档或确定性原始来源组为候选单位。**向量相似、同一事件关键词或未知独立性不足以自动判定为相同原始证据**；不同出处的独立观点、反面证据和相互冲突的数值不能因为相似而被吞掉。
4. **候选优先级**：按与研究目标/Scope 的相关性、增量信息价值、事件/数据修订的重要性、可追溯来源、观点差异、所研究时间的适用性组织稳定排序；使用可解释的规则、已保存分类、全文和必要的语义检索即可。来源信誉只是独立维度，不等于真实性或投资重要性；**不引入必须依赖模型的复杂综合打分系统**。
5. **维度覆盖与裁剪**：从本次需要的研究维度中分配软预算，优先覆盖核心问题、重大新变化、关键事实及重要反面证据，避免某个高频新闻主题或同一转载来源耗尽全部输入；在严格的总 Token/候选扫描预算内选择可追溯的代表性文档片段、完整数值点/序列引用及历史结论。维度预算按 Task 可调，空维度不强制填充。裁剪的是**本次模型输入**，不删除 Repository 数据。
6. **引用与快照**：取材结果保存唯一 ID、来源、版本、选入原因、原始证据链、确定性排序/规则版本、实际采用的正文片段/数值引用、各维度计数和截断原因。研究 Agent 收到这个快照，而不是任意数量的全文。

**预算与覆盖统计必须区分三个数量：** `candidates_discovered`（查询实际发现的候选）、`candidates_screened`（在本次规则下已完成筛选）、`selected_count`（入选 Evidence Package）。还要记录 `unexamined_count`、查询是否分页/触顶 `retrieval_truncated`，以及各来源的可用性；**扫描上限达到时不能把“只扫描了部分候选”标为全部已覆盖**。候选多但完成筛选后未入选，属于有意取舍；根本没有扫描/来源不可用属于覆盖缺口，必须分别说明。包内的 `selected_count` 不应等同于独立证据组数量，转载组合并需单独统计。

#### Evidence Package 数据契约

~~~yaml
evidence_package:
  run_id: run_001
  selection_policy_version: v1
  objective_ref: task_snapshot
  as_of: "2026-10-19T09:00:00Z"
  scope: {industries: [semiconductors], regions: [US]}
  changes_window:
    start: "2026-10-16T09:00:00Z"
    end: "2026-10-19T09:00:00Z"
  selection_budget:
    max_tokens: 30000
  selected:
    - ref: doc_101
      revision: 2
      role: new_information
      dimension: policy
      reason: policy_update
      evidence_ref: chunk_101_2
      source_group: origin_101
    - ref: observation_205
      revision: 1
      role: new_information
      dimension: macro_indicators
      reason: new_data_release
    - ref: analysis_018
      role: historical_context
      dimension: prior_research
      reason: previous_research
  coverage:
    candidates_discovered: 320
    candidates_screened: 320
    selected_count: 3
    unexamined_count: 0
    retrieval_truncated: false
    per_dimension: {policy: 1, macro_indicators: 1, prior_research: 1}
    sources_unavailable: []
  selection_notes:
    - "Example only: 3 selected out of 320 screened"
~~~

以上为**格式示例，非实际采集与运行结果**。实际包还应保存研究输入引用、数据版本及运行配置的稳定快照；正文片段可通过持久 Chunk 引用获得，避免重复存储全文。表与 JSONB 如何组织属于实现选择，不要求为 Evidence Package 新建独立物理表。包内的筛选统计来自实际查询过程，不允许 Agent 虚构补齐。

#### Agent 补充检索与故障边界

- 研究 Agent 可以通过现有已授权的 Repository 只读 Tool 提出补充查询，用来核对关键事实、查找反例、补齐缺少的历史背景。新查到的资料按相同 `as_of`、权限、引用和 Token/调用预算约束加入本次 `research_inputs`；不回写为 Document 的“已研究”状态，也不覆盖原始证据包快照。
- 模型不应通过重新搜索任意外部网站绕过研究截止时点。必要的远程 Tool 补证仍走可信来源登记；若该资料在 `as_of` 后才公开，不能作为该历史研究时点的已知事实。
- 取材失败与无候选不同：检索超时、分类积压、数据源断更、候选扫描不完或版本无法确定时，分别记录原因和受影响维度。可配置 Task 是否允许“带明确覆盖不足的报告”；不满足 Task 成功条件就不能推进第 8.2 节的研究游标。
- Evidence Package 在下游 Agent 失败时保持可复查，重试默认沿用同一 Run 已保存的输入、版本和选择规则；明确发起的新研究 Run 才按新的 `as_of` 检索更新的资料。
- 同一 Task Run 无论研究市场、行业、股票或混合主题，都调用同一个 `repository.select_evidence`；不新增研究专属的采集器、独立 Evidence 服务、DAG 或复杂打分调度器。

## 9. Agent、HTTP 模型与回退

Agent 配置包括：id、role、prompt、model_ref、tools[]，以及按职责需要的结构化输出契约。Decision Agent 可以返回布尔选择、类别或评分，适合受控标签分类与便宜的相关性判断；Generation Agent 可以输出研究文本或提出 Tool 调用请求。不同模型类型通过协议 Adapter 接入，不强行假设所有模型都能输出长文、原生工具调用或 JSON Schema。

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

## 11. 分析、独立验证与研究结果

分析结果以结构化记录优先，Markdown 报告为其展现形式。单条 Finding 至少包含：claim、kind（fact/inference/opinion 等）、verification_status、支持和反面 evidence_refs、关键假设、时间边界与来源说明。

每次 Analysis 保存 Task Run、Agent/模型、实际提示词版本或哈希、Evidence Package/最终 `research_inputs` 引用、输入文档与金融数值版本引用、输出 Finding、生成时间和用量。Report 引用 Analysis，可被其他研究任务检索，而不是把报告正文当作无法拆分的唯一数据。

**综合报告契约：** 报告应明确本次**新增情报的增量窗口**、研究 Scope 与知识截止时点，同时允许引用该窗口之外的历史财报、政策、既有研究作为背景。综合呈现市场情绪及其指标、重大财经资讯、宏观/行业发展、政策法规变化、重点公司与金融数据、与上次研究相比的新变化、存在分歧的证据以及尚未证实的风险。重点是跨来源综合判断，而不是按每篇新闻逐条复述。报告可根据策略只选择其中适用的维度，但必须保存输入数据/资料的范围、采集新鲜度、关键来源引用和明确的不确定性。如果某维度数据缺失，应标为未覆盖或资料不足，不能默认为没有变化。

市场情绪相关结论必须区分可观察指标、媒体/机构观点和模型的综合推断；样本不足或同源转载较多时不能宣称市场整体共识。研究任务可引用历史报告对比判断变化，不影响持续采集任务。


### 11.1 独立核查、记录契约与状态聚合

**Finding 是不可被核查步骤静默改写的研究主张。** Researcher 保存主张及支持引用后，初始 `verification_status = unverified`。Researcher 自评、模型置信分数、同一文本的重复生成都不能直接升为 `supported`。

Verifier 与 Researcher 为不同执行步骤，拥有独立的任务输入和结果记录。Verifier 检查的不是“另一个模型是否赞同”，而是：主张是否与原始资料一致、数值/单位/期间是否正确、证券身份是否匹配、引用是否被断章取义、引用来源是否真正独立、是否存在反面证据或前视偏差。可确定的数学与财务核对优先使用代码计算；可按任务选择相同或不同模型，**不强制每项研究调用两种模型**。

#### 核查数据契约

| 记录 | 必须保留的字段及含义 |
| --- | --- |
| `findings` | `finding_id`、`analysis_id`、确定的 `claim` 内容、`kind`、研究时点、原始 `evidence_refs` |
| `verification_runs` | `run_id`、任务/Verifier 标识、执行时间、输入 Finding 列表、核查规则/模型/提示词版本、数据时间边界 |
| `finding_verifications` | `verification_id`、`finding_id`、`run_id`、`verdict`、支持与反面证据引用、判定依据、`checked_at`、`is_active`、`supersedes_id`（可空）、`inactive_reason`（可空） |
| `reports` | 生成时的 Finding 引用、当时的聚合状态快照、研究报告内容与生成时间 |

`verdict` 仅表示一次核查的结果：`supported`、`contradicted` 或 `insufficient_evidence`；`unverified` 和 `contested` 是**聚合状态**，不作为单次核查结论。每条核查都保存输入证据的版本和理由，核查未完成/执行失败不能伪造一个成功的 verdict。

#### 当前状态的确定性聚合规则

只读取同一个固定 `finding_id` 下 `is_active=true` 的有效核查记录。对于已被正式修正、数据引用被证明错误或新版本明确取代的核查，保留旧记录，但设置失效标记与 `inactive_reason`；失效有依据，不能因为与另一条核查不一致就自动移除。

| 有效记录中的结论组合 | 当前 `verification_status` |
| --- | --- |
| 无有效记录 | `unverified` |
| 只有 `insufficient_evidence` | `insufficient_evidence` |
| 有 `supported`，无 `contradicted` | `supported` |
| 有 `contradicted`，无 `supported` | `contradicted` |
| 同时有 `supported` 与 `contradicted` | `contested` |

`insufficient_evidence` 不会覆盖有效的支持或反驳。追加一条反面核查，若没有使之前的支持记录正式失效，状态从 `supported` 变为 `contested`；若旧核查因为引错财报被有依据地取代，它不再参与计算，但仍可以在历史中查看。

任何状态变动都不会修改原始 Finding 或历史 Report 的生成时快照。当前页面读取最新聚合状态，报告查看其生成时快照与可追溯核查历史；报告如需更新，另生成新版本，不隐式改写原有报告。

`supported` 只表示**现有证据能够支持主张**，不是市场预测一定实现，也不是数学意义上的真实性保证。缺少可复查依据时保留不确定性，不用多模型投票或复杂评分制造虚假确定性。

#### 例子与验收边界

1. Researcher 生成“某公司收入同比增长 30%”，系统记录 `unverified`；确定性核查读取两期同口径财务数值并计算后，才能写入证据支持或反驳的验证记录。
2. 某 Finding 先有一个有效 `supported`，后续检索到独立反面材料，新增有效 `contradicted`，页面必须显示 `contested`，且可分别打开两次验证及对应出处。
3. 当旧核查引用了错误证券或错误财务期间，修正时保留记录和失效理由；重新聚合只读取有效记录，历史报告仍保持原有状态与时间说明。

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

- Overview：持续采集覆盖情况、近期重要事件、市场情绪与综合研究报告、任务执行情况；展示超时未解析的 pending 数量和 failed 数量，并提供定位入口。
- Intelligence：来源、股票、**类别、主题标签、行业、地域**、时间、信任级别与全文/语义检索；支持筛选无已确认证券及未解决实体提及的文档，但不要求人工逐篇确认。
- Tasks：任务名称、类型、可选策略、上次执行、状态和手动触发。
- Research：按日/周或自定义窗口查看综合研究报告、市场情绪变化、结构化结论、证据来源与历史观点；支持查看本次 Evidence Package 的来源/版本、取材覆盖情况和缺失维度，不是逐篇新闻解读列表。
- Runs：任务执行步骤、模型调用、费用、错误与回退信息；可查看积压/失败文档的来源、采集时间、最近尝试、错误和重试状态。

第一版的来源、模型、Agent、策略、任务和工具均以 YAML/Markdown/.env 管理；Web 只提供必要的查看与触发能力，不重复开发配置编辑系统。

## 14. 原型验证目标与边界

原型应完整覆盖“关注范围内的真实来源 → 持续采集和轻量分类 → S3/数据库情报沉淀 → 独立定时/手动综合研究 → 保存证据和报告 → Web 查看”的端到端路径。纯采集任务可以长期独立运行，无需每篇资料生成分析；定时研究也可以在采集源暂时不可用时使用已有数据并说明覆盖不足。

验证重点：增量幂等、原始证据及转载关系追溯、异步实体解析后的可靠证券级就绪事件、独立自动分类后的主题/行业就绪事件与可选研究触发、无证券文档依然可研究、直接事实关联与主题相关性分离、证券身份与历史代码 as-of 消歧、结构化金融数据的时点正确性、按证券独立 Task Run 的并发和故障隔离、保守事件归并、核查状态确定性聚合、任务/Agent 解耦、简单主备回退、外部内容隔离、配置扩展成本、可观察模型消耗、ARM + Docker 部署可用性。

### 综合研究与采集验收场景

- **统一取材引擎**：市场、行业和个股 Task 仅调整 Scope、Objective、预算和策略，均通过 `repository.select_evidence` 获取不同内容的 Evidence Package；没有三套独立查询流程。
- **跨维度关联**：研究 NVDA 可以将未直接提及 NVDA 的出口管制政策选为行业/宏观背景；该资料不能因此产生对 NVDA 的已确认直接证券关联。
- **历史背景不是新增事件**：今日新分类的旧公告进入新增候选，而三个月前的财报可以作为背景引用；二者均保留原始时间与版本。
- **转载、分歧和证据独立性**：同一官方公告的多条确认转载不占多个主要证据席位，但不同机构独立提出的相反观点保留；无法确认是转载时不能仅凭内容相似强制合并。
- **容量边界与覆盖说明**：500 条实际候选超出输入预算时，取材按维度裁剪并记录总发现/已筛选/选入数；如果检索只扫描了部分候选，必须标明 `retrieval_truncated` 与 `unexamined_count`，不假装全部检查。
- **失败与可复现**：分类积压、来源断更和取材超时能被识别；研究 Agent 失败后可复用已保存的相同 Evidence Package 重试，不污染共享 Document，不在成功条件未满足时推进研究游标。


- **跨研究复用**：同一份此前采集的官方财报能够同时被日报、行业周报和某只股票的专项研究引用。Document 只有来源、客观时间及处理状态，不存在属于哪个研究窗口或已被哪个 Run 消费的字段；每个 Run 的引用记录独立保存。
- **增量与背景分离**：日报增量窗口只包含昨天以来刚入库/更新的资料，但允许读取三个月前的财报验证新观点；旧财报属于 background，不被算作今天新发布的情报。
- **窗口无缺口**：周五日报成功后，周末发布的政策资料进入 Repository，周一日报必须能够纳入；某一次日报失败时不推进研究游标，下一次成功报告覆盖之前未处理的增量。
- **晚到资料**：一份上周五发布、周二才采集或完成分类的公告，仍可出现在周二研究的新增候选中；保留它真实的 `published_at`，不得把其发布时间错误更改为周二。

- **独立执行**：连续采集 100 篇符合 Research Scope 的资讯，形成 100 条可查资料，但不自动创建 100 份研究报告；定时研究任务按指定窗口读取全部相关文档、指标和历史结论，生成一份综合报告。
- **故障隔离**：研究 Agent 不可用时，采集、去重、分类继续运行；某 RSS/API 暂停更新时，研究仍能使用已保存数据，报告必须显示来源数据过期或覆盖缺失。
- **相关性边界**：一则没有 NVDA 字样的半导体出口政策仍可因行业/政策 Scope 被保留；无关 RSS 条目不会进入全文提取和深度处理，且过滤计数可查。
- **情绪证据**：日报综合客观市场指标和不同发布者的资讯，标明“指标变化”和“模型归纳观点”的区别，不得以重复转载的新闻数量伪装市场整体情绪。
- **事件研究不是默认**：文档完成实体解析或分类后，不会自动调用研究 Agent；只有显式启用的事件研究 Task 才能因该事件创建研究 Run。

### 一致性验收场景

- **入库与实体触发的竞态**：文档先入库，实体处理保持 pending 时，不得创建任何依赖 security_id 的研究 Run；稍后 Worker 确认关联并提交后，恰好创建一次相应证券 Run，重复领取同一事件也不能重复创建。
- **部分解析和补充识别**：文档确认证券 A，但另一个提及未解决，状态为 partial；证券 A 可以被研究。之后确定证券 B 时，仅为新增的 B 产生研究范围，不重复触发 A。
- **历史股票代码**：同一 Ticker 在不同时间对应不同 Security；历史报道使用其指代时间作 as-of 匹配，无法唯一确认时保留 unresolved，不使用当前映射覆盖旧文档。
- **转载与引用**：两篇转载同一原文的资料在 Event 内可关联，但只视为一条共享原始证据；仅引用了他人报道的独立调查不得自动认定为转载，无法判断时保持 unknown。
- **解析积压可见与恢复**：某文档入库后超过 `entity_resolution_stale_after` 仍为 pending，Overview 计数增加且 Runs 可定位文档；Worker 重启、租约恢复并成功完成后计数下降、正常触发证券 Run。连续失败达到重试上限后显示 failed、保留错误与重新入队入口，不允许静默跳过。
- **晚到结果**：某文档入库数天后才确认新证券关联，仍持久化关联并按幂等规则发出就绪事件；不按文档年龄丢弃，时效判断留给 Task/Agent。
- **宏观新闻无股票仍能研究**：一篇货币政策新闻没有直接确认的 Security，实体处理完成但证券集合为空，分类结果有 `macro`、`interest_rates`、`banking`；必须可按这些类别/标签检索，并仅按订阅该主题的 Task 触发研究，不制造任何银行股票的直接实体关联。
- **分类与实体处理相互独立**：实体解析为 `partial` 或 `failed` 时，有效的主题分类仍能入库和触发主题研究；分类失败时已确认的证券仍可触发证券研究。两者的重试分别幂等、错误可定位。
- **同一行业的相关性与事实边界**：分类模型标注 `real_estate` 仅表明行业相关，不得等价于文档提及某家房地产上市公司；股票级研究需另有可信的证券输入和证据。
- **核查冲突与修正**：同一 Finding 的两条有效核查分别支持和反驳时显示 contested；有依据地废止错误核查后重新派生状态，旧核查仍可查看，历史 Report 不被改写。

不纳入此产品基线：自动交易、行情高频撮合、复杂量化回测、多租户权限、分布式消息平台、复杂 DAG 可视化、多 Agent 自主协商和以模型为中心的自动路由优化。
