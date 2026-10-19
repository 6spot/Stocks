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
8. **只做最小充分的调度与回退**。顺序步骤、简单条件、明确的按证券 fan-out 和触发器满足原型，不引入动态 DAG、模型评分路由或多 Agent 自主协商。
9. **证券身份与金融数据是一等公民**。公司、证券、交易代码分开管理；结构化行情和财务数据不强行转换为 Document。

## 3. 概念与职责

| 概念 | 定义 | 负责 | 不负责 |
| --- | --- | --- | --- |
| Source | 信息发布主体或上游数据服务的一份配置 | 身份、采集范围、访问参数、信任级别 | 规定 AI 如何分析 |
| Connector | 通用采集实现：RSS、HTTP API、Web | 请求、增量获取、格式解析的通用流程 | 判定利好、组织研究任务 |
| Source Mapper | 特定来源的字段映射及必要解析规则 | 转换上游格式为统一信息记录 | 变成独立底层 Connector 类型 |
| Intelligence Repository | 情报、证券实体、金融事实与证据的统一数据访问边界 | 持久化、去重、索引、查询、来源关联 | 自主决定投资策略 |
| Issuer / Security | 发行主体与可交易证券 | 稳定身份、历史代码、文档和数据关联 | 以当前 Ticker 替代永久身份 |
| Market Data | 行情、财务及公司行为等结构化数据 | 带时间口径的数值存储与追溯 | 用文本切片替代数值序列 |
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
- 分析 Task 从 Repository 按发行主体或证券、时间、信息类型、可信度和检索条件选择输入；行情和财务等结构化数据通过同一 Repository 查询接口提供。
- 一个 Task 可以按证券范围拆分为多个独立 Task Run；具体规则见第 8 节。
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
| entity_mentions / document_entities | 文档中提及的主体与证券、消歧证据和状态 |
| watchlists / watchlist_securities | 研究标的列表及成员证券 ID |
| documents | 每份获取的信息、来源元数据、对象引用及实体处理状态 |
| document_relations | 文档间去重、转载和引用关系及其判定依据 |
| document_chunks | 可检索文本切片、位置与所属文档 |
| embeddings | 向量、模型、维度、生成时间及切片引用（可以与 Chunk 关联） |
| market_bars | 证券行情 OHLCV、数据时间、交易场所、调整口径及来源 |
| financial_facts | 财务指标、报告期间、单位、披露时间、修订与原始申报引用 |
| corporate_actions | 拆股、股息、合并、证券变更等事件 |
| events / event_documents | 同一事件与文档的候选/已确认关系及归并依据 |
| tasks / task_runs / step_runs | 任务定义快照、按证券独立的运行及逐步骤执行记录 |
| analyses / findings | 研究过程、结构化结论、事实/推断类型、模型及提示词版本 |
| verification_runs / finding_verifications | 独立核查执行、判断、依据与核查模型信息 |
| evidence_links | 单条结论与 Document/Chunk/结构化金融数据的可追溯关系 |
| reports | 可阅读报告及所引用的 Analysis |

数据库表可根据性能合理拆分或合并，但上述信息语义及可追溯关系必须保留。Source 信任等级变更，不覆写既有研究执行时使用的快照。

### 6.3 发行主体、证券身份与消歧

Issuer 是公司或发行主体，Security 是可研究或交易的具体证券，两者以内部稳定 ID 关联。一个 Issuer 可以关联多种 Security（不同股类、ADR 等）；Ticker 只是在特定交易场所、特定有效期间使用的标识，不能作为永久主键。外部 CIK、FIGI 等标识需保留命名空间及来源；名称、Ticker 或公司关系的变更保留历史有效期。

- Watchlist 以 security_id 表示研究标的；需要公司层面分析时，通过 issuer_id 查询其关联证券。
- Document 可以提及多个 Issuer/Security。文档先完成原始内容入库，实体识别在独立 Worker 中**异步后处理**；识别原文提及并保存候选、规则匹配结果和必要时的模型消歧结果，不在采集事务中等待模型。
- 文档的实体处理状态使用 `pending`、`resolved`、`partial`、`failed`：`resolved` 表示处理结束且无待消歧提及（可以没有股票）；`partial` 表示处理结束但仍有不确定提及；`failed` 表示处理失败、可重试。未确定的关联保留 unresolved，不强行匹配。
- 解析历史 Ticker 时采用 **as-of** 语义：有明确的被指代时间时使用该时间，否则以 `published_at` 为默认时点；若时间缺失或映射冲突，则维持 unresolved。实体关联需记录解析时点、所用标识与判断依据，不能按当前 Ticker 直接重写历史文档。
- 优先使用可验证的外部标识、交易所、上下文和历史有效期；合并、改名、拆分、退市与证券更换记录为可追溯的实体/证券关系变动，不删除旧标识和历史关联。

### 6.4 结构化金融数据与时间语义

Market Data 通过同一 Source / Connector / Source Mapper 接入，但按数值事实存储，不强行写入 Document Chunk。Agent 通过 Repository 的只读 Tool 获取指定证券、发行主体、时间范围和指标的数据。能够定期获取的数据应沉淀并复用，不能仅依赖 Agent 每次临时查询外部服务。

- market_bars：security_id、时间粒度/时间戳、交易场所、OHLCV、币种、调整口径、Source 与数据版本。
- financial_facts：issuer_id、指标及报表口径、数值、单位/币种、财务期间、公开披露时间、采集时间、修订记录和原始申报引用。
- corporate_actions：拆股、分红、并购或证券标识变动等，保存生效日期、可获知时间和原始来源。
- 研究时使用 as-of 语义：按研究时点只读取当时已公开的数据版本，不因后续财报修订或行情调整造成前视偏差；研究记录固定具体数据版本或引用。
- S3 保留结构化数据原始响应/文件快照，PostgreSQL 保存规范化数值、来源和校验标识。

### 6.5 事件归并与证据独立性

Event 是多篇文档可能共同描述的现实事件，并非一篇新闻或一组相似向量。处理顺序：

1. 使用公告 ID、上游事件标识、Canonical URL、内容哈希等确定性规则处理真正的重复记录。
2. 通过已消歧的 Issuer/Security、事件类型、发生/披露时间、全文与向量相似度查找候选 Event。
3. 对候选关系使用明确的规则或可选 Agent 语义判断。仅相关但并非同一件事的报道不得合并；证据不足时暂不合并。
4. event_documents 保留关系依据、匹配方式和确认状态；归并允许人工纠正或重新评估，而不覆盖原始 Document。

同一 Event 下有多篇 Document 不代表有多份独立证据。用最小的 `document_relations` 保存文档之间的 `duplicate_of`、`republished_from`、`cites` 等关系：记录发起文档、目标文档（若已入库）、未入库时的目标原始 URL、关系类型、判定依据及 `candidate / confirmed` 状态。引用别人的材料并不必然等于转载；未确认的关系不自动作为独立性结论。Verifier 沿确认的转载/原始出处关系判断证据是否真正独立，不按 Document 数量计数。

### 6.6 检索

Repository 对调用者提供：按 Issuer/Security、来源、事件、时间和数据可获知时点的查询；文档全文/语义/混合检索；结构化行情财务查询；相似事件和证据读取。

向量必须记录 Embedding 模型和维度；不同模型的向量不直接混合排序。pgvector 从初版引入，具体向量索引按已配置的 Embedding 模型建立。向量相似度只表示语义相关，不表示事实真实性或证据独立性。

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
| trigger | manual、schedule（明确时区）、on_new_documents（文档级）、on_entities_resolved（证券级）等 |
| scope | 可选的证券范围；可按 security_id 展开独立 Run |
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

### 8.1 按证券 fan-out

fan-out 发生在 **Task Run 创建阶段**，不在 Task 的 steps 中提供循环/并行 DSL：

- Task 声明单证券或 Watchlist 等 scope。若配置 `for_each: security`，触发时先解析为有上限的去重 security_id 集合，再为每个证券创建独立 Task Run；每个 Run 使用相同 Task 定义并携带自己的 security_id、时间窗口和参数。
- 使用 task_batch_id 关联本次展开的所有 Run，单证券失败只影响对应 Run；Worker 以 `max_concurrency` 限制并发。重复触发使用由 Task 定义版本、触发 ID、证券 ID 和数据窗口组成的幂等键。
- 一个收集任务仍可只产生一个 Run 并处理一批文档；只有显式配置证券级 fan-out 的任务才按证券展开。
- 后续汇总任务从 Repository 查询指定批次或时间窗口的成功结果，并显式展示缺失、失败或尚未完成的证券；不隐含“所有分支成功才能继续”的任务依赖屏障。
- `on_new_documents` 只表示文档已入库，供与实体关联无关的文档级任务使用；**不得直接用于需要 security_id 的 fan-out**。
- 异步实体解析把 `document_entities` 与文档 `resolved / partial` 状态一并提交，并在**同一数据库事务**写入可被 Worker 领取的 `on_entities_resolved` 事件记录；提交后才允许按已确认的 security_id fan-out。失败状态不发证券级触发；没有证券关联时事件可以记录，但不产生证券 Run。
- 后续重新解析产生新增且已确认的证券关联时可以再次触发；以文档、证券、任务和研究窗口构成幂等边界，避免对已有证券重复研究。触发和领取使用现有 PostgreSQL 记录与 Worker，无需另建消息总线。

### 8.2 任务配置示例

下列任务展示纯采集、按文档触发分析和按证券展开的不同场景：

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
    trigger: {kind: on_entities_resolved}
    scope: {from: trigger.security_ids, for_each: security}
    steps:
      - id: judge
        use: agent.event_judge
        input: {document_ids: trigger.document_ids, security_id: run.security_id}
      - id: analyze
        use: agent.researcher
        input: {document_ids: trigger.document_ids, security_id: run.security_id}
        when: {step: judge, field: should_analyze, equals: true}
      - id: save
        use: repository.save_analysis
        input: analyze.output
        when: {step: analyze, status: succeeded}

  daily_watchlist_research:
    type: analysis
    strategy: long_term
    trigger: {kind: schedule, cron: "0 8 * * 1-5", timezone: America/New_York}
    scope: {from: watchlist, for_each: security, max_items: 50}
    execution: {max_concurrency: 3}
    steps:
      - id: research
        use: agent.researcher
        input: run.security_id
      - id: save
        use: repository.save_analysis
        input: research.output
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

## 11. 分析、独立验证与研究结果

分析结果以结构化记录优先，Markdown 报告为其展现形式。单条 Finding 至少包含：claim、kind（fact/inference/opinion 等）、verification_status、支持和反面 evidence_refs、关键假设、时间边界与来源说明。

每次 Analysis 保存 Task Run、Agent/模型、实际提示词版本或哈希、输入文档与金融数值版本引用、输出 Finding、生成时间和用量。Report 引用 Analysis，可被其他研究任务检索，而不是把报告正文当作无法拆分的唯一数据。

### 11.1 独立核查契约

- Researcher 负责提出 Finding 与证据引用；新 Finding 默认 `unverified`。Researcher 的自评、模型置信分数或重复表达不能直接转为 `supported`。
- Verifier 是**与研究生成步骤分离的执行角色**，独立读取原始引用和可用的反面证据，逐条检查“证据是否真正支持所表达的结论”；优先核对日期、证券身份、来源独立性与数据可获知时点。
- 对数值、同比、财务口径等可确定检查的事项优先执行确定性计算；模型判断补充语义核查，不替代可计算校验。
- Verifier 可使用不同提示词并可按任务选择同一或不同模型；不强制双模型，也不要求每份普通摘要都进行昂贵复核。需要升级为已核实状态的结论必须具有独立核查记录。
- 验证结果写入独立的 `verification_runs` 与 `finding_verifications`，保留核查输入、方法、引用、模型/规则版本、判定与理由，不覆盖原始 Finding。每条核查记录标明是否仍然有效；被重新核查正式取代时，旧记录保留但不参加当前状态计算。**不默认只取最新记录，也不让历史失效结论永久造成冲突**。
- 当前 `verification_status` 仅从有效记录派生：无记录为 `unverified`；只有证据不足为 `insufficient_evidence`；存在支持且没有反驳为 `supported`；存在反驳且没有支持为 `contradicted`；同时存在有效支持与反驳为 `contested`。证据不足记录不覆盖已有有效支持或反驳。
- `supported` 的含义是“提供的证据支持此结论”，不是保证未来预测正确或证明现实世界的绝对真值。Report 固定生成时的核查状态快照，后续再核查不静默改写历史报告。
- 首版只需独立核查记录、有效标记和上述聚合规则；不引入核查审批流、复杂证据评分或多模型强制投票。报告展示未核实、相互冲突及证据不足的结论。

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

验证重点：增量幂等、原始证据及转载关系追溯、异步实体解析完成后的可靠证券级触发、证券身份与历史代码 as-of 消歧、结构化金融数据的时点正确性、按证券独立 Task Run 的并发和故障隔离、保守事件归并、核查状态确定性聚合、任务/Agent 解耦、简单主备回退、外部内容隔离、配置扩展成本、可观察模型消耗、ARM + Docker 部署可用性。

不纳入此产品基线：自动交易、行情高频撮合、复杂量化回测、多租户权限、分布式消息平台、复杂 DAG 可视化、多 Agent 自主协商和以模型为中心的自动路由优化。
