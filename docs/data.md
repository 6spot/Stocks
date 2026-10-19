# 数据采集、分类与 Intelligence Repository

本篇定义可复用的情报数据资产、来源接入和检索契约。执行与研究逻辑见 [任务执行](execution.md)、[研究取材](research.md)；返回 [架构总览](architecture.md)。

## Source 与 Connector 设计

### 通用接入类型

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

### 来源与发布者

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

### Research Scope 与有界采集

Research Scope 定义“**值得采集和后续研究的内容范围**”，与 Source（从哪里获取）、Connector（如何获取）及 Strategy（如何研究）分离。一个 Scope 可以由 Watchlist、行业、宏观主题、金融/监管政策主题构成；它不要求每篇资料都直接提到某个股票代码。

| Scope 内容 | 示例 | 采集原则 |
| --- | --- | --- |
| 关注证券 | Watchlist 内的 Security / Issuer | 有精确证券/公司 ID 的 API 优先按上游参数查询 |
| 关注行业 | 半导体、能源、银行业 | 采集产业链、竞争格局、供应链及影响该行业的政策 |
| 宏观与市场 | 利率、通胀、就业、流动性、波动率 | 采集可观察的指标、官方发布以及相关金融资讯 |
| 金融政策与法规 | 货币政策、监管、出口管制 | 不要求公告直接提及 Watchlist 证券 |

**采集两层过滤：**

1. **上游过滤**：HTTP API 能按发行主体、时间、事件类型或主题查询时直接限定范围；RSS 优先订阅对应行业/市场频道，并按发布时间或源游标增量获取。不能假设每个 RSS 提供按股票和关键词查询能力。
2. **采集后的相关性标注**：对已配置且已成功获取的 Source 条目，先可靠保存可获取的原始内容和元数据，再以规则或轻量分类模型生成主题标签、相关性与抽取决策。相关性低可不进入重点索引/研究候选，但**不得仅因分类结果、是否值得抽取或是否属于当前 Watchlist 而删除、丢弃已采集内容**。对尚未请求的全网资料，仍由 Source 配置和上游查询范围限制采集，不要求无边界抓取。

相关性标注应**适度宽松**：不能只匹配 Ticker；与关注行业、公司经营环境、宏观政策间接相关的内容也属于研究候选。判断不确定时保留原文及待分类状态。每个 Source 的获取数量、已保存数量、跳过深度处理数量、失败和最后成功时间应可查看；**跳过抽取不等于过滤或删除原文**。

采集范围从 Research Scope 读取，不在每个 Connector 内重复维护一套投资偏好。Source 的访问权限、实际 API 查询能力及频率限制仍独立生效。

#### 来源更新、采集状态与覆盖

**不建设独立 Source Monitor。** 采集使用现有 Source 配置、Task 调度、`source_cursors`、`collection_runs` 和文档后处理状态；Overview/Runs 通过查询这些记录展示健康情况。

- Source 可以配置 `schedule`（实际轮询节奏）与可选 `expected_freshness`（根据该来源实际披露节奏判断是否过期），二者**不相等**：每 15 分钟查询一次 RSS 不代表每 15 分钟必须有文章；月度经济指标不能按日更要求判故障。不了解发布节奏时不设置硬性过期告警。
- Collection Run 记录 `started_at`、`finished_at`、`status`、`fetched_count`、`filtered_count`、`ingested_count`、`error_count`、`last_error`；与 Source 游标一起能够计算最近成功采集时间、失败次数及覆盖范围。原始请求成功且返回零条是**正常空结果**，不是报错。
- 三类状态分别呈现：**采集是否成功**、**来源的数据是否按已知节奏更新**、**已入库资料是否完成分类/实体解析**。来源长期不发布、Connector 故障和 Worker 分类积压不能混为一谈；来源未提供或实际获取不到的资料在研究覆盖信息中明确提示。
- 游标只在需要持久化的结果及采集统计成功提交后推进；失败 Run 记录原因供 Worker 按原有策略重试。采集计数支持发现过滤过严等问题，不能因为采集任务成功就断言所有应有资料都已覆盖。

不引入新告警调度服务、健康评分模型、专门心跳表或完整的外部数据发布日历。V0 先有可查询的事实、错误与缺口；真实来源有明确披露规律时再配置针对性过期判断。

## Intelligence Repository 与存储

### 存储分工

- PostgreSQL：Source、采集状态/游标、发行主体与证券身份、结构化行情与财务数据、标准化 Document 元数据、可搜索正文、股票与事件关联、Task/运行记录、分析结论与证据关联。
- pgvector：Document Chunk 的 Embedding 与语义相似度能力，和 PostgreSQL 全文检索、结构化过滤结合构成混合检索。
- 远程 S3 兼容存储：HTML/XML/JSON 原始快照、PDF、研报及较大的原始文件。数据库只保留 Object Key、内容哈希、尺寸、类型等元数据。
- S3 通过标准 API 访问。可利用 Tailscale 私有网络连接独立存储服务器，但应用不绑定某个私网产品或存储厂商。

写入流程为：获取原始内容 → 存储 S3 不可变快照 → 数据库事务内保存文档与幂等键、更新游标并登记后续事件 → 后续分类、标签、按需抽取。数据库失败时允许重试并清理孤立对象；绝不在原始内容未可靠保存时推进游标。**对纳入配置 Source 且成功采集的每份资料，无论抽取决策如何，都长期保留原文/原始响应、来源与时间信息；分类和抽取结果只作为附加数据，不替换或删除原始资料。** 如果 RSS 只返回标题/摘要而正文尚未取得，要保留已得到的 Feed 原始响应与链接，明确标记正文未获取并按配置继续尝试，不能把摘要冒充完整原文。

### 核心实体

| 实体 | 数据含义 |
| --- | --- |
| sources | 上游身份、Connector 类型、出版主体、信任配置 |
| source_cursors / collection_runs | 上游采集增量位置、运行时间和状态、抓取/过滤/保存数量及错误；用于直接计算来源覆盖与新鲜度 |
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
| data_series / data_observations | 非证券专属的宏观、政策相关经济指标与市场指标的统一序列定义、观测值、时间、来源和历史修订 |
| financial_facts | 财务指标、报告期间、单位、披露时间、修订与原始申报引用 |
| financial_quality_checks | 结构化金融数据的口径、计算及同口径跨来源检查结果、差异及原始引用；可依附金融事实/采集 Run 存储，无需独立服务或专属物理表 |
| corporate_actions | 拆股、股息、合并、证券变更等事件 |
| events / event_documents | 同一事件与文档的候选/已确认关系及归并依据；政策变化复用其关联及带时间的事件属性 |
| tasks / task_runs / step_runs | 任务定义快照、独立研究范围与增量窗口、按证券独立的运行及逐步骤执行记录 |
| research_cursors / research_inputs | 每个任务/范围的成功增量位置，以及每次研究实际选用的文档、结构化数据与分类版本引用；逻辑数据契约可由既有运行记录承载 |
| evidence_packages | 某次研究的取材查询条件、候选覆盖统计、入选证据及其版本、排除/缺口说明的持久化快照；可直接作为 Task Run 的结构化输出存储，无需独立服务或专属物理表 |
| analyses / findings | 研究过程、结构化结论、事实/推断类型、在本次研究中的重要性、模型及提示词版本 |
| finding_relations | 一次研究的 Finding 对一个或多个历史 Finding 的延续、修正、挑战或取代关系，保留证据与判断理由 |
| verification_runs / finding_verifications | 独立核查执行、判断、依据与核查模型信息 |
| evidence_links | 单条结论与 Document/Chunk/结构化金融数据的可追溯关系 |
| reports | 可阅读报告、生成时的结论/核查状态快照、动态内容块及所引用的 Analysis 与数据/图表 |

数据库表可根据性能合理拆分或合并，但上述信息语义及可追溯关系必须保留。Source 信任等级变更，不覆写既有研究执行时使用的快照。**资料只持有自身的发布时间、采集/入库时间、有效版本、可研究状态与关联元数据；研究进度、使用记录及时间范围均属于独立的 Task/Run 数据。**


### 发行主体、证券身份与消歧

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

### 自动分类、标签与关联边界

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
| `should_extract` | 同一次低成本分类请求给出的布尔抽取决策；表示是否需要后续 LLM 结构化抽取，**不是保留/删除原文的开关** |

标签及类别使用受控词表（配置文件可维护），前端与 Task 使用**稳定标签 ID** 查询而非依赖自由文本字符串。词表允许自然扩展与修订，但保留分类时的词表版本；初版可以将分类作为一条结构化 JSON/JSONB 记录存储，不强制建立独立的标签服务或多张关联表。

**自动分类流程与失败边界：**

1. Document 原文可靠入库后，Worker 先使用已知元数据/规则，必要时**一次调用 Jev 这类低成本 Decision Model** 完成类别、多个标签和 `should_extract` 判断。模型只选择预定义结果，**不在这一阶段生成事实正文、数字或结论**；不依赖某个固定厂商。模型接口沿用[Agent 与模型配置](execution.md)的 Provider/Adapter。
2. 分类与抽取决策必须符合固定 Schema 和受控词表；超出选项、模型故障或无效结构时按有限重试处理，保留原文及待处理/失败状态，不能默认为 `should_extract=false`。分类完成后，不论标签是否为空、类别是否为 `other`，文档仍在 Repository 中可查询。
3. 分类状态与实体解析状态**独立**：`classification_status = pending / completed / failed`；一方完成无需等待另一方。已完成的结果与其修订记录持久化，重新分类保留历史，更新当前结果并依据实质变化发布新事件。
4. 分类结果、状态/修订号及 `on_document_classified` 持久化事件在同一数据库事务写入。Worker 仅消费已提交事件；失败保留错误、重试与 Runs 可查询记录，防止分类失败造成无声遗漏。
5. `should_extract=true` 才按需进入后续生成式 LLM 事实抽取；`false` 不触发抽取，但保留原文、标签和研究可检索性。抽取结果保存独立记录、原文位置/证据引用和处理状态，失败可重试且绝不覆写 Document。结构化 API 指标由程序直接解析，不需要经过 LLM。抽取决策和抽取结果都允许未来重新处理，不将首次判定视为永久正确。
6. 主题或行业相关性不自动扩展成 `document_entities` 的 `confirmed` 证券。只有原文真实提及且能够消歧的证券才产生**直接实体关联**；由政策或宏观事件推测可能受影响的公司，应标记为研究候选，并由研究 Task 使用其他证据独立验证。

**示例：**

~~~yaml
document_classification:
  category: macro
  event_type: monetary_policy
  topics: [interest_rates, liquidity]
  related_industries: [banking, real_estate]
  regions: [US]
  should_extract: true
  direct_security_ids: []
~~~

此处 `should_extract` 仅决定是否继续结构化抽取；它不决定原文保留、分类有效性或能否参与研究。`direct_security_ids` 是从独立的已确认 `document_entities` 查询得到的阅读视图字段，**不由分类模型直接决定**；真实持久化时由分类记录与实体关联组合展示。该文档没有直接提及的证券，仍然可以被宏观、行业研究 Task 使用。模型所输出的“可能影响”标签只代表待研究的主题关联，绝非确定的股票涨跌判断或投资建议。

#### 市场情绪的数据含义

市场情绪是综合研究的一个输入维度，而不是一条新闻的“乐观/悲观”标签或模型自报的全市场分数。

- **可观察指标**：波动率、成交量、资金流、期权/调查等可获得的情绪相关数据，以结构化金融指标形式记录数值、单位、市场/证券范围、时间与来源。指标的解释依赖研究策略，不将任何单个指标直接定义为“市场情绪真值”。
- **信息叙事/观点**：媒体、研究机构、企业及政策发布的信息可按主题和可选的情绪倾向归类，保留原始出处与发布时间。模型给出的倾向属于来源文本的分类结果，不能自动代表整体投资者观点。
- **阶段性研判**：报告可以综合指标变化和多来源信息，得出带有证据、时间范围及不确定性的市场氛围判断；数据样本偏差、转载和观点分歧需要明确说明，不将新闻条数简单当作市场情绪统计。

可配置的分类模型只负责必要标签及相关性判断；“市场情绪如何变化、对哪些研究范围有意义”由定期研究任务在多源上下文中形成，而非每篇文档单独生成研究结论。

### 结构化金融数据与时间语义

Market Data 通过同一 Source / Connector / Source Mapper 接入，但按数值事实存储，不强行写入 Document Chunk。Agent 通过 Repository 的只读 Tool 获取指定证券、发行主体、时间范围和指标的数据。能够定期获取的数据应沉淀并复用，不能仅依赖 Agent 每次临时查询外部服务。

- market_bars：security_id、时间粒度/时间戳、交易场所、OHLCV、币种、调整口径、Source 与数据版本。
- financial_facts：issuer_id、指标及报表口径、数值、单位/币种、财务期间、公开披露时间、采集时间、修订记录和原始申报引用。
- corporate_actions：拆股、分红、并购或证券标识变动等，保存生效日期、可获知时间和原始来源。
- data_series / data_observations：宏观经济与非证券专属市场指标的数值序列及观测版本，例如 CPI、失业率、收益率、波动率等。**只增加一种通用结构化指标模型**，不为不同指标创建各自的表、Connector 或研究流程。

**最小序列契约：**

| 记录 | 必要内容 |
| --- | --- |
| `data_series` | 稳定 `series_id`、指标 ID/定义、适用地域或市场、频率、单位/币种与统计/调整口径；同名但口径不同的指标不得混作一个序列 |
| `data_observations` | `series_id`、观测期/时间 `period`、数值 `value`、`source_ref`、`available_at`、`collected_at`、`revision`、原始记录 `raw_ref`；保留同一时期的历史修订，允许不同来源分别入库 |

研究读取按 `as_of` 选择当时已公开且系统已获取的适用版本；`available_at`（来源对外可获知）与 `collected_at`（Stocks 实际获取）不得混淆。**观测期不是发布期**：上月 CPI 本月发布，它进入本月新增资料，但数值仍属于上月观测；旧数值今天修订，也作为修订变化进入研究，不伪造新一期观测。所有结构化指标继续通过现有 Source/Connector/Source Mapper 入库，并接受下述质量核对。


- 研究时使用 as-of 语义：按研究时点只读取当时已公开的数据版本，不因后续财报修订或行情调整造成前视偏差；研究记录固定具体数据版本或引用。
- S3 保留结构化数据原始响应/文件快照，PostgreSQL 保存规范化数值、来源和校验标识。

#### 金融数据的标准化与质量核对

**数据质量检查从采集入库阶段开始，不等到生成报告时才发现数值、单位、口径错误。** Source Mapper 先保存原始值、原始字段和响应快照，再规范化可理解的数字。数值事实至少可追溯：`metric_id`、适用的 `issuer_id`/`security_id`、`value`、`unit`/`currency`、`period_start/end` 或观测时间、`fiscal_period`、`accounting_basis`（GAAP/Non-GAAP 等）、`consolidation_scope`、调整/复权口径、`published_at`/`available_at`、`collected_at`、`source_ref`、`raw_ref` 和版本。未知口径标记未知，不凭模型推断填空。

1. **先判断可比性**：指标定义、币种、单位、财期、会计口径、合并范围、证券股类、调整版本匹配或完成有依据的换算后，才跨来源比较。GAAP 与 Non-GAAP、ADR 与普通股、不同复权价格不直接计算“误差率”。
2. **计算程序化**：市值 = 给定时点未复权价格 × 对应股数口径；同比/环比、利润率、估值倍数、现金流等按明确的公式和输入单位计算。使用 Decimal 或等价定点精度；保存公式版本、输入数值/来源版本、计算时点与结果，禁止 LLM 心算产生可引用的财务事实。
3. **一手披露优先，独立来源可交叉验证**：关键事实优先使用监管、交易所或公司原始披露，并尽可能用真实独立的第二来源核对。同一原始财报被两个网站转载，不形成两份独立证据。缺第二来源可保留一手数据，但不得宣称完成双源验证。
4. **保留冲突与质量状态**：检查结果可以是 `unchecked / matched / discrepant / incomparable / insufficient_sources / check_failed`，保存各来源原值、差异、原因、检查时间/方法/版本。容差依指标口径、舍入及精度配置，**不照搬外部 Skill 的统一 1% 或 2% 阈值**。不能用均值/中位数静默掩盖来源冲突。
5. **质量错误不污染共享数据**：原始采集成功即可靠入库；质量检查失败仅影响对应结构化值的质量标识、选择与后续 Verifier，并不造成 Document 丢失。修复后追加新检查或修订记录，原版与旧研究引用仍可追溯。
6. **历史行情口径明确**：未复权价格、拆股/分红、复权调整方法与数据版本分开保留；总回报需明确计入分红，跨拆股的每股比较必须使用一致口径。


### 事件归并与证据独立性

**Event 表示现实中的一件事，不等于文章来源，也不等于向量相似集合。** 同一家公司同一天发布不同公告可对应不同 Event；多家媒体报道同一公告可以对应一个 Event，但其证据可能仅来自一份原始披露。

事件归并流程：

1. 用公告/上游唯一 ID、Canonical URL、原文哈希做确定性去重；严格区分“内容相同”与“只是报道同一事件”。
2. 利用已确认的证券或发行主体、事件类型、发生与披露时间、全文及向量检索寻找候选 Event。
3. 只有满足可解释的规则或经过明确语义判断，才在 `event_documents` 中记录 `confirmed` 归属；证据不足时保留 `candidate`，不能为提高归并率而强制合并。
4. 保存关联来源、判断方法和依据；允许纠正归并但不删除 Document 和来源元数据。

#### 政策生命周期（复用 Document 与 Event）

政策不只是新闻标题，而是可能经历草案、正式发布、生效、延期、修订、暂停、废止的可追踪对象。**V0 不新增政策专属服务或通用状态机**：

- 政策原始文本及每次新文件依旧作为 Document；分类标记 `category=policy` 及相关主题、地区。
- 能从官方文号、监管事项编号等可靠依据确定属于同一政策时，用稳定的 `policy_ref`（可由既有 Event 关联属性表示）关联有关 Document / Event。关联不确定保留候选，不按标题相似自动合并。
- 每次明确的政策变化作为带来源的 Event/事件属性，最小记录 `policy_ref`、`change_type`、`announced_at`、`effective_at?`、`document_ref`、`evidence_ref` 及版本。变更类型例如 proposed、adopted、effective、amended、postponed、suspended、repealed；**`amended` 是一次变化，不代表永恒的当前状态**。
- 是否已经生效只由可确认的正式依据、生效条件与相应时间判断。尚未确定的生效时间记为未知；不能把草案当作已生效，也不能凭媒体推测标成官方状态。
- 研究通过已有 `search_changes` 找到新政策文件/变化，通过 `search` 读取相关历史文本；必要时按 `as_of` 从已有变化记录还原当时已知的政策进度，保留后来修订的历史。

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

### 检索

Repository 对调用者提供：按 Issuer/Security、行业、主题、类别、事件类型、地域、来源、事件、时间和数据可获知时点的查询；文档全文/语义/混合检索；结构化行情、财务、通用指标序列/版本与政策变化查询；相似事件和证据读取。

分类结果与实体解析结果支持独立过滤：`confirmed_security_ids` 为空而分类完整的宏观/行业资料是**正常数据**；存在 `unresolved` 提及的文档可按需筛选，但不作为默认的人工审批队列。

向量必须记录 Embedding 模型和维度；不同模型的向量不直接混合排序。pgvector 从初版引入，具体向量索引按已配置的 Embedding 模型建立。向量相似度只表示语义相关，不表示事实真实性或证据独立性。

**取材查询接口：** `repository.search_changes` 以 Task Run 的增量窗口与 `as_of` 找到新增入库、完成分类、重要修订等可研究变化；`repository.search` 可不受增量窗口限制，按同一 `as_of`、Scope 和类型检索历史证据。两者都返回稳定引用、版本、时间、来源、关联依据、查询批次和可复查的排序/过滤信息；查询不能修改 Document 的全局状态。Evidence Selection 按[统一研究取材](research.md)在这两个 Repository 查询之上执行，不另建一套取材数据库。

## 来源可信度、证据与安全

可信信息分为三个独立维度：

1. **来源身份与默认可信度**：T1 官方原始记录、T2 可识别原始发布者、T3 二手媒体/机构、T4 未核实来源。这些是可配置初始类别，不是客观真伪概率。
2. **具体论断属性**：披露事实、已知数值、个人观点、预测、未经证实的传闻，分别标记。
3. **核实状态**：unverified、supported、contradicted、contested、insufficient_evidence，保留支持与反面证据；这些状态由独立核查记录按[独立核查与状态聚合](research.md)的规则派生，不由生成该 Finding 的 Agent 自行宣布。

可高度信任官方系统确实披露了一份文件，但不因此认定文件中的未来预测必然成立。低等级来源可以作为线索，不可仅因级别低就静默丢掉重要未核实事件。可信度、重要性、交易价值不能混成一个分数。

安全边界：

- 原文、RSS 正文、网页、搜索结果和 MCP 返回值均为不可信数据，不执行其包含的指令。
- Agent 的 system 指令、工具白名单、参数边界由可信 Task/Agent 配置控制；工具实际执行前由 Stocks 校验。
- 外部服务默认仅授予只读搜索和提取能力，不授予交易、任意数据库写入、Shell 或密钥读取能力。
- 对抓取内容进行类型/大小约束、URL 安全校验、限流与日志脱敏。
- 每条重要分析结论必须引用可查证的 Document/Chunk 或说明证据不足。
- “找到多条报道”不代表“多个独立来源确认”；需要追踪最初发布者与转载关系。
