# 研究取材与分析报告

本篇负责 Evidence Selection、Research Agent、Analysis/Report 与证据追溯；数据见 [数据设计](data.md)，执行见 [执行设计](execution.md)，界面见 [界面设计](interface.md)。返回 [架构总览](architecture.md)。

## 统一取材：Evidence Selection 与 Evidence Package

**一套取材逻辑服务所有研究范围，不按市场/行业/个股分别建设取材引擎。Repository First 是默认取材规则**：同时查询已保存的 Document 原文与分类、关联 Fact、历史分析；结构化 API 数据也是由原始响应 Document 衍生的 Fact。检索结果在同一 Evidence Selection 中合并关联并去重，不能把 Fact 和其来源 Document 当成两份独立证据。已有数据满足时不重复调用外部 API。`repository.select_evidence` 是现有 Task 中可配置的 Repository 步骤：先寻找本次可研究的新增变化，再查询必要历史背景，整理可复查的 Evidence Package 交给 Research Agent。这里的 “Evidence Selection” 表示执行能力，“Evidence Package” 表示本次结果快照；**都不代表新微服务、全局 Agent 或持久化材料副本**。

### 取材请求与研究维度

| 输入 | 语义 |
| --- | --- |
| `run_id`、`task_id`、`task_version` | 研究所属 Task Run 与配置快照 |
| `objective` | 本次需要回答的研究问题，不限于预先固定的日报/行业/股票类型 |
| `scope` | YAML 设置的公司名称、股票代码、市场/地域、行业、宏观与政策主题的适用组合，不依赖内部证券 ID |
| `window_start` / `window_end`、`as_of` | 仅前者界定新增变化；`as_of` 约束所有事实和版本可获知性，沿用[研究窗口与任务示例](execution.md) |
| `data_requirements[]`（可选） | 研究关注的证据类型或主题维度，例如政策、宏观指标、企业披露、行业信息、历史观点 |
| `budget`（可选） | 证据 Token 总预算、候选扫描上限、每类内容/维度的取材上限等有界限制；未配置使用可调整的默认值 |
| `selection_policy_version` | 本次取材规则或配置版本，便于解释不同运行的差异 |

- 已显式配置的 `data_requirements` 作为研究偏好；缺省时，使用 Scope 的结构化标签与 Objective 相关的关键词/主题词表生成默认维度和检索条件。必要时允许由已配置的低成本 Decision Agent 提出补充维度，但其结果须经受控词表/查询参数校验，不能修改 Task 权限、时间截止或预算。**不为市场日报、行业报告和个股报告分别硬编码固定取材数量或查询流程。**
- **直接提及与背景相关分开**：研究 NVDA 时，原文明示 NVIDIA/NVDA 的公司公告、财务资料是直接候选；行业政策、供应链和宏观资料可作为背景候选，即使没有出现 NVDA 字样。背景关联不得被改写成“该政策直接点名 NVDA”，有歧义的名称/代码保留不确定性。
- 一个维度可以在本次无适用资料；必须表示“没有找到/未能获取/尚未扫描完整”中的实际情况，不能为满足取材额度制造内容或将其他维度的资料强行改类。
- **检查信息覆盖偏差**：热门大公司、英文资料与成熟行业通常有更多报道；数量多不代表更具投资价值、证据更独立或研究更确定。取材记录一手资料可得性、指标历史长度、独立原始来源分布、语言/地区覆盖及关键数据缺口；不凭新闻条数自动对公司评级，也不因资料少认定其基本面差。
- **原始资料与反向观点并重**：关键财务/监管事实优先获取一手申报、公司公告与监管文件；二手观点也可用来寻找解释和反面证据，但保留源头关系，不能只采纳支持既有结论的新闻。

### 候选获取、去重、排序与预算

按以下顺序执行，全部为现有 Repository、Worker 和 Task 能实现的操作：

1. **新增集合**：按 `window` 与 `as_of` 调用 `search_changes`，同时寻找本次新入库的 Document、Fact 及有意义的修订；按稳定引用和变化类型去重。旧资料晚到归入本次增量，仍保留真实 `published_at`、来源明确给出的 `source_updated_at`。
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
| `previous_analysis_refs[]` | 可复用的历史 Analysis 或报告引用；允许为空，不要求逐条 Finding 关系 |
| `coverage` | 取材/来源缺口、未扫描候选、分类积压等；缺口不是“没有发生变化”的证据 |
| `allowed_tools[]`、`budget` | 由可信 Task 配置授权的 Repository/远程工具与有限调用/Token 预算，模型不能扩权 |

**研究行为：**

1. 从本次新增变化入手，结合历史资料识别与 Objective 相关的事实、指标修订、事件与政策变化，不逐篇按新闻顺序复述。区分“新公开的旧事实”“既有指标的修订”“当前发生的现实变化”和“不同来源的观点”。
2. 根据相关性、变化程度、可能的影响路径、证据强弱、历史研究差异和待解决问题，判断哪些内容对**本次研究**重要。重要不等于利好、利空或确定性；没有显著新变化也是有效结论，不能为凑栏目发明新观点。
3. 检查 Evidence Package 的关键缺口，主动追问重要判断最可能错在哪里、哪些证据可以推翻它、是否存在其他合理解释；必要时用已授权 Repository 只读 Tool 进行少量反证与背景补充检索。找不到反证不意味着已证明正确；缺少来源、反面证据或指标时可带不确定性结束，不能捏造数据。
4. 将研究结果表示为统一的 Analysis：简要综合判断、`findings[]`、`open_questions[]`、引用证据和覆盖范围；向报告层提供可阅读的段落建议，但不把 Markdown 作为唯一资产。
5. 可以引用历史报告比较变化，但不需要逐条 Finding 历史关系表；任何比较都以真正的来源变化为依据。

**补充检索调用与终止条件：** Agent 只能提出已授权工具中的结构化请求，说明 `purpose`、允许的过滤条件及需要查证的问题；系统强制固定 `as_of`、超时、调用次数、Token 预算及来源登记。对同一检索无进展时停止，保留 `open_questions`；不会无限尝试，也不创建隐式 Agent 子任务。新的补充证据追加到本 Run 的最终 `research_inputs`，保留初始 Evidence Package 原快照。历史回溯不得使用 `as_of` 后公开的新资料伪装当时已知的事实。

**输出校验：** 只检查所需结构、证据引用存在性与时效、调用权限和必要字段；不用文字长短、情绪方向或模型自评分数硬编码研究判断。非法引用或结构错误允许有限重试，不得静默编造事实。

## Analysis 与 Report

Research Agent 从去重后的 Evidence Package 形成 Analysis，再生成 Report。**不要求逐条 Finding 主数据、长期 Finding 关系图、独立 Verifier、核查状态机或多轮聚合算法**。

**Analysis 保存：** 本次 Run、目标与范围、`as_of`、增量窗口、实际 Document/Fact 证据引用及资料缺口；模型分析形成的摘要、主要变化、数据、理由、风险、不同解释和不确定性；按需附 `findings[]`、历史报告引用、模型与提示词信息。

**Report 保存：** 标题、研究范围、时间、可阅读正文/章节、真实数据引用和必要的来源/版本。市场、行业和个股共用报告契约，不强制固定栏目；无显著变化时可以如实说明。将原始数据、媒体观点与模型推断明确区分，资料缺失或冲突时直接说明，不能自动宣称“已核实”。

报告在成功保存后可被后续 Task 参考，失败不覆盖最后成功报告；简单的历史报告对照足以满足首版需求，不为未来的复杂关系提前建表或设计 UI。图表如需生成，数字必须来自 Repository 实际记录，而非 LLM 虚构。
