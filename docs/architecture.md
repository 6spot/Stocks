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

**永久身份不能依赖股票代码。** Issuer（公司或发行主体）使用内部 `issuer_id`；Security（具体证券，如不同股类或 ADR）使用内部 `security_id`，同一 Issuer 可以关联多种 Security。Ticker、交易所代码、CIK、FIGI 等均是有来源和适用范围的外部标识，而非内部主键。Watchlist 保存 `security_id`；公司层面研究通过 `issuer_id` 聚合相关证券。

**历史身份字段与约束：**

| 记录 | 关键字段 | 约束与语义 |
| --- | --- | --- |
| `issuers` | `issuer_id`、名称、外部身份引用 | 公司改名不重建内部 ID；公司合并或实体变化保留关系历史 |
| `securities` | `security_id`、`issuer_id`、证券类别 | 股票类别、ADR 等不因为公司相同就合并 |
| `security_identifiers` | `security_id`、`type`（ticker/FIGI 等）、`value`、`exchange`、`valid_from`、`valid_to`、`source_ref` | 有效期采用 `[valid_from, valid_to)`，允许空上界；同一命名空间、交易场所和时间点出现多个候选时不得猜测 |
| `entity_mentions` | `mention_id`、`document_id`、原文词语、位置或文本引用、识别到的实体类型、`reference_time`、`time_basis`、`status` | 每处提及可独立消歧；`unresolved` 必须可查询 |
| `document_entities` | `document_id`、`issuer_id` 或 `security_id`、`resolution_status`、`resolved_as_of`、`method`、`evidence_ref`、`resolution_revision` | 支持多对多关系；只有 `confirmed` 的 `security_id` 能驱动证券级任务 |
| `documents` | `entity_resolution_status`、`entity_resolution_revision`、`entity_resolved_at`、错误信息（失败时） | 文档完整入库与实体解析相互独立；初始状态为 `pending` |

以上列出要求保留的**数据语义和字段契约**；具体索引、主键类型或部分辅助字段的物理拆分由数据库实现决定，不得丢失历史时间和确认依据。

**实体解析时点（as-of）规则：**

1. 一个提及若明确指代某个历史时间，以该提及的 `reference_time` 为准，并保存原文依据（例如“该公司在 2023 年仍使用代码 X”）；否则默认使用文档的 `published_at`。
2. 选择在解析时点有效的标识，即 `valid_from <= reference_time < valid_to`（空上界视为持续有效），结合交易所、证券类别和原文上下文进行匹配；优先采用可核对的身份标识。
3. 不能用当前 Ticker 映射替代历史匹配，也不能用采集时间 `collected_at` 充当文章的历史指代时间。若缺少可靠时点或匹配不唯一，保存 unresolved 与候选依据，不创建猜测性的已确认关系。
4. 解析结果应记录采用的 `resolved_as_of`、`time_basis`（explicit_mention / document_published）、匹配方法与来源依据。新规则重新解析只修正当前关联，不覆盖原文或抹去历史解析结果。

**异步解析与文档状态：**

- 采集先完成 S3 原文/快照与 Document 入库，记录 `pending`；不得在采集事务内等待模型或联网消歧。后台 Worker 随后执行识别、规则匹配及必要时的 Agent 消歧。
- `pending`：等待或正在解析；`resolved`：本轮处理完成且没有未解决的实体提及（允许识别结果为空）；`partial`：本轮处理完成但仍存在未解决的提及；`failed`：处理过程失败，保存错误，允许按重试策略重做。
- 每轮解析只生成一组可确认的关联及一组仍未确认的提及。**状态 `partial` 不妨碍已确认证券参与后续研究**，但未确认候选绝不触发证券任务。
- Worker 先计算本轮关联集合与当前已确认集合之间的差异。只有首次完成或发生实质变更时才增加 `entity_resolution_revision`；相同数据重复处理保持修订号不变。
- 将解析结果、文档状态和后续触发记录放在同一数据库事务提交。失败时不发布证券触发；未确认提及可由之后的规则升级或补充资料重新解析。
- 后续纠正历史身份映射时，保留旧研究的输入快照；对依赖错误映射的现有结果标记需重新评估，当前查询不再将其当作正确证券的有效研究结果，必要时按新的确认关系重新发起任务。无需引入全量依赖图。

### 6.4 结构化金融数据与时间语义

Market Data 通过同一 Source / Connector / Source Mapper 接入，但按数值事实存储，不强行写入 Document Chunk。Agent 通过 Repository 的只读 Tool 获取指定证券、发行主体、时间范围和指标的数据。能够定期获取的数据应沉淀并复用，不能仅依赖 Agent 每次临时查询外部服务。

- market_bars：security_id、时间粒度/时间戳、交易场所、OHLCV、币种、调整口径、Source 与数据版本。
- financial_facts：issuer_id、指标及报表口径、数值、单位/币种、财务期间、公开披露时间、采集时间、修订记录和原始申报引用。
- corporate_actions：拆股、分红、并购或证券标识变动等，保存生效日期、可获知时间和原始来源。
- 研究时使用 as-of 语义：按研究时点只读取当时已公开的数据版本，不因后续财报修订或行情调整造成前视偏差；研究记录固定具体数据版本或引用。
- S3 保留结构化数据原始响应/文件快照，PostgreSQL 保存规范化数值、来源和校验标识。


### 6.5 事件归并与证据独立性

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


### 8.1 数据生命周期、可靠触发与证券 fan-out

**文档级任务和证券级任务必须使用不同的就绪条件。** Document 入库并不表示其证券身份已确认。

#### 数据生命周期与触发语义

| 阶段 | 提交内容 | 可供哪些 Task 消费 |
| --- | --- | --- |
| 1. 采集入库 | S3 原文与 PostgreSQL Document、采集幂等键、采集游标；实体状态为 `pending` | `on_new_documents`：不依赖证券身份的文档处理任务 |
| 2. 后台实体解析 | `entity_mentions`、`document_entities`、文档解析状态/修订号 | 解析结果提交后才能产生证券级触发 |
| 3. 就绪事件持久化 | 与第 2 步**同一 PostgreSQL 事务**写入 `on_entities_resolved` 事件，记录文档、修订、确认证券集合 | `on_entities_resolved`：需要 `security_id` 的 Task |
| 4. 创建 Run | Worker 消费已提交的事件，按确认证券创建独立、可重试 Task Run | 常规执行与结果入库 |

**最小持久化触发契约：**

~~~text
TaskEvent:
  event_id:              stable unique id
  kind:                  on_new_documents | on_entities_resolved
  document_ids:          list[document_id]
  resolution_revision:   int | null
  confirmed_security_ids: list[security_id]  # resolved event only
  created_at:            timestamp
  delivery_state:        pending | processing | completed
  retry_metadata:        attempts, lease_expires_at, last_error
~~~

TaskEvent 可以是 PostgreSQL 任务/事件表的一个类型，不需要部署消息中间件。事件与相应业务数据在同一事务中写入；Worker 领取并创建 Task Run 时采用唯一键和事务保护。Worker 崩溃或重启后，可以安全重新领取未完成事件，重复投递不能重复创建相同 Run。

- 第 1 步事务失败不推进游标、不创建任何新数据触发；重试先检查内容哈希及幂等键。
- 第 2 步 `failed` 时记录错误并重试，不发布 `on_entities_resolved`；`partial` 可以发布已确认证券集合，并保留待消歧部分。
- 即使本轮没有确认任何 Security，也保存解析完成/部分完成的事实；`confirmed_security_ids` 为空时不创建证券 Run。
- 重新解析只在已确认关联**实质改变**时发布新修订事件。新增证券产生新的研究范围；撤销或改正旧关联须在 Repository 中保留更正信息，并标记依赖旧关联的研究需要重估。与旧修订输入相同的重试不得产生重复触发。

#### Fan-out 是创建 Run，不是 Task 内循环

- Task 的 `scope` 可以是单证券、Watchlist 或事件携带的已确认 `security_id` 集合；显式 `for_each: security` 才按证券展开，其余 Task 可以对整批文档只运行一次。
- 将 scope 展开为去重、稳定顺序的内部 `security_id`，校验 `max_items` 上限；每个证券产生一个 Run，携带 `security_id`、`task_batch_id`、时间窗口和输入证据引用。
- Run 的幂等身份由 `task_id`、Task 定义版本、触发事件 ID（或调度窗口）、`security_id` 与研究窗口组成；数据库层建立唯一约束。**同一个事件重投不产生新 Run，确有实体关联修订的事件则允许新 Run。**
- 每个 Run 有独立状态、步骤日志与重试；Worker 使用 Task 的 `max_concurrency` 限制并发；某证券失败不撤销其他证券的成功结果。
- 需要汇总时，汇总 Task 读取指定批次/窗口的研究结果及缺失状态，不自动创建隐式依赖屏障。
- 系统只支持上述顺序步骤、结构化 `when` 条件、触发、fan-out 和有限重试；不引入循环 DSL、通用 DAG 引擎或分布式事件总线。

**示例：** 一份公告入库后先得到 `pending`，此时不会触发“按 NVDA 研究”；解析完成并确认 NVDA 才产生证券级 Run。若 Worker 恰好在事件提交后宕机，恢复时仍能领取同一事件，但唯一约束保证不会再建第二个 NVDA Run。若后来更正该公告实际关联另一只股票，新的解析修订保留纠正记录，并据此创建新证券的研究 Run。

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
