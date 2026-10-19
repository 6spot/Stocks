# Task、Agent 与 Tool 执行契约

本篇只负责 Task / Run 的执行、触发、调度、模型配置和工具调用；研究语义及取材输出见 [研究设计](research.md)，数据模型见 [数据设计](data.md)。返回 [架构总览](architecture.md)。

## Task 执行契约

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


### 数据生命周期、可靠触发与证券 fan-out

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

### 默认采集、综合研究与可选即时研究示例

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
      - id: verify
        use: repository.verify_key_findings
        input: save.output
        with: {scope: key_facts_and_numbers}
      - id: report
        use: repository.render_report
        input: {analysis: save.output, verification: verify.output}

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
