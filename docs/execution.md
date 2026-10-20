# Task、Agent 与 Tool 执行契约

本篇只负责 Task / Run 的执行、触发、调度、模型配置和工具调用；研究语义及取材输出见 [研究设计](research.md)，数据模型见 [数据设计](data.md)。返回 [架构总览](architecture.md)。

**阶段边界：** V0 由 Owner 手动触发采集和一项研究，保存实际输入、引用和报告；查询当时已保存的资料，不使用“上次成功以来”的增量游标。V1 增加独立定时 Task、后台处理、可靠增量窗口及恢复。V2 增加研究中的授权外部补证和更丰富的模型/Tool 能力。下文定时与窗口规则从 V1 起适用。

## Task 执行契约

Task 属性：

| 属性 | 语义 |
| --- | --- |
| id / name | 稳定标识、可读标题 |
| type | collection / processing / analysis / report 等显示分类，不限制允许的步骤 |
| strategy | 可选策略 ID；无策略的采集任务正常运行 |
| trigger | V0 使用 manual；V1 增加 schedule，cron 明确时区，every 表示固定间隔；文档事件型研究触发属于后续增强 |
| scope | 市场/地域、公司名称、股票代码、行业与主题等研究范围；在 Task 顶层 YAML 唯一定义，研究 Run 固定为 `run.scope` 快照 |
| steps | 有序步骤及参数/上游结果引用 |
| enabled | 是否参与触发 |

目标步骤种类：Connector 获取、Repository 持久化/查询、解析与分块、Decision Model 分类及抽取决策、按需 LLM 抽取、Embedding 与索引、研究 Agent 调用、授权 Tool 查询及报告保存。**V0 手动获取和研究；V1 用 YAML 定时采集、异步整理数据，并按任务自身设定的早晚/日/周时间生成综合报告；任何阶段都不按文章逐篇生成报告。**

步骤按配置顺序执行；when 仅允许基于已执行步骤的结构化字段或状态判断，不使用自由 Python 表达式或通用 DAG。满足条件才执行；条件不满足为 SKIPPED，不是 FAILED。

研究的取材、游标、分析与报告共用 `run.scope`，步骤不再另定义一份研究范围；补充查询可带具体检索条件以查找直接资料或相关背景，但不能修改 Run 的研究范围。采集轮询由采集 Task 的 `trigger` 唯一调度，Source 只保存获取参数和可选更新预期，不另设第二个调度入口。

**V0 已实现的手动研究子集：** YAML 只接受 `repository_search` 与 `research_agent` 两种步骤，研究 Task 的最后一个步骤必须是 `research_agent`。`repository_search` 读取已保存的当前 Chunk 和检索结果，可选 `source_ids` 最多 20 个且必须去重；不配置时查询全部来源。`research_agent` 只接收该 Run 内前序搜索结果；`tools` 必须为空，不执行外部 Tool/MCP。`when` 只允许引用更早的 `repository_search` 结果，并仅支持以下白名单判断：

| `op` | 允许的 `field` | 语义 |
| --- | --- | --- |
| `exists` | 不配置 | 引用的前序步骤执行成功 |
| `nonempty` | `output`、`output.items`、`output.coverage`、`evidence_refs` | 对应结构化结果为非空 |
| `equals` / `not_equals` | `output.truncated` | 与显式布尔值比较 |

条件不满足时保存 `skipped` 的 StepResult。配置拒绝未知字段、未知步骤/类型、未知 Agent、重复步骤 ID、向前引用、未声明 Schema 关键字以及未实现的 Tool。公开示例位于 `config/tasks.yaml`，分别覆盖成功执行、条件跳过和无证据失败。

统一执行结果：

~~~text
StepResult:
  status: succeeded | failed | skipped | cancelled
  output: validated structured payload | null
  evidence_refs: list of durable references
  usage: tokens, duration, cost (where available)
  error: structured error | null
~~~

按任务记录配置/策略/模型/提示词快照，便于解释历史研究结果。Task 使用独立运行记录，Worker 与 Web API 分进程；待执行记录及任务运行占用保存在 PostgreSQL。外部调用需有超时、有限重试和幂等键。原始资料及处理状态成功持久化后，Worker 才处理相应待办步骤；这不触发逐篇研究，综合研究仍由自身的 manual/schedule 触发。

V0 Run 在创建时快照 Task 的 objective、strategy、scope、步骤/Agent 配置、research prompt 和 Research Agent 模型路由，也快照 Repository 搜索使用的 Embedding 路由（别名、模型、维度、能力、超时及可用于核对配置是否有变化的端点哈希）。执行时重新取得环境凭据，并核对当前路由与快照；发生不兼容变化时步骤失败，不静默换成另一模型或向量空间。Task 和 Run 不保存 endpoint 明文、API key 或 Authorization。每个 StepResult 保存结构化 output、持久证据引用、usage 和结构化 error；ModelCall 保存请求/实际 alias 与模型名、供应商返回模型名、耗时、Token、回退原因和安全错误，不保存请求或响应正文。

### 同一任务运行与异常处理

- **同一个 Task 同时只允许一个待执行或正在运行的 Run。** 手动触发、定时触发与重试共用同一入口，由数据库原子检查并占用；任务已占用时不再创建新 Run，手动请求返回现有运行信息，定时触发跳过本次。不同 Task 可以独立运行。
- V0 的手动 CLI 在一个命令中完成原子创建、领取和执行。数据库对 `status IN ('queued', 'running')` 建立每个 Task 唯一的条件索引；重复触发若已有活跃 Run，立即输出该 Run 的 JSON 摘要并以成功退出码返回，不查询模型凭据、不等待或抢占其他进程。新 Run 结束后才可创建下一次 Run。所有权令牌只标识当前执行进程，普通触发不会接管其他进程已领取的 Run。
- V1 起，Run 未正常结束、Worker 中断或处理失败时，保留已保存的进度、结果和错误，供控制台统一查看。Owner 确认旧执行已停止后，可结束异常 Run，再重试或继续处理；不要求自动租约接管或复杂崩溃恢复调度。
- 解析、分类、抽取、索引和取材积压使用 Task/Run 或 Document 处理状态管理；有限自动重试后，可由 Owner 在控制台手动重试或继续处理，不另建积压管理服务。交互见[界面设计](interface.md)。


### V1 数据处理与定时研究

采集与研究独立运行；原始资料先保存，公司名称和股票代码由分类结果作为检索标签提供，不额外执行证券身份解析。常规 Source 采集只处理新条目与来源已发出的更新信号，不新增自动回访既有文章的调度 Task；V2 研究时确需复核可通过授权 Tool 按需获取并入库。

| 阶段 | 系统行为 |
| --- | --- |
| 1. 采集、获取与入库 | 配置 RSS/媒体/API 范围内的新内容及更新全部收录；外部搜索按研究目的选择要获取的结果。取得的原文或原始响应统一存 Document、S3 快照、来源/时间和幂等键。全文失败须标记不完整 |
| 2. 后台轻量处理 | 按[后台解析契约](data.md#后台解析片段与索引)完成必要解析和分块；适用文本分类并按需抽取 Fact，结构化响应直接解析 Fact；片段生成 Embedding 并建立 pgvector 索引。各步骤保存可复用结果及处理状态 |
| 3. 研究 Task | YAML 设置 `manual` 或带时区 `schedule`，触发时按同一任务运行规则创建 Run，从 Repository 统一检索 Document + Fact，合并去重后交给同一研究 Agent |
| 4. 报告留存 | 成功保存报告及本次证据引用后，按下文扫描进度规则推进对应任务增量窗口；失败保留记录，不修改已入库情报或丢失待研究增量 |

解析、分类、抽取与 Embedding 通过后台 Worker 的有限重试和幂等机制执行。原件、片段和 Fact 的存储与依赖以[数据设计](data.md#后台解析片段与索引)为准；重试复用已完成步骤，从失败或未完成步骤继续，不因研究再次查询就重跑处理链路。任何一步失败时都保留已入库原文并展示对应积压；分类错误不能视为“无关”，未抽取 Fact 的文档也应可被 pgvector 检索；未索引的 Document 可查看但必须暴露召回缺口。

**整个 Stocks 系统不提供证券身份主数据、证券级事件解析或按股票自动 Fan-out。** 市场早晚报告和 NVDA 早晚报告是两份独立配置的 YAML Task；如需关注多个股票，直接定义各自的研究任务，仍调用同一套 Repository 与 Research Agent。此处不是首版限制，也不作为后续扩展路线。

### 采集与定时研究示例

#### 研究增量窗口与共享资料引用

以下增量规则从 V1 起适用；V0 手动研究只记录本次实际使用的资料、版本和生成时间，不设置或推进增量游标。

**研究窗口是 Task Run 查找新增变化的范围，不是 Document 的属性。** Document 记录来源首次发布时间 `published_at`、来源最后更新时间 `source_updated_at`（可为空）、Stocks 采集时间 `collected_at`、原始出处和内容版本；研究游标与取材状态归属于 Task/Run。

| 时间或引用 | 所属记录 | 作用 |
| --- | --- | --- |
| `published_at` / `source_updated_at` / `collected_at` | Document | 来源首次发布、来源最后更新（可缺失）及 Stocks 采集时间 |
| 解析/分类/Fact/索引处理时间 | Document/Fact/Chunk 及处理记录 | 标明各项结果何时可供研究使用；报告引用当时的输入快照 |
| `change_watermark_start` / `change_watermark_end` | Task Run | 本次新增/变更情报的可靠扫描边界，采用 `(start,end]`；只由已提交变化推进 |
| `window_start` / `window_end` | Task Run | 与本次变化水位关联的展示时间，不单独决定候选是否进入窗口 |
| `as_of` | Task Run | 本次研究的参考时间，默认取 Run 启动时间；不限制运行中按需补证，也不表示严格的历史知识截止 |
| 成功增量游标 | Task + Research Scope | 为本任务的下一次运行确定增量起点；不同研究任务/范围互不影响 |
| `research_inputs` | Task Run / Analysis | 记录实际使用的 Document 内容版本、Chunk/Fact 引用、分类输入快照和历史 Analysis 引用 |

**时间语义：** `change_watermark_end` 固定本次例行增量扫描的终点，`window_end` 仅记录展示时间，`as_of` 标明研究参考时间。这些字段不禁止 V2 研究过程中通过授权工具获取补充资料。运行中取得的资料保存真实来源时间、采集时间和版本，按[研究设计](research.md#agent-补充检索与故障边界)追加到本次实际输入；晚于参考时间发布或更新的资料如被采用，应在报告中明确说明。报告保存当次实际取材和生成时间，后续资料更新不改写旧报告。当前产品不承担严格还原过去某一时刻全部已知信息的历史回放能力。

**窗口生成与推进规则：**

1. 研究 Run 开始执行时固定本次变化水位范围：起点取**同一 Task + 研究范围上一次成功提交结果并完成扫描的终点水位**，终点取本次启动时可确认已提交的最高水位；`window_start` / `window_end` 记录对应展示时间，默认 `as_of` 为 Run 启动时间。第一次运行使用明确配置的初始起点或初始回看范围并据此确定初始水位；更换研究范围后重新初始化，不沿用旧范围游标。待执行期间不提前扫描或推进窗口。
   若同一范围存在待继续的扫描进度，优先沿用保存的原窗口、查询条件及分页位置，扫描完成后再打开后续窗口；本次 Run 的 `as_of` 仍按实际参考时间记录，不将续扫误报为新的扫描范围。
2. 窗口只用于发现已进入 Repository、并在本次可研究增量中首次出现或发生重要修订的资料。文章的 `published_at` 不用于判定是否“已处理”：周二才入库或完成分类的上周五公告，也会作为周二新增可研究信息进入后续 Run。保留资料自身的真实发布时间供报告解释。
3. 对 Document 入库、解析/分类/索引完成、Fact 抽取/解析及内容修订保留可追溯的系统可研究变化。增量窗口以**已提交变化的稳定水位**界定；水位必须按提交可见顺序推进，不能直接把事务开始时的 `collected_at`、自增序列分配时刻或原文发布时间当作游标。Run 取得终点水位时，应保证此前水位的写入已经提交；之后提交的写入即使更早开始，也进入后续窗口。窗口同时保留相应时间用于报告展示。实现可使用同一 PostgreSQL 内按事务串行提交的变化计数，不引入额外服务。
4. 只有要求的分析与报告结果**成功持久化**且对应窗口**完成扫描**后，才原子地推进该 Task + Scope 的研究游标。扫描未完成时，将查询条件、原窗口与分页位置保存在 Task/Run，后续自动或由 Owner 手动继续扫描，不因已生成一份报告就跳过剩余候选；已完成筛选但未选入模型输入的候选不阻塞推进。失败、取消或中断不推进；下次运行重试或覆盖未成功区间。即使周末无定时运行，周一仍包含周末新增信息。上述进度沿用现有记录，不新增独立扫描服务。
   “完成扫描”针对本次记录的增量查询范围与候选，不表示已穷尽全部背景资料或互联网信息；索引积压和来源缺失仍按实际情况说明。
5. Agent 可以根据明确的 Research Scope、`as_of` 与检索条件访问**增量窗口之外的历史背景资料和以前的 Analysis**，并对比既有结论；这些背景不应错误计作本次新增情报。每个研究 Run 将最终采用的输入 ID、版本、选择原因或查询条件留在独立 `research_inputs` 中，以便复现。
6. 同一份资料可参与多个不同的 Task、时间窗口与策略研究，不在 Document 上设置全局 `analyzed`、`last_used_by_task`、`research_window` 或 `consumed_at`。重复性由各自 Run 和输入引用的幂等约束处理。

~~~text
Run A（日报）：增量变化窗口 [上次成功扫描终点, 本次扫描终点)
                 + 查询此前财报、历史政策及过往观点作为背景
                 → 保存本次 research_inputs → 综合报告

Run B（行业周报）：自身独立的增量变化窗口
                 + 可以复用日报引用过的同一 Document
                 → 保存自身 research_inputs → 行业报告
~~~

**两类检索必须区分**：`repository.search_changes` 按变化水位选择本次新增/修订的候选证据；`repository.search` 按研究时点、Scope 与条件选择所有可用的背景/验证资料。二者均读取同一 Repository，前者不修改资料，也不把研究消费进度写回 Document。研究任务通常通过[统一研究取材](research.md)的 `repository.select_evidence` 统一执行这两类查询及取材，不需要每种研究自己重写步骤。

以下为 **V1 定时任务的逻辑配置示例**，不属于 V0 交付门槛：

~~~yaml
tasks:
  collect_news:
    type: collection
    trigger: {kind: schedule, every: 15m}
    steps:
      - id: fetch
        use: connector.rss
        with: {source: financial_news}
      - id: acquire
        use: collection.acquire_content
        input: fetch.items
      - id: save
        use: repository.ingest
        input: acquire.output

  daily_market_report:
    type: report
    trigger: {kind: schedule, cron: "0 8,20 * * *", timezone: America/New_York}
    window: {mode: since_last_success, initial_lookback: 7d}
    scope: {regions: [US], topics: [macro, policy, market_sentiment]}
    steps:
      - id: evidence
        use: repository.select_evidence
        with:
          scope: run.scope
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

  nvda_daily_report:
    type: report
    trigger: {kind: schedule, cron: "0 8,20 * * *", timezone: America/New_York}
    window: {mode: since_last_success, initial_lookback: 7d}
    scope: {symbols: [NVDA], companies: [NVIDIA], topics: [semiconductors]}
    steps:
      - id: evidence
        use: repository.select_evidence
        with:
          scope: run.scope
          objective: "综合 NVDA 相关公司公告、行业政策、走势与市场背景"
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

~~~

这里的 `financial_news`、`collection.acquire_content`、Agent ID 与时间都是逻辑配置示例，并非已实现接口；`acquire` 表示任意渠道只取得标题/摘要/链接时需补取正文，已取得全文或完整响应则无需重复下载。**fetch 和 acquire 之间不执行逐篇相关性过滤。** `scope: run.scope` 表示读取当前 Run 的研究范围快照；入库后处理依赖遵循[数据设计](data.md#后台解析片段与索引)，综合研究仍由各自 YAML Task 调度。

## Agent、HTTP 模型与回退

Agent 配置包括：id、role、prompt、model_ref、tools[]，以及按职责需要的结构化输出契约。`model_ref` 指向 `config/models.yaml` 中的 Agent 路由名；该路由的 `primary` / `fallbacks` 是模型别名，不要求与 Agent 的 `model_ref` 同名。**Decision Model 是模型能力类别，不是 Jev、Clef 等特定品牌的别名**：Jev、Cloudflare Clef/Clef-flash 或符合相同需求的其他模型均可配置为分类/决策模型。分类步骤对预定义问题做单选、多选或是/否判断，包括 `should_extract`，并统一归一化为 Stocks 的业务 Schema；如 Provider 返回各选项概率，仅作为决策输入/诊断信息，不自动作为事实可信度或投资评分。Generation Model 则用于按需生成事实抽取结果、研究文本及授权 Tool 调用请求。不同模型类型通过协议 Adapter 接入，不强行假设所有模型都能输出长文、原生工具调用或 JSON Schema。

Provider 定义 protocol、HTTP endpoint、认证方式、默认超时等。**决策模型的有界问题/选项接口与普通 OpenAI-compatible `chat/completions` 并不必然相同**；按实际 Provider 协议通过薄 Adapter 归一化服务端格式。文中的模型名称仅作可配置角色的示例，不代表协议兼容性已经验证或相应 Adapter 已实现。对于不确定、无效或调用失败的决策，保留原文与未完成状态，不能默认解释成 `should_extract=false`。Provider 不承担 Agent 逻辑，任何私有部署、官方服务或模型聚合服务都只是一个 Provider 配置实例，不构成系统依赖，不引入独立模型调度服务。

Embedding 同样通过 Provider 和模型引用配置，声明向量生成能力，复用现有超时、有限重试和调用记录。入库片段与查询文本使用兼容的模型和维度；回退目标不兼容时记录失败，不混写不同向量空间。索引与模型切换的行为由[数据设计](data.md#检索)维护，不新增模型路由服务。

模型策略只定义 primary 与按顺序排列的 fallback[]：

- 主模型遇额度耗尽、限流、服务错误、连接失败或超时等可回退故障时，尝试备用模型。
- 配置错误、认证错误、能力不支持或错误请求等不进行盲目回退。
- 回退模型必须满足当前步骤所需能力；全部失败则记录并按 Task 策略处理失败。
- 每个候选模型使用自身配置的请求超时；`retries` 是每个候选在切换到下一候选前的最大重试次数，回合总数受有限上限约束。
- 不做模型质量动态打分、成本自动选路或无限重试。
- 记录请求的 Provider/模型、实际可获得的返回模型标识、耗时、Token、可获得费用及回退原因。

## Tools 与远程 MCP

Tool 与 Connector 处于不同边界：

- Connector 为常规采集 Task 提供重复、增量的数据获取。
- Tool 用于 Agent 或显式 Task 步骤中的按需查询、补充和交叉验证；研究**先查询 Repository**，只在现有数据不足、过期、存在冲突或需要核实时访问外部来源，不能默认每份报告重新调用行情、财务和宏观 API。
- MCP 仅支持远程 Streamable HTTP；不实现 stdio 客户端。
- MCP 的工具发现、调用和结果解析由通用客户端负责。外部服务作为配置项，不为每个工具提供商实现一套底层框架。
- MCP 搜索供应商属于“发现/查询渠道”，其返回网页的真正 Publisher 是原网站，不因搜索服务本身而提升可信等级。
- 研究通过外部搜索、MCP/Tool 找到资料时，按研究目标选择实际获取的结果；取得原始正文/响应后，通过统一 Source/Repository 持久化并分类、按需提取 Fact、生成 Embedding。Analysis 引用已保存证据，不能把一次性搜索摘要当作全文；已有 Repository 资料满足需要则直接复用。
- 工具调用由 Stocks 授权后执行，模型只能提出结构化请求。
