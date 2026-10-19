# 研究取材、分析与核查

本篇负责 Evidence Selection、Research Agent、Findings、历史研究关系及 Verifier；Repository 数据契约见 [数据设计](data.md)，Task / Run 的执行细节见 [执行设计](execution.md)，展示见 [界面设计](interface.md)。返回 [架构总览](architecture.md)。

## 统一取材：Evidence Selection 与 Evidence Package

**一套取材逻辑服务所有研究范围，不按市场/行业/个股分别建设取材引擎。Repository First 是默认取材规则**：同时查询已保存的 Document 原文与分类、关联 Fact、历史分析；结构化 API 数据也是由原始响应 Document 衍生的 Fact。检索结果在同一 Evidence Selection 中合并关联并去重，不能把 Fact 和其来源 Document 当成两份独立证据。已有数据满足时不重复调用外部 API。`repository.select_evidence` 是现有 Task 中可配置的 Repository 步骤：先寻找本次可研究的新增变化，再查询必要历史背景，整理可复查的 Evidence Package 交给 Research Agent。这里的 “Evidence Selection” 表示执行能力，“Evidence Package” 表示本次结果快照；**都不代表新微服务、全局 Agent 或持久化材料副本**。

### 取材请求与研究维度

| 输入 | 语义 |
| --- | --- |
| `run_id`、`task_id`、`task_version` | 研究所属 Task Run 与配置快照 |
| `objective` | 本次需要回答的研究问题，不限于预先固定的日报/行业/股票类型 |
| `scope` | Securities、Issuer、行业、区域、宏观主题和政策主题的任意适用组合 |
| `window_start` / `window_end`、`as_of` | 仅前者界定新增变化；`as_of` 约束所有事实和版本可获知性，沿用[研究窗口与任务示例](execution.md) |
| `data_requirements[]`（可选） | 研究关注的证据类型或主题维度，例如政策、宏观指标、企业披露、行业信息、历史观点 |
| `budget`（可选） | 证据 Token 总预算、候选扫描上限、每类内容/维度的取材上限等有界限制；未配置使用可调整的默认值 |
| `selection_policy_version` | 本次取材规则或配置版本，便于解释不同运行的差异 |

- 已显式配置的 `data_requirements` 作为研究偏好；缺省时，使用 Scope 的结构化标签与 Objective 相关的关键词/主题词表生成默认维度和检索条件。必要时允许由已配置的低成本 Decision Agent 提出补充维度，但其结果须经受控词表/查询参数校验，不能修改 Task 权限、时间截止或预算。**不为市场日报、行业报告和个股报告分别硬编码固定取材数量或查询流程。**
- **精确关联和背景关联分开**：研究某 Security 时，直接关联的公告/财务等是核心候选；行业政策、供应链或宏观信息是间接背景候选。后者不能因被纳入 Evidence Package 就被写成 `document_entities.confirmed`，不能冒充公司直接证据。
- 一个维度可以在本次无适用资料；必须表示“没有找到/未能获取/尚未扫描完整”中的实际情况，不能为满足取材额度制造内容或将其他维度的资料强行改类。
- **检查信息覆盖偏差**：热门大公司、英文资料与成熟行业通常有更多报道；数量多不代表更具投资价值、证据更独立或研究更确定。取材记录一手资料可得性、指标历史长度、独立原始来源分布、语言/地区覆盖及关键数据缺口；不凭新闻条数自动对公司评级，也不因资料少认定其基本面差。
- **原始资料与反向观点并重**：关键财务/监管事实优先获取一手申报、公司公告与监管文件；二手观点也可用来寻找解释和反面证据，但保留源头关系，不能只采纳支持既有结论的新闻。

### 候选获取、去重、排序与预算

按以下顺序执行，全部为现有 Repository、Worker 和 Task 能实现的操作：

1. **新增集合**：按 `window` 与 `as_of` 调用 `search_changes`，同时寻找本次新入库的 Document、Fact 及有意义的修订；按稳定引用和变化类型去重。旧资料晚到归入本次增量，但仍保留真实 `published_at`。
2. **背景集合**：按 Scope + Objective 使用 `search`，查询研究时点可获知的历史 Document、Fact 和已有 Analysis，不受增量窗口限制。区分 `role = new_information / historical_context`；同一资料可被不同 Run 复用。
3. **统一合并与去重**（仅影响本次研究输入，不删除原始存储）：
   - **Document + Fact 同时命中**：以 Fact 的结构化字段表达该项已整理事实，并关联其来源 Document 的原文/响应位置；原文仍可补充该 Fact 未覆盖的其他信息，**不能整篇排除**。同一 Fact 经全文与结构化两条查询路径返回时只出现一次。
   - **重复原文/转载**：相同来源的重复抓取由入库幂等处理；已确认内容相同或转载同一原始出处的不同 Document 合为一组证据，保留各份来源及链接。仅靠向量相似、主题或标题相同不得认定为重复。
   - **同指标跨来源**：指标、主体、统计期间、单位与口径一致且数值一致时，可合并为一组事实并保留各来源引用；**数值冲突、版本不同或口径不同必须并列保留，不能平均或静默覆盖**。
   - **独立观点与其他事实**：同一事件下独立观点、反证和同一 Document 中其他不重叠的事实继续保留；仅在已确认同一源头时按一份独立证据计数。
   - **增量与背景重复**：同一事实同时进入 `search_changes` 和 `search` 时统一复用其引用，不重复加入研究输入；原始候选计数、合并后证据组数分别记录。
4. **候选优先级**：按与研究目标/Scope 的相关性、增量信息价值、事件/数据修订的重要性、可追溯来源、观点差异、所研究时间的适用性组织稳定排序；使用可解释的规则、已保存分类、全文和必要的语义检索即可。来源信誉只是独立维度，不等于真实性或投资重要性；**不引入必须依赖模型的复杂综合打分系统**。
5. **维度覆盖与裁剪**：从本次需要的研究维度中分配软预算，优先覆盖核心问题、重大新变化、关键事实及重要反面证据，避免某个高频新闻主题或同一转载来源耗尽全部输入；在严格的总 Token/候选扫描预算内选择可追溯的代表性文档片段、完整数值点/序列引用及历史结论。维度预算按 Task 可调，空维度不强制填充。裁剪的是**本次模型输入**，不删除 Repository 数据。
6. **引用与快照**：取材结果保存唯一 ID、来源、版本、选入原因、原始证据链、确定性排序/规则版本、实际采用的正文片段/数值引用、各维度计数和截断原因。研究 Agent 收到这个快照，而不是任意数量的全文。

**去重后输出必须保留每组证据对应的全部原始 Document/Fact 引用，报告不能将同一来源的 Document 与它的 Fact 计为两条独立支持证据。** 未参与事实抽取的 Document 依然可进入候选；索引、分类不确定或失败不得等同于不存在事实。

**预算与覆盖统计必须区分三个数量：** `candidates_discovered`（查询实际发现的候选）、`candidates_screened`（在本次规则下已完成筛选）、`selected_count`（入选 Evidence Package）。还要记录 `unexamined_count`、查询是否分页/触顶 `retrieval_truncated`，以及各来源的可用性；**扫描上限达到时不能把“只扫描了部分候选”标为全部已覆盖**。候选多但完成筛选后未入选，属于有意取舍；根本没有扫描/来源不可用属于覆盖缺口，必须分别说明。包内的 `selected_count` 不应等同于独立证据组数量，转载组合并需单独统计。

### Evidence Package 数据契约

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
    - ref: fact_205
      source_document_ref: doc_202
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

### Agent 补充检索与故障边界

- 研究 Agent **先**通过 Repository 的只读检索补齐问题；已有可用材料不能因为方便而默认重查外部。只有关键事实缺失、不够新、来源冲突或确需交叉核实时，才在 Task 明确授权下调用外部 Tool。新获得的有复用价值的原文或结构化响应经统一 Source/Repository 流程登记、留存原始证据与时间，生成可追溯引用，再按相同 `as_of`、权限和预算约束加入本次 `research_inputs`；不得把新资料标记为 Document 的“已研究”状态，也不得覆盖原始证据包快照。
- 模型不应通过重新搜索任意外部网站绕过研究截止时点。必要的远程 Tool 补证仍走可信来源登记；若该资料在 `as_of` 后才公开，不能作为该历史研究时点的已知事实。
- 取材失败与无候选不同：检索超时、分类积压、数据源断更、候选扫描不完或版本无法确定时，分别记录原因和受影响维度。可配置 Task 是否允许“带明确覆盖不足的报告”；不满足 Task 成功条件就不能推进[研究窗口与任务示例](execution.md)的研究游标。
- Evidence Package 在下游 Agent 失败时保持可复查，重试默认沿用同一 Run 已保存的输入、版本和选择规则；明确发起的新研究 Run 才按新的 `as_of` 检索更新的资料。
- 同一 Task Run 无论研究市场、行业、股票或混合主题，都调用同一个 `repository.select_evidence`；不新增研究专属的采集器、独立 Evidence 服务、DAG 或复杂打分调度器。

## Research Agent 的输入、行为与有界补证

Research Agent 是普通可配置的 Agent 角色，执行现有 Research Task 中的一个研究步骤；**不是独立的调度服务、固定的报告类型或额外的 Research Engine**。市场、行业、个股、跨主题研究统一使用相同的 ResearchContext 和输出契约，通过 Objective、Scope、可选 Strategy 与证据包体现差异。

**输入 ResearchContext：**

| 字段 | 契约 |
| --- | --- |
| `run_id`、`objective`、`scope`、`strategy_ref?` | 对应 Task Run 的不可变配置快照；研究什么、面向何种视角 |
| `as_of`、`changes_window` | 所有引用必须在 `as_of` 可获知；窗口仅划定新增变化而不限制历史背景 |
| `evidence_package_ref` | 指向[统一研究取材](research.md)已保存的取材快照，不要求全文重复塞入 Prompt |
| `previous_analysis_refs[]` | 相关历史 Analysis/Findings 的稳定引用及可用版本；允许为空 |
| `coverage` | 取材/来源缺口、未扫描候选、分类积压等；缺口不是“没有发生变化”的证据 |
| `allowed_tools[]`、`budget` | 由可信 Task 配置授权的 Repository/远程工具与有限调用/Token 预算，模型不能扩权 |

**研究行为：**

1. 从本次新增变化入手，结合历史资料识别与 Objective 相关的事实、指标修订、事件与政策变化，不逐篇按新闻顺序复述。区分“新公开的旧事实”“既有指标的修订”“当前发生的现实变化”和“不同来源的观点”。
2. 根据相关性、变化程度、可能的影响路径、证据强弱、历史研究差异和待解决问题，判断哪些内容对**本次研究**重要。重要不等于利好、利空或确定性；没有显著新变化也是有效结论，不能为凑栏目发明新观点。
3. 检查 Evidence Package 的关键缺口，主动追问重要判断最可能错在哪里、哪些证据可以推翻它、是否存在其他合理解释；必要时用已授权 Repository 只读 Tool 进行少量反证与背景补充检索。找不到反证不意味着已证明正确；缺少来源、反面证据或指标时可带不确定性结束，不能捏造数据。
4. 将研究结果表示为统一的 Analysis：简要综合判断、`findings[]`、`open_questions[]`、引用证据和覆盖范围；向报告层提供可阅读的段落建议，但不把 Markdown 作为唯一资产。
5. 对历史 Finding 的变化作有证据的**候选关系判断**，由系统验证其引用的目标 ID、Run/Scope 和时间可见性后保存；不因模型一次判断就改写旧 Finding 或核查状态。

**补充检索调用与终止条件：** Agent 只能提出已授权工具中的结构化请求，说明 `purpose`、允许的过滤条件及需要查证的问题；系统强制固定 `as_of`、超时、调用次数、Token 预算及来源登记。对同一检索无进展时停止，保留 `open_questions`；不会无限尝试，也不创建隐式 Agent 子任务。新的补充证据追加到本 Run 的最终 `research_inputs`，保留初始 Evidence Package 原快照。历史回溯不得使用 `as_of` 后公开的新资料伪装当时已知的事实。

**输出校验：** 仅校验结构、合法的 `kind` 与 `importance`、引用 ID/版本是否存在且可获知、引用对应输入或经登记的补证、工具权限及必需字段。不能用“文字长短”“情绪方向”“模型自评可信度”等硬编码规则替代模型的研究判断。非法引用、结构错误可以有限重试/返回步骤错误；不允许为了通过校验静默生成虚构事实。

## 分析、独立验证与研究结果

分析结果以结构化记录优先，Markdown 报告为其展现形式。单条 Finding 至少包含：claim、kind（fact/inference/opinion 等）、verification_status、支持和反面 evidence_refs、关键假设、时间边界与来源说明。

每次 Analysis 保存 Task Run、Agent/模型、实际提示词版本或哈希、Evidence Package/最终 `research_inputs` 引用、输入文档与金融数值版本引用、输出 Finding、生成时间和用量。Report 引用 Analysis，可被其他研究任务检索，而不是把报告正文当作无法拆分的唯一数据。

**综合报告契约：** 报告应明确本次**新增情报的增量窗口**、研究 Scope 与知识截止时点，同时允许引用该窗口之外的历史财报、政策、既有研究作为背景。综合呈现市场情绪及其指标、重大财经资讯、宏观/行业发展、政策法规变化、重点公司与金融数据、与上次研究相比的新变化、存在分歧的证据以及尚未证实的风险。重点是跨来源综合判断，而不是按每篇新闻逐条复述。报告可根据策略只选择其中适用的维度，但必须保存输入数据/资料的范围、采集新鲜度、关键来源引用和明确的不确定性。如果某维度数据缺失，应标为未覆盖或资料不足，不能默认为没有变化。

市场情绪相关结论必须区分可观察指标、媒体/机构观点和模型的综合推断；样本不足或同源转载较多时不能宣称市场整体共识。研究任务可引用历史报告对比判断变化，不影响持续采集任务。


### Analysis、Finding 与历史研究关系

**Evidence 是长期共享的来源资产，Finding 是某次研究提出的一条固定主张，Report 是从本次研究结果组织的阅读产物。** 一份资料可以支持多个 Finding，一条 Finding 可以被多个报告和后续研究引用；三者都不互相覆盖。

**Analysis / Finding 的基本契约：**

| 字段 | 语义 |
| --- | --- |
| `analysis_id`、`run_id`、`evidence_package_ref`、`research_inputs_ref` | 一次研究的身份及完整取材链 |
| `summary`、`open_questions[]`、`coverage_ref` | 综合判断、尚未解决的问题与资料覆盖情况 |
| `finding_id`、`claim`、`kind` | 不可变 Finding；`kind = fact / inference / opinion` 表示主张性质，**fact 并不自动表示已核实** |
| `evidence_refs[]`、`counter_evidence_refs[]` | 支持与相反的证据引用（含具体数据/Chunk 版本）；不能只给来源名称而没有可查证引用 |
| `assumptions[]`、`uncertainties[]`、`time_context` | 推断前提、尚未确认的因素和适用时间 |
| `testable_hypothesis?`、`expected_signal?`、`next_check_condition?`、`invalidating_evidence?` | 可选的可验证研究假设、观察信号、下一次检查条件与潜在反证；不是自动交易或研究任务触发规则 |
| `importance`、`importance_reason` | 在本 Run 中的展示优先级：`key / supporting / monitor`；说明与 Objective、实际变化或未解决风险的关系，**不是可信度或投资评分** |
| `verification_status` | 按[独立核查与状态聚合](research.md)独立核查记录派生；初始为 `unverified`，不由 Researcher 直接确定 |
| `finding_relations[]` | 本次 Finding 与一个或多个历史 Finding 的具体关系、原因及引用的新证据 |

**可持续验证的研究假设（复用 Finding，不建立独立 Thesis 系统）：** 研究 Agent 或 Owner 可为适合长期跟踪的 Finding 附上可检验的指标/条件、支持和反驳信号以及预期复查时点或事件。后续 Research Task 读取原 Finding 和新证据，判断原假设被支持、需要修正还是受到挑战，并按下述关系保存新 Finding；原版保留。观察条件不是已被证明的事实，也不会自动触发交易。只有显式启用的 Task 才能按事件开始新的研究，默认采集不会因为假设存在而启动逐篇分析。

**历史关系类型：**

| `relation_type` | 含义 |
| --- | --- |
| `reinforces` | 本次新证据进一步支持历史判断 |
| `refines` | 本次证据使此前判断的条件、范围或表述需要补充/修正 |
| `challenges` | 新证据对历史判断提出挑战；**不等于已证明原判断错误** |
| `supersedes` | 在本次明确指定的研究语境中用新的 Finding 取代旧的判断；不删除旧记录，也不更改旧记录的事实核查状态 |

`new` 是没有历史关联时的展示分类，**不是必须落库的一种关系**。`finding_relations` 至少保存 `from_finding_id`（当前）、`to_finding_id`（历史）、`relation_type`、`reason`、`evidence_refs`、`created_at`；研究时点与 Scope 可以由关联 Analysis 获取。仅允许指向当时已存在、对当前 Task 可访问的历史 Finding，不允许自引用、无依据的关联或跨时间反向引用；同一组关系幂等。

关系只表达**新的研究如何理解旧的研究**，不直接变更历史 Finding 或其 Verification 记录。某个历史 Finding 本次未被引用不意味着已失效；上一份报告也不因为出现 `supersedes` 就被删除。真正的核查事实更正仍按[独立核查与状态聚合](research.md)的独立核查修正与历史快照规则处理。

**研究质量约束：**

- Agent 根据 Objective 与证据决定哪些 Finding 值得标为 `key`；每个关键判断都必须有重要性理由与证据/前提。不能要求固定生成 N 条“重点发现”或用关键词计数判断重要性。
- `supporting` 是帮助解释核心判断的材料；`monitor` 是影响还不明确、值得后续跟踪的问题。三种等级描述报告中的用途，不表示主张真实性、概率或交易信号。
- 允许 `findings=[]`，并明确输出“本次未发现足以改变既有主要判断的新增证据”，前提是取材覆盖满足任务成功条件；若部分关键来源缺失，则只能说明资料覆盖不足，不能宣称没有重要变化。
- 关键数值/政策事实核查优先，观点和影响预测可以保留未核查性质及前提。研究过程与 Researcher 输出不向 `supported` 自动升级。
- **区别事实、估值和表述变化**：两次研究比较时，先区分基本面/宏观事实变化、股价或估值变化、历史数据版本或会计口径修订，以及单纯的文字重写。只有可引用的实质新证据才建立 `refines / challenges / supersedes`；不同措辞、涨跌或媒体数量变化本身不构成基本面逆转。异动原因不明确时可以明确写“原因未确认”，不能为了完整叙事杜撰精确贡献比例。

~~~yaml
analysis:
  run_id: run_001
  evidence_package_ref: package_001
  summary: "本次研究判断示例，非真实市场结论"
  findings:
    - finding_id: finding_101
      claim: "某项新政策修改了特定产品限制范围"
      kind: fact
      importance: key
      importance_reason: "改变了此前研究使用的政策前提"
      evidence_refs: [doc_101]
      counter_evidence_refs: []
      assumptions: []
      uncertainties: []
      finding_relations:
        - to_finding_id: finding_082
          relation_type: refines
          reason: "新文件补充了此前尚未明确的适用范围"
          evidence_refs: [doc_101]
    - finding_id: finding_102
      claim: "若限制适用于目标产品，相关企业可能面临经营压力"
      kind: inference
      importance: monitor
      importance_reason: "影响路径值得追踪，但企业敞口尚未核实"
      evidence_refs: [doc_101]
      counter_evidence_refs: []
      assumptions: ["相关产品确实落在限制范围内"]
      uncertainties: ["公司受影响收入尚不明确"]
      finding_relations: []
  open_questions:
    - question: "具体业务敞口是多少？"
      evidence_gap: company_disclosure
  coverage_ref: package_001
~~~

此例为**结构化输出示意**。真实 `finding_id` 由 Stocks 分配或校验，研究 Agent 可以在单次模型响应中输出所有 Finding，不要求为每个判断单独调用 Agent。

### 独立核查、记录契约与状态聚合

**Finding 是不可被核查步骤静默改写的研究主张。** Researcher 保存主张及支持引用后，初始 `verification_status = unverified`。Researcher 自评、模型置信分数、同一文本的重复生成都不能直接升为 `supported`。

**报告生成顺序为：Researcher → 持久化 Findings → 关键事实核查（若 Task 启用）→ Report Renderer。** `repository.verify_key_findings` 表示现有 Task 中执行 Verifier 的一步，而非 Repository 自己生成研究结论或一个新服务。任务可以选择只核查重要事实、数值、政策条款；其他 Finding 保留 `unverified`。被选择的必需核查若失败，不能假装成功或把相关 Finding 标为 `supported`；按 Task 成功条件选择任务失败，或明确生成包含未核查说明的报告。核查范围和失败处理在执行快照中保存。

Verifier 与 Researcher 为不同执行步骤，拥有独立的任务输入和结果记录。Verifier 检查的不是“另一个模型是否赞同”，而是：主张是否与原始资料一致、数值/单位/期间是否正确、证券身份是否匹配、引用是否被断章取义、引用来源是否真正独立、是否存在反面证据或前视偏差。可确定的数学与财务核对优先使用代码计算，并复用[结构化金融数据与时间语义](data.md)的金融质量检查结果；必要时回查原始文件和不同来源，区分真正的数值错误与口径不同。金融数据质量状态不直接等同于 Finding 的核查判决。优先逐项验证影响主要结论的数字、政策日期和关键事实；其他数据可按 Task 要求进行可复现抽检，保存抽样规则、样本数量、未核查项目和覆盖范围，**抽检通过并不证明整份报告的所有事实都正确**。可按任务选择相同或不同模型，**不强制每项研究调用两种模型**。

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
