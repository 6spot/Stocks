# Task、Agent 与 Tool 执行契约

本篇只负责 Task / Run 的执行、触发、调度、模型配置和工具调用；研究语义及取材输出见 [研究设计](research.md)，数据模型见 [数据设计](data.md)。返回 [架构总览](architecture.md)。

## Task 执行契约

Task 属性：

| 属性 | 语义 |
| --- | --- |
| id / name | 稳定标识、可读标题 |
| type | collection / processing / analysis / report 等显示分类，不限制允许的步骤 |
| strategy | 可选策略 ID；无策略的采集任务正常运行 |
| trigger | V0 使用 manual 或 schedule（明确时区）；文档事件型研究触发属于后续增强 |
| scope | 市场/地域、公司名称、股票代码、行业与主题等研究范围；直接从 YAML 配置 |
| steps | 有序步骤及参数/上游结果引用 |
| enabled | 是否参与触发 |

最小步骤执行种类：Connector 获取、Repository 持久化/查询、Decision Model 分类及抽取决策、按需 LLM 抽取、研究 Agent 调用、授权 Tool 查询及报告保存。**V0 使用 YAML 定时采集、异步整理数据，并按任务自身设定的早晚/日/周时间生成市场或个股综合报告；不按文章逐篇生成报告。**

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


### V0 数据处理与定时研究

采集与研究独立运行；原始资料先保存，公司名称和股票代码由分类结果作为检索标签提供，不额外执行证券身份解析。

| 阶段 | 系统行为 |
| --- | --- |
| 1. 原始资料入库 | Source/Connector 获取文章、公告或 API 响应，先保存 S3 快照及 Document、游标与幂等键 |
| 2. 后台轻量处理 | 对适用文本由可配置 Decision Model 一次完成类别、标签（含有依据的公司/代码）和 `should_extract`；需要时再调用 LLM 生成 Fact。结构化 API 响应直接由 Source Mapper 解析 Fact |
| 3. 研究 Task | YAML 设置 `manual` 或带时区 `schedule`，到点独立创建 Run，从 Repository 统一检索 Document + Fact，合并去重后交给同一研究 Agent |
| 4. 报告留存 | 成功保存报告及本次证据引用后推进对应任务增量窗口；失败保留记录，不修改已入库情报或丢失待研究增量 |

后台分类/抽取任务通过已有 Worker 的有限重试与幂等机制运行。分类失败时保留原文并展示积压，不将失败当成“无关信息”或 `should_extract=false`；未抽取的原文仍能参与研究。

**整个 Stocks 系统不提供证券身份主数据、证券级事件解析或按股票自动 Fan-out。** 市场早晚报告和 NVDA 早晚报告是两份独立配置的 YAML Task；如需关注多个股票，直接定义各自的研究任务，仍调用同一套 Repository 与 Research Agent。此处不是首版限制，也不作为后续扩展路线。

### 采集与定时研究示例

#### 研究增量窗口与共享资料引用

**研究窗口是 Task Run 查找新增变化的范围，不是 Document 的属性。** Document 记录来源首次发布时间 `published_at`、来源最后更新时间 `source_updated_at`（可为空）、Stocks 采集时间 `collected_at`、原始出处和内容版本；研究游标与取材状态归属于 Task/Run。

| 时间或引用 | 所属记录 | 作用 |
| --- | --- | --- |
| `published_at` / `source_updated_at` / `collected_at` | Document | 来源首次发布、来源最后更新（可缺失）及 Stocks 采集时间 |
| `classified_at` / 标签及 Fact 处理时间 | Document/Fact 及处理记录 | 标明分类、抽取何时可供研究使用；报告引用当时的输入快照 |
| `window_start` / `window_end` | Task Run | 本次新增/变更情报的选择范围，采用 `[start,end)` |
| `as_of` | Task Run | 研究取材的知识截止时点；所有输入在该时点必须已经可获得 |
| 成功增量游标 | Task + Research Scope | 为本任务的下一次运行确定增量起点；不同研究任务/范围互不影响 |
| `research_inputs` | Task Run / Analysis | 记录实际使用的 Document/Fact 引用、分类输入快照、数据版本和历史 Analysis 引用 |

**窗口生成与推进规则：**

1. 周期性研究 Run 创建时，将 `window_start` 设为**同一 Task + 研究范围的上一次成功完成并提交结果的 `window_end`**；本次 `window_end` 取启动时固定的截止时点。第一次运行使用明确配置的初始起点或初始回看范围。
2. 窗口只用于发现已进入 Repository、并在本次可研究增量中首次出现或发生重要修订的资料。文章的 `published_at` 不用于判定是否“已处理”：周二才入库或完成分类的上周五公告，也会作为周二新增可研究信息进入后续 Run。保留资料自身的真实发布时间供报告解释。
3. 对 Document 入库、分类完成、Fact 抽取/解析及内容修订保留可追溯的系统可研究时间；增量查询按这些已提交的变化去重，不能用原文发布时间替代系统入库时点。研究截止后完成的处理留给下一个 Run。
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

**两类检索必须区分**：`repository.search_changes` 选择本次新增/修订的候选证据；`repository.search` 按研究时点、Scope 与条件选择所有可用的背景/验证资料。二者均读取同一 Repository，前者不修改资料，也不把研究消费进度写回 Document。研究任务通常通过[统一研究取材](research.md)的 `repository.select_evidence` 统一执行这两类查询及取材，不需要每种研究自己重写步骤。

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
    trigger: {kind: schedule, cron: "0 8,20 * * *", timezone: America/New_York}
    window: {mode: since_last_success, initial_lookback: 7d}
    steps:
      - id: evidence
        use: repository.select_evidence
        with:
          scope: {regions: [US], topics: [macro, policy, market_sentiment]}
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
    scope: {symbols: [NVDA], companies: [NVIDIA]}
    steps:
      - id: evidence
        use: repository.select_evidence
        with:
          scope: {symbols: [NVDA], companies: [NVIDIA], topics: [semiconductors]}
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

这里的 `financial_news`、Agent ID、股票代码及运行时间都是**配置示例**，不代表已经实现相应 Task 或预设真实数据源。V0 使用 YAML 配置独立的市场、行业或个股任务；不提供可视化编辑，也不要求事件驱动研究。数据库仅记录执行时的配置、时间范围、实际输入及结果快照。

## Agent、HTTP 模型与回退

Agent 配置包括：id、role、prompt、model_ref、tools[]，以及按职责需要的结构化输出契约。**Decision Model 是模型能力类别，不是 Jev、Clef 等特定品牌的别名**：Jev、Cloudflare Clef/Clef-flash 或符合相同需求的其他模型均可配置为分类/决策模型。分类步骤对预定义问题做单选、多选或是/否判断，包括 `should_extract`，并统一归一化为 Stocks 的业务 Schema；如 Provider 返回各选项概率，仅作为决策输入/诊断信息，不自动作为事实可信度或投资评分。Generation Model 则用于按需生成事实抽取结果、研究文本及授权 Tool 调用请求。不同模型类型通过协议 Adapter 接入，不强行假设所有模型都能输出长文、原生工具调用或 JSON Schema。

Provider 定义 protocol、HTTP endpoint、认证方式、默认超时等。**决策模型的有界问题/选项接口与普通 OpenAI-compatible `chat/completions` 并不必然相同**；Jev 与 Cloudflare Clef 支持兼容的决策接口，但 Stocks 通过薄 Adapter 归一化服务端格式，不能假定现有的 Chat 模型客户端已经支持这些模型。按实际 Provider 协议接入，不引入独立模型调度服务。对于不确定、无效或调用失败的决策，保留原文与未完成状态，不能默认解释成 `should_extract=false`。Provider 不承担 Agent 逻辑，任何私有部署、官方服务或模型聚合服务都只是一个 Provider 配置实例，不构成系统依赖。

模型策略只定义 primary 与按顺序排列的 fallback[]：

- 主模型遇额度耗尽、限流、服务错误、连接失败或超时等可回退故障时，尝试备用模型。
- 配置错误、认证错误、能力不支持或错误请求等不进行盲目回退。
- 回退模型必须满足当前步骤所需能力；全部失败则记录并按 Task 策略处理失败。
- 不做模型质量动态打分、成本自动选路或无限重试。
- 记录请求的 Provider/模型、实际可获得的返回模型标识、耗时、Token、可获得费用及回退原因。

## Tools 与远程 MCP

Tool 与 Connector 处于不同边界：

- Connector 为常规采集 Task 提供重复、增量的数据获取。
- Tool 用于 Agent 或显式 Task 步骤中的按需查询、补充和交叉验证；研究**先查询 Repository**，只在现有数据不足、过期、存在冲突或需要核实时访问外部来源，不能默认每份报告重新调用行情、财务和宏观 API。
- MCP 仅支持远程 Streamable HTTP；不实现 stdio 客户端。
- MCP 的工具发现、调用和结果解析由通用客户端负责。外部服务作为配置项，不为每个工具提供商实现一套底层框架。
- MCP 搜索供应商属于“发现/查询渠道”，其返回网页的真正 Publisher 是原网站，不因搜索服务本身而提升可信等级。
- 研究从外部取得有复用价值的新证据时，必须先按数据类型走统一 Source/Repository 入库、原始响应/文档留存及来源记录，再由 Analysis 引用；不能仅使用一次性 Tool 返回文本却不保存。没有新数据时直接复用现有 Repository 引用。
- 工具调用由 Stocks 授权后执行，模型只能提出结构化请求。
