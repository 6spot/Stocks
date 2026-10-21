# 数据采集、分类与 Intelligence Repository

本篇定义可复用的情报数据资产、来源接入和检索契约。执行与研究逻辑见 [任务执行](execution.md)、[研究取材](research.md)；返回 [架构总览](architecture.md)。

**阶段边界：** V0 仅接入少量真实来源，手动获取并形成 Document/Chunk/Embedding；V1 增加定时采集、分类和按需 Fact；V2 扩展来源与补证。下文未标阶段的来源安全、原件保留和证据追溯规则从 V0 起适用。

## Source 与 Connector 设计

### 通用接入类型

- **RSS/Atom**：按已配置订阅频道持续发现全部新文章及明确更新；获取完整正文或使用 Feed 自带全文并入库，不再按标题、摘要、Ticker 或投资相关性逐篇过滤。仅保存订阅地址/摘要不等于取得文章全文。
- **HTTP API**：按照已配置 API、查询参数、时间范围和游标收集返回数据，保留原始 JSON/XML 响应并按需解析 Fact；不再按当前研究任务相关性逐条过滤响应内容。
- **Web / 外部搜索**：搜索结果是供 Agent/Task 选择的链接和摘要，并非必须全部获取；对研究实际决定打开的网页、公告或文件，取得可用原文后统一入库。不能以搜索摘要冒充全文。动态页面按需支持，不强制浏览器。
- RSS、财经媒体、API、外部搜索及 Agent Tool 只在发现/获取方式上不同；实际取得的资料统一走 Document 持久化、分类/标签、按需 Fact 及 Embedding/pgvector。不得新增全局 `should_ingest` 判断模型或分渠道情报表。来源可预先配置或按需登记，始终追溯真实发布者、原始 URL 和采集渠道。

**所有入口使用相同的内容输入契约、落库格式和后处理流程。** Connector 负责发现、获取及来源元数据适配，将原始内容原样包装为统一采集输入；正文解析、分块及 Source Mapper 的 Fact 字段映射在入库后的处理阶段执行。Repository 不根据采集渠道另设业务流程、RSS 专属字段或独立存储模型。来源、URL、发布时间、采集时间、内容版本与完整性均为通用信息；是否需要文本分类或直接解析 Fact，由内容形态决定。

### 统一采集输入

Connector 和授权 Tool 的获取结果均转换为以下逻辑输入，再交给同一个 Repository 入库入口。此处定义数据含义，不规定 HTTP 接口、Python 类或新增物理表。

| 输入 | 含义 |
| --- | --- |
| 来源与出处 | Source 引用、真实 Publisher、原始 URL；上游标识与规范 URL 可缺省 |
| 原始内容与类型 | 本次实际取得的内容及 MIME 类型；工具只返回文本时保存文本，不假称已取得 HTML 或原始 PDF |
| 时间 | 真实采集时间；来源明确提供的发布时间、更新时间分别记录，未知留空 |
| 可用元数据 | 标题、语言等能够确认的信息，可由后台解析补充，不猜测 |
| 完整性与获取错误 | 全文、摘要或部分内容的实际情况，以及后续获取或重试所需的错误信息 |

RSS 条目和搜索结果可用于发现链接；实际文章仍以取得的文章内容为证据。发现响应需要留存时也使用相同存储格式，不能将一整份 Feed 或搜索响应冒充其中每篇文章的原文。

### 统一来源收集与后处理契约

**Source 配置决定持续收集范围，Research Scope 决定研究检索范围。** 已配置的 RSS、新闻媒体、API 直接收集所选频道/参数范围内全部新内容及来源提示的实际更新，不在采集前对每篇信息作投资相关性判断，也不因与当前关注股票无关而丢弃。采集量由来源/频道/API 参数、请求配额和采集周期限制，而不是通过逐条模型筛选。

外部搜索与研究 Tool 是按需发现渠道：Agent/Task 可以根据研究问题从搜索结果中选择需要实际访问的链接，而不必把所有搜索结果都下载。**一旦取得本次实际使用的原文、文件或原始 API 响应，就按统一流程保存**；选择搜索链接不是全局入库审批。

~~~text
已配置来源：RSS / 财经媒体 / API
    → 按 Source 配置获取全部新增内容及明确更新
按需渠道：外部搜索 / 授权 Tool
    → 按研究问题选择实际打开的结果
两类入口统一
    → 取得完整正文 / PDF / 原始 API 响应
    → Document + 不可变原始快照、来源/时间/版本/完整性
    → 后台解析
        ├─ 可检索文本 → Chunk/原始位置 → Embedding/pgvector 索引
        ├─ 适用文本 → 分类、标签、should_extract → 按需 LLM Fact
        └─ 结构化字段 → Source Mapper 直接解析 Fact
    → Repository 检索 Document 与 Fact，研究时去重
~~~

上述分支表示数据依赖，不要求并行调度或 DAG；分类、抽取失败不阻塞已解析片段建索引，结构化资料不必经过文本分类。

获取入口负责取得可用原始内容：只取得链接或摘要时继续获取原文，已取得完整正文或原始响应时无须重复下载。这条规则适用于所有渠道。获取失败时保留已得到的内容与 URL，使用统一的完整性和错误状态支持重试，不能把摘要标成完整原文。

真正的技术校验（损坏响应、权限与 URL 安全、请求限制、重复采集幂等、更新版本判断）仍然执行；它们不属于“是否值得研究”的内容过滤。已成功入库资料不得因分类结果、should_extract=false 或 Embedding 失败而删除。

### 来源与发布者

系统分别记录：

- source_id：系统采用的采集来源。
- publisher / original_publisher：实际发布主体，必要时记录待确认。
- original_url / canonical_url：直接引用地址和可追溯原始出处。
- external_id：上游原始标识（如果有）。
- `published_at`：来源首次发布时间；`source_updated_at`：来源明确给出的最后更新时间（未知为 null）；`collected_at`：Stocks 实际采集该快照的时间。三者不能互相代替；指标观测期单独记录。
- trust_tier_snapshot：采集时的信任等级快照。
- content_hash：原始对象的完整性校验。
- storage_ref：远程对象存储中的原文/快照引用。

多个渠道转载同一原报道，仍可保存多个原始 Document，但证据核实不能将它们统计为独立证据。

### Source 配置范围与 Research Scope

**Source 配置**定义持续采集的来源、频道、API 查询参数、时间范围与请求配额；实际采集周期由关联采集 Task 的 `trigger` 定义，归属[执行设计](execution.md)。**Research Scope** 定义市场、公司/股票、行业、宏观及政策等研究主题与搜索补证条件。研究任务关注什么不反向限制持续来源的入库。

| 场景 | 采集/研究行为 |
| --- | --- |
| 已配置的财经 RSS / 新闻频道 | 频道内全部新文章及来源明确更新的版本均尝试获取全文并保存 |
| 已配置的结构化 API | 在 API 请求参数和周期范围内收集返回内容、保留原始响应、按需生成 Fact |
| Research Task（如 NVDA 或半导体） | 从已累积的 Document/Fact 中检索、排序、去重，必要时按需补证，不过滤原有来源 |
| 外部搜索 / Agent Tool | 按研究目的选择需要实际获取的搜索结果，取得后统一持久化 |

**已有文章按来源信号驱动更新**：已配置源的轮询发现新条目以及 RSS/Atom updated、API 修改时间/版本/变更记录等明确更新信号；已有文章只在来源发出更新信号或研究授权补证时复查，不定期扫历史链接。更新时间只说明值得检查，内容没有变化就不新建正文版本，真实变化才保存新旧快照。

来源配置有界，不意味着无边界抓取整个互联网；但配置范围内不增加逐篇财经相关性判断、人工审批或 `should_ingest` 模型。

#### 来源更新、采集状态与覆盖

**不建设独立 Source Monitor。** 设计使用 Source 配置、采集游标、采集 Task 的 Run 和文档后处理状态；Collection Run 指采集 Task 的运行记录，不是另一套运行系统。Overview/Runs 查询这些记录展示健康情况，具体实现状态见[运行说明](quickstart.md)。

- 采集 Task 的 `trigger` 决定实际轮询节奏，Source 可配置 `expected_freshness`，根据该来源实际披露节奏判断是否过期；二者**不相等**：每 15 分钟查询一次 RSS 不代表每 15 分钟必须有文章。Source 不再重复定义 `schedule`，月度经济指标不能按日更要求判故障；不了解发布节奏时不设置硬性过期告警。
- Collection Run 记录开始/结束时间、状态、获取数、入库数、重复数、失败数及错误原因，支持查最近成功时间和来源覆盖。不再要求持续源记录“相关性过滤数”；请求成功却无更新是正常空结果，不是故障。
- 三类状态分别呈现：**采集是否成功**、**来源是否按已知节奏更新**、**已入库资料的解析/分类/Fact/索引处理状态**。来源长期不发布、Connector 故障与处理积压不能混为一谈；缺失数据在研究覆盖信息中说明。
- 游标只在需要的原始资料及采集状态成功持久化后推进；失败 Run 保留错误供 Worker 重试。采集成功代表此次配置来源的请求成功，不代表互联网上所有相关资讯都已覆盖。

不引入新告警调度服务、健康评分模型、专门心跳表或完整的外部数据发布日历。V0 先有可查询的原件、处理错误与已知缺口；V1 才按真实来源的披露规律配置针对性过期判断。

## Intelligence Repository 与存储

**Repository First 的数据边界：** 新闻、公告、政策法规中披露的 CPI、利率、就业、企业财务和市场数据，与 API 获取的数值同样是可复用资料。V0 先保存 Document 原件、不可变版本和可检索 Chunk；报告可直接引用原文片段。V1 增加 Fact：Decision Model 为适用文本标注分类、标签和抽取决策，需要结构化使用时由 LLM 从原文抽取 Fact，或由 Source Mapper 从 API 响应解析 Fact。研究优先查询已存数据，V2 才引入授权外部补证。此处分层不要求新服务或为每种金融指标建专用表。

### 存储分工

- PostgreSQL：Source、采集状态/游标、Document 元数据与分类 JSONB、可检索 Chunk 片段及向量、Fact、Task/Run、报告与证据引用；不在 Document 上另存整篇清洗正文。公司名称、股票代码、行业及主题只是可检索标签；系统不维护证券身份主数据表。
- pgvector：**V0 必须实现**的向量语义检索。适用 Document 正文/片段生成 Embedding 并入 PostgreSQL pgvector，配合来源、时间、可用标签等过滤；V1 再合并 Fact 结构化查询。代码、指标编号、日期等精确条件另走有界精确查询，不能依赖向量相似度；**不实现 PostgreSQL 全文检索（tsvector/GIN 全文索引或独立全文搜索引擎）**。
- 远程 S3 兼容存储：按 Document 内容版本保存实际取得的 HTML/XML/JSON、PDF、文本等原始内容；相同内容重复取得时复用已有对象。数据库保存原件的 Object Key、内容哈希、尺寸、类型等元数据，不复制原始文件，也不另存一份完整的清洗文件。
- S3 通过标准 API 访问。可利用 Tailscale 私有网络连接独立存储服务器，但应用不绑定某个私网产品或存储厂商。

目标写入流程为：**按照已配置来源收集（或按需搜索选取）→ 获取完整正文/原始响应 → S3 保存不可变快照 → 持久化 Document 与幂等键 → 解析与分块 → Embedding/pgvector 索引 → V1 分类/标签、按需 Fact**。V1 定时采集另保存采集游标。需要存储的原始资料未可靠保存前不能推进游标；解析、分类、Fact、Embedding 失败不删除 Document。仅有摘要而全文暂时不可得时保存已获信息，记录缺失和重试原因，不冒充全文。同来源相同原始字节幂等；原始字节变化保留新旧版本，仅更新时间改变不产生新内容版本。

**Document 版本身份（V0 起）：** `(source_id, dedupe_key)` 标识同一来源中的同一资料；每个内容版本有独立 `document_id` 和递增版本号。取得的原始字节相同则复用当前版本；字节变化时保存新快照和新版本，即使解析后的正文最终没有实质变化也保留原件差异。上游仅更新 `source_updated_at` 而原始字节不变时，不创建新内容版本。Chunk、后续 Fact 和报告引用具体 `document_id`/版本；旧版本及被引用片段不原位覆盖。研究以解析内容判断修订是否具有实际意义，不把网页模板或广告变化自动当作重要事件。

当前后端提供 `stocks.repository.ingest_document()` 作为这条入库链的最小实现。调用方提交 `DocumentInput`，Repository 校验 Source、内容类型、完整性状态及获取错误，按 `(source_id, dedupe_key)` 查找现有版本；新哈希先保存不可变 S3 原件，再提交下一版本号。同一内容版本不原位改写：每次取得结果分别记录在该 Document 的 `completeness`/`fetch_error`，并在 `document_collection_states` 保存资料身份最近一次尝试所对应的 Document、完整性、错误、来源时间和采集时间，用于展示最近采集状态及决定是否重试；相同原始字节仍复用内容版本，只更新这份采集状态。数据库提交失败时回滚 Document 事务，已写入但未被登记的对象不视为可用证据。`get_document()`、`get_latest_document()`、`get_collection_state()`、`list_document_versions()` 与 `read_document_bytes()` 提供按版本读取及当前采集状态。`get_latest_document()` 返回**最近一次实际取得内容**对应的 Document，即采集状态指针指向的版本；版本号只记录创建顺序，因此 “正文 A → 获取失败的部分内容 → 恢复正文 A” 会复用 A 的不可变版本，此时最近内容仍是 A 而不是版本号最大的版本。采集跳过判断与最近内容读取使用同一指针，历史版本的正文、标题与时间不回写。

V0 已提供 `config/sources.yaml` 配置的 RSS/Atom 手动采集入口。`stocks.collection.collect_source()` 先发现 Feed 条目，只对没有本地版本的条目、已有内容为 `partial` 的条目，或来源 `updated` / `pubDate` 信号发生变化的条目请求文章页；完整 HTML/文本字节连同响应声明的媒体类型与 `charset` 参数一起交给 `ingest_document()`；类型白名单只比较媒体类型本身，保存的内容类型保留编码声明，供后续解析严格解码。文章内容链接在通过 HTTPS、端口、userinfo 与地址校验前若无法解析，作为该条目的 `INVALID_ARTICLE_URL` 失败记录，同批其余条目继续处理。文章请求失败或正文为空时，以实际取得的 Feed 标题和摘要生成 `text/plain` 部分内容，记录 `completeness = partial` 与脱敏 `fetch_error`，并在后续运行重试：部分内容本身不是正文证据，即使来源没有提供任何时间信号也必须再次尝试全文，未知时间保持未知。相同原始字节复用内容版本；重试得到正文时因字节不同形成新版本。Embedding 索引见后文。V1-01 起采集可由 `collection` 类型 Task 按自身 `trigger` 定时触发：采集 Run 复用同一 `collect_source` 与本文描述的幂等/完整性规则，Run 记录位于 `task_runs`，不另建 Collection Run 表。采集的是“本次是否已取得该条目的当前内容”，判断依据是 `document_collection_states` 与来源时间信号，而不是按 Feed 条目保存的 per-Source Feed 游标；这样的 Feed 游标尚无任务实现，V1-05 交付的是下节所述的研究侧提交可见变化水位与消费游标，两者不是同一个概念。分类（V1-03）、通用 Fact 与 `http_api` Source Mapper（V1-04）及 Document/Fact/报告/证据阅读 API 已在当前代码实现。

### 后台解析、片段与索引

**原始内容保留一份，解析在入库后的后台处理阶段完成，检索读取已保存的片段和索引。** Chunk 文本、向量和必要 Fact 是支持检索与研究的派生数据，与原文存在必要的内容重叠；不再叠加一份完整清洗正文。解析设计为 Worker 中的普通代码步骤，不建设独立清洗服务或清洗 Agent。

| 内容类型 | 后台处理 |
| --- | --- |
| HTML | 从实际取得的页面提取正文、列表、表格及必要上下文，排除导航、广告、脚本等页面噪声；原始 HTML 保持不变，不要求下载整站配套资源 |
| 文本 / Markdown | 按原有段落和结构分块，做必要的编码、换行整理 |
| JSON / XML | 按字段和记录结构解析，直接映射适用 Fact；有可检索文本时生成片段，不强制将所有数值改写成文章 |
| PDF | 提取可用文字、表格并保留页码；无法可靠解析时记录缺口，不以模型猜测代替原内容 |

必要解析只处理格式与内容定位，保留数值、单位、表格对应关系、脚注和限定条件，不总结、改写或补充事实。按内容类型选择解析器，采集渠道不改变后续契约。

Chunk 至少关联 Document 及其内容版本，保存片段文本、顺序和原文定位信息；定位可为 HTML 节点/段落、PDF 页码或 JSON 字段路径。分块长度和必要重叠由配置控制，不按来源各建一套业务模型。分类读取预算内选取的已解析片段；文本 Fact 抽取按需读取相关片段，较长资料可分批抽取，无须持久化一份拼接全文。分类或抽取没有覆盖的部分保留在原文和 Chunk 中，不能据此宣称整篇不存在相应事实。

解析、分类、Fact 和向量索引分别记录处理状态、最近错误及所用处理器/模型版本，沿用 Document 或处理记录。不适用的阶段明确标为跳过，不作为失败或积压；例如纯数值响应可经字段映射进入 Fact 查询，不必生成文本片段或向量。原件保存成功不等于可语义检索；解析成功后，分类与向量索引可以独立完成，分类失败不阻塞已解析片段建索引。解析失败则保留原件供有限重试或控制台手动重试，不能把失败写成空正文或没有相关资料。

已成功处理的同一内容版本直接复用。原文更新或 Owner 明确重新处理时，才重新解析或索引；更换 Embedding 模型只需读取已有片段重新生成向量，无须重新下载、重复抽取 Fact。重新处理不能使历史报告的实际证据失效，已被报告引用的片段不得原位改写或删除，报告仍使用其已保存的输入片段与版本。上述操作沿用现有任务与状态，不增加处理历史平台。

**统一处理状态（V1-02）：** `document_processing_states` 以 `(document_id, stage)` 为主键，为 parse、classify、facts、index 四个阶段各保存一条当前记录，只负责排队、尝试次数与自动重试预算、下次可尝试时间、所有权 token、最近开始/完成时间、安全错误、等待/跳过原因、处理器版本与配置指纹、输入批次引用及适用性。Document 新内容版本在入库的同一事务内创建这四条待办；相同字节复用不重开已完成阶段。阶段状态只有 `pending | running | completed | failed | skipped`：`completed`/`failed` 由刚持久化的派生结果（解析批次状态、向量覆盖）推导，不接受调用方声明的成功；`pending`/`running` 只表示排队、等待或所有权；`skipped` 只由处理器显式适用性及其依赖传播产生。

- **完成不等于可重开。** `completed` 默认保持完成，不因最新 Embedding/分类模型配置不同而自动重开；只在领取待处理阶段时记录该次使用的配置身份，不保存密钥或 endpoint 明文。
- **能力缺失不等于跳过。** 没有注册处理器的阶段保持 `pending` 且带明确等待原因（`PROCESSOR_NOT_AVAILABLE`），不消耗尝试次数、不轮询空转，也不写成 `completed`/`skipped`。当前部署注册 parse 与 index；classify 与 facts 等待各自实现接入。
- **跳过需要显式依据。** `skipped` 必须带原因，且只能来自处理器显式产出的适用性信息（例如结构化数值资料 `requires_embedding=false`），不能由 MIME、空 Chunk 或缺少配置推断。当前尚未支持 JSON/XML/PDF，因此不会把它们自动标成“数值无需向量”。依赖一个已跳过阶段的下游阶段记录 `DEPENDENCY_SKIPPED`：它自己的适用性从未判定，只是当前无法执行，因此不会被当成永久结论；依赖阶段后来完成时重新回到 `pending`。跳过不消耗尝试次数，也不撤销已完成的可用结果。
- **失败有限重试。** `failed` 按指数退避与 `max_attempts` 自动重试；预算耗尽后停止自动领取，只能由 Owner 重试。解析失败、分类失败、抽取失败与索引失败互不阻塞：分类失败不影响已解析片段建索引，索引失败不影响分类/Fact 待办。
- **中断不自动接管。** 运行中的阶段不检测死进程；Owner 确认旧进程停止后才能释放。已提交的派生结果（解析批次、已写入的向量批次）保留并被识别为真实进度，恢复只继续缺失部分，不会把“部分完成”写成成功；旧所有者的写入被 token 校验拒绝，包括分批写入的中间向量批次。所有权与当前输入都以**数据库中的最新行**为准，执行上下文中缓存的阶段或解析指针不构成授权。
- **新当前批次使旧输入的运行中阶段作废。** 解析成功切换当前批次时，仍在运行且输入批次不是新批次的 index/classify 阶段在同一事务内释放所有权回到 `pending`，并指向新批次；旧执行者的写入与阶段结果被 token 校验拒绝，也不会把该次作废记为新输入的一次失败尝试（不消耗自动重试预算）。因此新批次不会被旧执行者标记为“已完成索引”而长期无人处理，下一次正常领取即为新批次生成向量。
- **重处理边界。** 显式重索引只读取已有 Chunk 重新生成向量，不重新解析、不重新抽取 Fact；显式重分类只重跑分类，不重开已完成 Fact。Fact 重处理需要明确请求或新 Document 内容版本。解析产生新当前批次会重开 index 与 classify，但已完成的 facts 保持完成。显式重处理请求只在真正产出结果的尝试成功后清除：失败的尝试或中断后的恢复保留该请求，避免后续普通重试把原有可用结果的进度误当作本次重建已完成。

V0-03 的后台解析入口是 `stocks.processing`；V1-02 起由 Worker 文档槽位或同一阶段服务的 CLI 触发。它按 `text/html`、`application/xhtml+xml`、`text/plain` 选择解析器；HTML 优先读取语义正文容器并排除脚本、导航、表单等噪声，保留标题、段落、列表和表格行文本，表格按行与单元格关联；纯文本按段落保留原位置。每个成功 Chunk 保存文本与 `locator`：纯文本在元素级 `char_start` / `char_end` 保存严格解码文本的字符范围，`segments[].locator.source_ranges` 保存切片实际可见文本的原文范围，普通连续文本可合并范围；HTML 元素级范围为原始解码 HTML 字符范围，实体字符的来源范围覆盖完整实体写法，表格行还保存单元格原文边界。表格拼接的空格和 `|` 以及 HTML 语义分块之间的 `\n` 由系统生成，表格分隔符在 `locator.separators` 显式标记，不能用元素级范围冒充原文。HTML 源码按纯文本安全解码显示，不执行原始标记。分块大小/重叠由 `PARSE_CHUNK_SIZE` / `PARSE_CHUNK_OVERLAP` 配置，不按来源建立独立字段。

解析结果采用不可变批次。`document_parse_batches` 记录每次处理的内容哈希、处理器版本、配置指纹、`completed | failed | unsupported` 状态、错误与 Chunk 数；`document_parse_states` 分别指向最近尝试批次和当前成功批次。相同 Document、处理器版本、配置指纹的普通运行复用已有结果；Owner 显式重处理创建新批次。重处理失败只更新最近尝试状态，不改变当前成功批次及其 Chunk；重处理成功切换当前批次，同时保留旧批次和 Chunk 供历史引用。无效编码、无可见正文等记为失败，不支持 MIME 记为 `unsupported`；都不能伪造空正文成功，原件不变。当前还没有 PDF/JSON/XML、OCR 或前端读取入口。

**V0 Embedding 索引：** `stocks.indexing` 从每个 Document 当前成功解析批次的 Chunk 生成向量，并写入 `document_chunk_embeddings`。每条向量以 `(chunk_id, model_name, dimensions)` 唯一标识，记录实际 Provider 别名、固定模型名与维度；`document_embedding_states` 记录每 Document 最近一次索引尝试的状态、当前解析批次、覆盖数量和稳定错误。当前向量维度固定为 1536，模型及同模型回退由 `config/models.yaml` 的 `embedding.models` 配置；回退目标必须与主目标使用相同模型名和维度。普通重复索引复用已完成状态；模型切换、重新解析或显式重建会为新批次生成向量。旧批次的向量仍保留供历史引用，但不进入当前检索。Embedding 失败不删除 Document、Chunk 或已有向量，并显示 `failed`、覆盖缺口和错误；成功完成必须覆盖当前批次全部 Chunk。V0 只支持一个当前向量空间，不混用不同模型或维度的距离结果。

**V1-02 后台执行：** 解析与索引由 Worker 的独立文档处理槽位按统一阶段待办执行，`WORKER_MAX_CONCURRENT_DOCUMENTS`（默认 1，1–16）控制同时处理的阶段数，与 Run 槽位互相独立，因此积压不会饿死采集/研究，长 Run 也不会阻止已入库资料被处理。文档处理的 Embedding 路由只在真正执行一个可领取的 index 阶段时读取；`config/models.yaml` 的 Embedding 配置缺失或损坏不会阻止解析，也不会阻止采集 Run，只让该次 index 尝试记录失败且不自动重试。研究 Run 的 Evidence Selection 仍按既有规则读取并核对自身的 Embedding 路由快照，与此互不影响。`stocks.processing status|retry|reprocess` 提供有界的积压查询与恢复入口，`retry` 只重开失败/未完成阶段，`reprocess` 可显式选择已完成阶段，运行中阶段必须 `--confirm-stopped`。手动 `stocks.processing parse` 与 `stocks.indexing` 是同一阶段服务的调用方，不保留绕过所有权的平行执行路径。

**V1-03 分类阶段：** `classify` 与 `index` 一样在真正领取时才读取自己的路由与词表：Decision Model 路由（`config/models.yaml` 的 `agents.classification`）或词表（`config/taxonomy.yaml`）缺失、损坏、协议不支持时只让该次分类尝试以 `DECISION_ROUTING_UNAVAILABLE` / `TAXONOMY_UNAVAILABLE` 失败且不自动重试，不影响解析、索引、采集或研究。分类读取的是当前成功解析批次的片段，新当前批次会把仍在运行的旧分类尝试作废并重新排队（与 index 相同的输入批次规则），旧执行者写入被 token fence 拒绝。

### 核心数据与公司/股票标签

Stocks 围绕长期积累和研究分析保存资料；公司名称、股票代码只是检索字段，不为公司、证券、代码历史或逐条提及记录建立独立主数据表。

| 数据 | 主要职责 |
| --- | --- |
| `sources`、采集游标及运行记录 | 来源配置、增量采集、失败和覆盖情况 |
| `documents` | 原始文章/API 响应的来源、时间、内容版本、S3 原件引用；当前 `classification` JSONB、解析/分类/抽取/索引状态及公司/股票等检索标签 |
| `facts`（V1） | LLM 从原文抽取或 API 直接解析的事实、数值、时间口径和证据位置，每条关联来源 Document 版本 |
| Document Chunk / Embedding（V0 必须） | 入库后后台解析得到的片段、原始位置与相应向量，供 pgvector 召回、研究和证据阅读；当前已有不可变批次与文本/HTML 定位 Chunk，向量由 V0-04 生成，Embedding 失败不丢片段或 Document，索引状态可恢复 |
| Task/Run、Analysis/Report 记录 | V0 手动执行、实际取材快照、分析与报告、来源引用；V1 增加定时运行与增量进度 |

**公司与股票的最小规则：**

- Decision Model 可以在分类结果中给出 `companies[]`（原文明确提及的公司名称）、`symbols[]`（能依据原文和上下文明确指代的股票代码），以及 `related_industries[]`、`topics[]`（主题/间接相关性）。全部随当前分类存于 Document，不额外建立实体解析 Worker、历史代码有效期表或关联表。
- 关注列表先由 YAML 配置公司名称、代码及必要时市场/交易所信息。市场、行业、单股 Task 按配置的名称、代码、标签、原文片段和时间范围查询；不必给股票预分配内部身份 ID。
- 不能确定股票代码代表哪家公司时，保留原文及能确认的公司名称；不猜测代码，也不阻塞资料入库或研究。**直接提及与推测可能受影响必须区分**：某政策与半导体行业相关，不等于原文提及 NVDA；该文仍可以行业/宏观背景身份参与 NVDA 研究。
- 同一来源内容未变保持幂等；正文内容变化形成新 Document 快照版本，即使 URL 相同也保留旧版。来源更新时间只是元数据，不能单凭它判断版本。重新分类只更新当前分类，不新建 Document 或分类历史。
- 不以标签决定已入库原文是否保留。分类不确定、缺少股票代码或无 Fact 时仍能依靠 pgvector 语义检索与来源/时间过滤被发现；Embedding 尚未完成或失败时保留资料及可查询的处理状态，不把索引失败解释成信息不存在。所有研究均遵循 Repository First，并在统一取材环节与 Fact 合并去重。

**长期产品边界：不建设证券主数据系统、历史股票代码身份映射或按证券自动 Fan-out 的任务系统。** 这不是 V0 暂缓事项，也不列入后续增强。代码歧义只需保留原始内容与不确定性；研究可依据上下文和证据作判断，不建立证券身份数据库。

### 自动分类、标签与关联边界

V1 起的分类是直接保存在 Document 版本中的轻量情报属性，不是逐篇投资分析；也不要求文章必须提到具体股票。宏观、行业、政策资料即使没有公司名称或股票代码，仍可完成分类、Embedding、pgvector 语义检索，并被定时研究任务读取。

**统一分类结果由 Stocks 定义，与模型厂商无关：**

| 字段 | 语义与约束 |
| --- | --- |
| `document_id` | 分类结果属于当前 Document；不为不同模型的分类单独新增 Document 或分类历史行 |
| `category` | 单个主要类别，来自受控词表，例如 `macro`、`policy`、`industry`、`company`、`other` |
| `event_type` | 可选的事件类型，如 `monetary_policy`、`earnings`；不适用时为空 |
| `topics[]` | 可多选的主题标签，例如 `interest_rates`、`inflation`、`liquidity` |
| `companies[]`、`symbols[]` | 原文有依据地明确提及的公司名称、可确认的股票代码；不强制填写、不猜测歧义代码 |
| `related_industries[]` | 文章讨论或可能影响的行业分类标识，属于**主题相关性**，不表示直接提及某只股票 |
| `regions[]` | 所涉及的地域或市场；不明确时为空，不根据语言猜测 |
| `method`、`model_ref`、`taxonomy_version` | 当前分类方式、模型/规则和词表引用；显式重新分类只回写当前分类 JSONB，不改变稳定标签 ID 与 `should_extract` 语义 |
| `classified_at`、`reason_or_evidence_ref` | 分类发生时间及可追溯依据；不能把模型分类标记成已经核实的事实 |
| `should_extract` | 同一次低成本分类请求给出的布尔抽取决策；表示是否需要后续 LLM 结构化抽取，**不是保留/删除原文的开关** |

标签及类别使用可配置的受控词表，前端与 Task 使用稳定标签 ID 查询。**V1 将当前分类、标签、`should_extract`、模型信息及分类状态保存在相应 Document 版本的 JSONB/必要状态字段中，不新建 `document_classifications` 或分类历史表。** 更换模型配置只影响后续待处理资料；Owner 可显式对已完成资料重新分类并回写当前结果，不自动重跑分类或已成功的 Fact 抽取。报告需要复查时由 Task Run 保存实际采用的输入快照，不为分类历史设计多套有效记录。结构化 API 响应不必强制走文本分类模型，直接由 Source Mapper 解析可用数据。

**V1-03 当前实现：** 词表由 `config/taxonomy.yaml` 维护（`version` 加各节的稳定 `id`），Stocks 从它派生发给模型的 JSON Schema，因此词表外的类别、主题、行业或地域是契约违规而不是额外标签。`documents.classification` 保存当前结果，包含上述业务字段以及 `method`、`model_ref`、`model_alias`、`reported_model_name`、`taxonomy_version`、`taxonomy_fingerprint`、`classified_at`、`usage`，并在 `reason_or_evidence_ref` 中记录模型给出的理由、所用解析批次、`chunk_ids`、输入字符数与是否截断。分类状态沿用统一的 `document_processing_states` 中 `classify` 行，不另建 `classification_status` 列或分类历史表。分类输入是当前成功解析批次按 `CLASSIFICATION_MAX_CHARACTERS`（默认 6000，200–40000）截取的片段，不把任意长度全文交给模型。

Decision Model 通过 `config/models.yaml` 的 `agents.classification` 配置，`protocol` 目前只实现 `openai_compatible`（其余协议在读取配置时即被拒绝，不假装兼容）；`primary`/`fallbacks` 只是模型别名，`retries` 与回退属于同一次业务分类，不构成第二次分类流程。`should_extract` 作为显式适用性写入 `classify` 阶段：`false` 让 `facts` 以 `NOT_APPLICABLE` 跳过，之后若显式重分类得到 `true`，被跳过的 `facts` 会重新回到 `pending`，而已完成的 Fact 不会被重开。分类失败（词表外结果、无效结构、超时、凭据或路由缺失）记录安全错误码并进入既有有限重试，不会写成 `should_extract=false`，也不改写上一次成功保存的分类结果、原件或已建索引。

**自动分类流程与失败边界：**

1. Document 原文可靠入库并完成必要解析后，Worker 先使用已知元数据/规则，必要时在正常成功路径中**一次调用配置的低成本 Decision Model** 完成类别、多个标签和 `should_extract` 判断；输入遵循上述片段与预算约束，不要求一次塞入任意长度的全文。模型可以是 Jev、Cloudflare Clef/Clef-flash 或其他符合所需决策能力的模型，**不绑定某个厂商、模型或部署平台**。它选择预定义结果，不在这一阶段生成事实正文、数字或结论；通过[Agent 与模型配置](execution.md)的 Provider/Adapter 接入，失败重试与回退不计作另一次业务分类流程。
2. 分类与抽取决策必须符合固定 Schema 和受控词表；超出选项、模型故障或无效结构时按有限重试处理，保留原文及待处理/失败状态，不能默认为 `should_extract=false`。分类完成后，不论标签是否为空、类别是否为 `other`，文档仍在 Repository 中可查询。
3. `classification_status = pending / completed / failed` 及事实抽取状态直接记录在 Document 或相应处理任务中。重新分类回写当前结果，不另建历史表；实质变化可以进入增量研究。
4. 分类结果、状态和完成时间作为当前 Document 的处理信息可靠写入。Worker 只处理已保存的资料，按原有任务/重试机制处理失败与积压；分类变化的提交顺序可供 V1 研究增量窗口查询。不为分类完成创建独立的研究触发事件。
5. `should_extract=true` 才按需调用生成式 LLM 抽取 Fact；`false` 不触发抽取，但原文及标签始终可被研究检索。抽取结果作为独立 Fact 关联到来源 Document 和证据位置，失败可重试且不覆写原始资料。结构化 API 响应由程序直接解析 Fact，不强制进行 Decision Model 判断。
6. 模型应区分原文明确提及的 `companies[]`、`symbols[]` 与可能受影响的 `related_industries[]`、`topics[]`。后者仅供研究检索，不得自动生成某只股票被原文直接提及的标签；不能消除的名称/代码歧义留空或保留原始文字，不制造确定性关联。

**示例：**

~~~yaml
document_classification:
  category: macro
  event_type: monetary_policy
  topics: [interest_rates, liquidity]
  related_industries: [banking, real_estate]
  regions: [US]
  companies: []
  symbols: []
  should_extract: true
~~~

`should_extract` 只决定是否进入 LLM 事实抽取，不决定原文保留或研究资格。示例没有直接提及公司/股票，但可供宏观或行业研究使用；行业可能受影响是研究候选，不是确定的股票涨跌判断。

#### 市场情绪的数据含义

市场情绪是综合研究的一个输入维度，而不是一条新闻的“乐观/悲观”标签或模型自报的全市场分数。

- **可观察指标**：波动率、成交量、资金流及公开调查等可获得的数据可作为 Fact 保存数值、单位、观察时点和来源。指标解释交给研究，不将某个指标直接当作“市场情绪真值”。
- **信息叙事/观点**：媒体、研究机构、企业及政策发布的信息可按主题和可选的情绪倾向归类，保留原始出处与发布时间。模型给出的倾向属于来源文本的分类结果，不能自动代表整体投资者观点。
- **阶段性研判**：报告可以综合指标变化和多来源信息，得出带有证据、时间范围及不确定性的市场氛围判断；数据样本偏差、转载和观点分歧需要明确说明，不将新闻条数简单当作市场情绪统计。

可配置的分类模型只负责必要标签及相关性判断；“市场情绪如何变化、对哪些研究范围有意义”由定期研究任务在多源上下文中形成，而非每篇文档单独生成研究结论。

### Fact：结构化事实与数值（V1）

`facts` 保存值得重复查询和比较的内容：LLM 从公告/文章中按需抽取的事实，以及从 API 原始响应直接解析的数值。**所有 Fact 都引用来源 `document_id`**，证据可定位到文本段落、PDF 页码或 JSON 字段路径；API 一次返回多个指标时，可以用一份原始响应 Document 对应多条 Fact。不产生结构化信息的 Document 依旧可检索。Fact 不是替代原文的“真值表”，模型抽取的内容先视为带来源的陈述。

**V1-04 当前实现：** 单一 `facts` 表同时服务两种来源，`origin_method` 区分 `llm_extraction` 与 `source_mapping`，类型相关细节放在 `attributes` JSONB，不按指标建专表。`evidence_ref` 至少记录定位种类与身份：文本事实保存 `chunk_id`/`parse_batch_id`/`position`，映射事实保存 RFC 6901 `pointer` 与来源内容哈希/版本。幂等身份是 `(document_id, fact_key)` 唯一约束，`fact_key` 由来源方法、事实类型、证据位置、指标与主体推导，因此同一内容版本的同一位置只会存一条；重试或显式重处理不会倍增相同事实，而修订值属于新的 Document 版本、也就是新的 `document_id`，旧 Fact 不被覆盖。数值 Fact 必须带明确单位（`value IS NULL OR unit IS NOT NULL`），未知单位不猜测：抽取结果若只有数字没有单位，保留文字陈述并丢弃数字。`period_start/end`、`available_at` 与入库时间相互独立。读取入口为只读 `/api/facts`（按来源、Document、类型、代码、指标过滤）、`/api/documents/{id}/facts` 与 `stocks.processing facts --source-id|--document-id-report`，都不触发抽取。

**结构化 API 与 Source Mapper（V1-04）：** `config/sources.yaml` 的 `connector: http_api` 来源声明 `api_url`、可选 `query` 与 `mapper`；它不配置 `feed_url`，RSS 来源也不配置 `mapper`，两者不能混写。采集只做一次有界请求，把原始响应字节连同声明的内容类型存成 Document 版本，`dedupe_key` 为来源标识，相同字节复用版本、变化字节产生下一版本；解析响应、映射 Fact 留给 `facts` 阶段，因此纯数值响应不需要文本分类或向量。`mapper.fields` 的每项声明 `kind`（`numeric`/`text`）、`pointer` 与 `fact_type`；`numeric` 必须配置 `unit`（无法确定单位即拒绝配置），可选 `metric_id` 与 `subject/symbol/period/available_at` 指针。映射是确定性程序：字段缺失或为 null 时该字段不产生 Fact（全部缺失报 `MAPPING_NO_FIELDS_MATCHED`），数值路径上的非数值报 `MAPPING_VALUE_NOT_NUMERIC`，时间字段接受完整 ISO 8601 或 `YYYY-MM`/`YYYY` 期间并按 UTC 期间起点锚定。

`facts` 阶段按内容来自行选择路径：结构化响应用配置的 Source Mapper 直接映射（无模型调用），普通文本只在当前分类 `should_extract=true` 时调用 Generation Model，`should_extract=false` 则显式以 `NOT_APPLICABLE` 跳过而未决定的判定会让阶段以 `CLASSIFICATION_PENDING` 等待（不消耗尝试次数）。生成模型路由与 Source Mapper 都在真正领取该阶段时才读取，缺失或损坏只让该次尝试失败或等待，不影响解析、分类、索引与采集。抽取结果若引用不在当前解析批次中的 Chunk 会被拒绝（`EXTRACTION_CITATION_INVALID`），伪造引用不能进入可检索证据。

| 字段 | 最小语义 |
| --- | --- |
| `id`、`document_id`、`evidence_ref` | Fact 标识、来源原文/响应及准确证据位置 |
| `fact_type`、`subject_name?`、`symbol?`、`metric_id?` | 事实类型、明确提及的公司名称或代码、指标名称/标识（能确定时填写），不要求公司主数据外键 |
| `statement?`、`value?`、`unit?` | 文字事实或数值及单位；不存在则留空，不能虚构 |
| `period_start/end?`、`available_at?` | 指标所属时间/期间以及来源公开可获知时间，不等于入库时间 |
| `attributes`（JSONB） | 不同事实类型所需的可选扩展属性，例如政策生效日期、财务口径、OHLCV 数值 |
| `origin_method` | `llm_extraction` 或 `source_mapping`，区分模型提取与直接字段解析 |

通用字段用于按类型、主体、时间和指标检索；扩展属性不要求每种事实固定列或专属表。关系可简化为 `documents (1) → (0..N) facts`。原文更新产生新的 Document 版本，来自新版的数据以新的来源引用保存；源数据修订不覆盖旧研究实际使用的数值引用。

**同一 Document 内容版本已成功完成 Fact 抽取或解析后，正常采集与研究直接复用结果，默认跳过重复处理。** 未完成或失败的处理可重试，Owner 可显式重新处理；原文实际变化时处理新的内容版本。切换模型或再次研究同一文章不会自动触发重复抽取。重试仍按来源版本及事实标识保持幂等，不为正常流程预建抽取纠错版本体系；已保存报告实际使用的输入与证据保持可复查。

#### 数值与时间语义

行情、财报、CPI、利率、就业等均可从原始公告抽取或 API 响应解析为 Fact。系统不提供实时行情读取、金融时间序列或证券行情服务，也不为这些来源设置专属金融主数据表。实际取得的指标仍保留统计周期、单位、披露时间及来源说明，供研究判断是否可比。例如上月 CPI 本月才发布：观测期属于上月，发布和采集时间分别记录；修订数值保留来源与版本。

研究使用 Repository 已存资料，并允许通过授权工具补充运行中取得的资料；研究参考时间 `as_of` 与增量窗口的含义以[执行设计](execution.md#研究增量窗口与共享资料引用)为准。未知字段保持未知，补取资料保留真实发布时间、采集时间及内容版本。报告引用具体 Fact 及其来源 Document/版本；同一 Fact 不因为查询路径不同而被当作两份独立证据。

#### 研究所需的数值准确性

这些是事实整理与研究引用的基本约束，**不是额外的金融数据质量平台、证券身份系统或实时行情计算服务**：

- API 响应和公告中的原始数值先完整保存；明确能解析的数值才作为 Fact 保存，未知单位或口径不猜测。
- 比较或引用数值时，要注意统计期间、单位/币种和必要口径。不同来源的冲突保留原值及证据，不用平均数掩盖。
- 研究需要同比、估值等计算时，使用确定性程序与可追溯输入，不让 LLM 凭空心算；没有必要预建所有金融公式或行情修订体系。
- 历史报告保存当时实际引用的数据和原文版本，之后资料更新不能静默改写旧报告。

### 来源关联、去重与政策资料

**首版不建设 Event 知识图谱、`event_documents`、`document_relations` 或政策生命周期状态机。** 这些关系不是情报积累和定时分析的前置条件。

- 同一来源、同一内容重复抓取保持幂等；不同渠道转载的原始 Document 可以保留，但研究取材时只把确认同源的资料视为同一组证据，并保存各自引用。
- 仅标题、关键词或语义相似不表示同一来源；独立观点、数值冲突及不同版本不得强行合并。
- 政策草案、正式公告、修订或生效说明各自保存原文和时间，研究凭具体来源文本判断其含义与适用状态，不将草案等同正式生效政策。
- 来源去重和证据独立性在统一的 [Evidence Selection](research.md) 中处理，不需要另建事件关系库或核验服务。

### 检索

V0 的 Repository 查询 Document Chunk：使用 **pgvector 语义召回**，结合来源、时间等元数据过滤，并对股票代码、指标编号等短字符串提供有界精确查询。不能把分类标签作为唯一的精确查询入口，因为 V0 尚无自动分类，V1 也可能分类失败。V1 增加 Fact 按主体、类型、指标、期间和数值口径的结构化查询，与 Document 候选统一形成 Evidence Package 并去重。**不另建全文检索系统**。

**检索路径：** 对本次查询文本生成查询向量 → 在相应 pgvector 索引中结合元数据过滤召回 Chunk → 返回片段及 Document 元数据；精确条件走有界字段或 Chunk 文本查询。V1 将结构化 Fact 候选合并取材。查询向量生成只处理本次查询，不重新解析原文或生成库内文档向量；文档量增长由已建索引和有界分页处理，不在查询时遍历 S3、解析全部 HTML/PDF 或临时重建索引。Document 命中后可按需读取相邻已存片段补齐上下文；查看指定原件是单独的证据读取操作。

标签不是唯一召回手段：无 Fact、分类失败、标签错误或未直接提及股票的文档，仍可通过向量语义搜索与来源/时间等字段查询进入候选。Embedding 未完成时其原始 Document 仍可读取，取材记录索引积压/失败，不假装已完成全部相关召回。

**pgvector 是首版必备能力**：Document/Chunk 的 Embedding 记录模型标识和维度；查询使用兼容的向量模型与相应索引，不混合不同模型的向量距离结果。原始内容可读与向量可检索是两个状态，分类及抽取失败也不阻止向量索引任务。

当前 `stocks.search` 提供两条读取路径。语义查询只为本次查询文本生成向量，通过当前模型和维度的 HNSW 余弦索引结合来源、发布时间或采集时间过滤，返回当前解析批次中的持久 Chunk、Document 版本、来源和相似度；精确查询只接受 64 字符以内的股票代码或指标编号形式，使用词边界正则在有界结果集中匹配已存 Chunk，不依赖 Embedding 或分类。两条路径都返回实际过滤条件和 `truncated`；语义查询还返回当前解析批次的向量索引覆盖，精确查询不需要 Provider 或向量空间，因此不报告由模型决定的向量覆盖。`truncated` 表示返回数量达到上限，未扫描数量在未知时保持未知；精确查询要求至少存在已解析的当前 Chunk。Document 命中后可用 `--chunk-id` 读取当前批次的相邻已存片段。`stocks.research.select_evidence` 基于这些结果形成持久 Evidence Package。

**V1-05 当前实现：** `stocks.changes.search_changes()` 是原始变化页的公开读取入口。它在同一条 PostgreSQL 查询中按固定 `(start,end]`、可选 `kind` 与 `Document.source_id` 条件先过滤，再执行 `LIMIT`，并以 `(watermark, change_record.id)` 返回原始续点；额外读取一行只用于判断该筛选条件在原窗口内是否还有记录，因此不会被候选按 Document 聚合后的水位推进分页。`ChangePage.completed` 只表示本页已耗尽当前固定查询条件，`TaskRun.change_scan_progress.status='scanned'` 也只表示原始页耗尽；二者都不表示最终报告成功或成功游标已经推进。当前边界是原始 change scan 与 checkpoint；增量 Document/Fact 与历史背景证据的统一合并、同 Document 证据组去重与覆盖统计自 V1-06 起由 `stocks.research.select_evidence()` 完成，`EvidencePackage.evidence_groups` 保存合并后的证据链。跨 Document 的转载识别仍未实现，公开证据 API 已在 V0-07 提供。

Embedding 模型引用、维度及分块参数通过配置指定；首版使用一个当前模型及其对应向量索引即可。模型切换后的重建属于后台处理，完成前明确显示索引覆盖状态，不以无结果掩盖重建缺口。结构化 Fact 查询不以其是否已生成向量为前提。

**取材查询边界：** `stocks.changes.search_changes()` 按固定 Run 窗口读取新增 Document/Fact 变化原始页，`stocks.repository.search` 对应路径不受增量窗口限制、读取适用历史证据。当前 Evidence Selection 已接入前者并在 V1-06 中与后者统一合并：同一 Document 的增量与背景命中折成一个证据组，Fact 关联到来源 Document/Chunk，`EvidencePackage.evidence_groups` 保存合并结果与 `new_information` / `historical_context` 角色。仍分别记录的 `candidates_discovered`、`candidates_screened`、`selected_count`、`evidence_group_count` 与 `filtered_out_count` 用于区分原始候选、筛选、入选和独立证据组。跨 Document 转载识别仍属后续能力；两类读取都不修改全局 Document 消费状态。

## 来源可信度、证据与安全

可信信息分为三个独立维度：

1. **来源身份与默认可信度**：T1 官方原始记录、T2 可识别原始发布者、T3 二手媒体/机构、T4 未核实来源。这些是可配置初始类别，不是客观真伪概率。
2. **具体论断属性**：披露事实、已知数值、个人观点、预测、未经证实的传闻，分别标记。
3. **证据支持程度**：研究的事实、推断与未经证实信息要区分，重要判断引用支持或相反的资料并说明不确定性；不要求独立核查运行表或状态聚合系统。

可高度信任官方系统确实披露了一份文件，但不因此认定文件中的未来预测必然成立。低等级来源可以作为线索，不可仅因级别低就静默丢掉重要未核实事件。可信度、重要性、交易价值不能混成一个分数。

安全边界：

- 原文、RSS 正文、网页、搜索结果和 MCP 返回值均为不可信数据，不执行其包含的指令。
- Agent 的 system 指令、工具白名单、参数边界由可信 Task/Agent 配置控制；工具实际执行前由 Stocks 校验。
- 外部服务默认仅授予只读搜索和提取能力，不授予交易、任意数据库写入、Shell 或密钥读取能力。
- 对抓取内容进行类型/大小约束、URL 安全校验、限流与日志脱敏。
- 重要研究判断引用可查证的 Document/Fact 原始依据，否则标明证据不足。
- “找到多条报道”不代表“多个独立来源确认”；需要追踪最初发布者与转载关系。
