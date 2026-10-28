# 研究取材与分析报告

本篇负责 Evidence Selection、Research Agent、Analysis/Report 与证据追溯；数据见 [数据设计](data.md)，执行见 [执行设计](execution.md)，界面见 [界面设计](interface.md)。返回 [架构总览](architecture.md)。

**阶段边界：** V0 从当前已保存的 Document Chunk 中手动取材，保存实际使用的证据包、Analysis 和可追溯 Report；说明来源或索引缺口，不声称完成增量扫描。V1 增加 `search_changes`、Fact 与 Document 合并去重、扫描进度及定时研究。V2 增加授权外部补证、跨来源独立性和冲突处理、历史比较。下文描述完整目标契约，不将 V1/V2 细节作为 V0 验收门槛。

## 统一取材：Evidence Selection 与 Evidence Package

V0 只对本次手动查询实际看到的 Document Chunk 形成 Evidence Package：记录研究目标、范围、取材时间、被选片段的 Document 版本、选择理由、查询条件及已知来源/索引缺口。候选未穷尽时说明截断，不填写虚构总数。V0 不使用 `changes_window`、Fact 引用或“已扫描完上次以来全部变化”的状态；以下完整候选扫描与合并规则从 V1 起逐步实现。

**一套取材逻辑服务所有研究范围，不按市场/行业/个股分别建设取材引擎。Repository First 是默认取材规则**：V0 查询已保存的 Document 片段；V1 起合并分类、Fact 和历史分析，结构化 API 数据也是由原始响应 Document 衍生的 Fact。V1 的检索结果在同一 Evidence Selection 中合并关联并去重，不能把 Fact 和其来源 Document 当成两份独立证据。已有数据满足时不重复调用外部 API。`repository.select_evidence` 是 Task 中可配置的 Repository 步骤：V0 整理当前已保存资料，V1 先寻找本次可研究的新增变化，再查询必要历史背景，整理可复查的 Evidence Package 交给 Research Agent。这里的 “Evidence Selection” 表示执行能力，“Evidence Package” 表示本次结果快照；**都不代表新微服务、全局 Agent 或持久化材料副本**。

取材默认读取已处理的 Chunk、V1 起可用的 Fact 和元数据，遵循[检索路径](data.md#检索)；需要上下文时读取同一 Document 的相邻片段，不在取材请求中遍历原始文件、临时解析全库或重建文档向量。原件用于指定证据的追溯，需要解析或索引但尚未完成的资料按实际状态计入覆盖缺口；V1 起纯数值响应等不适用向量索引的资料通过 Fact 查询取材，不因此计为索引积压。

### 取材请求与研究维度

| 输入 | 语义 |
| --- | --- |
| `run_id`、`task_id`、`task_version` | 研究所属 Task Run 与配置快照 |
| `objective` | 本次需要回答的研究问题，不限于预先固定的日报/行业/股票类型 |
| `scope` | YAML 设置的公司名称、股票代码、市场/地域、行业、宏观与政策主题的适用组合，不依赖内部证券 ID |
| `as_of`；V1 起增加变化水位及 `window_start` / `window_end` | `as_of` 是研究参考时间；V1 变化水位决定例行增量范围，窗口时间用于展示；V2 运行中可按需补证，沿用[时间与窗口规则](execution.md#研究增量窗口与共享资料引用) |
| `data_requirements[]`（可选） | 研究关注的证据类型或主题维度，例如政策、宏观指标、企业披露、行业信息、历史观点 |
| `budget`（可选） | 证据 Token 总预算、候选扫描上限、每类内容/维度的取材上限等有界限制；未配置使用可调整的默认值 |
| `selection_policy_version` | 本次取材规则或配置版本，便于解释不同运行的差异 |

- 已显式配置的 `data_requirements` 作为研究偏好；缺省时，使用 Scope 的结构化标签与 Objective 相关的关键词/主题词表生成默认维度和检索条件。必要时允许由已配置的低成本 Decision Agent 提出补充维度，但其结果须经受控词表/查询参数校验，不能修改 Task 权限、研究参考时间、增量窗口或预算。**不为市场日报、行业报告和个股报告分别硬编码固定取材数量或查询流程。**
- **直接提及与背景相关分开**：研究 NVDA 时，原文明示 NVIDIA/NVDA 的公司公告、财务资料是直接候选；行业政策、供应链和宏观资料可作为背景候选，即使没有出现 NVDA 字样。Scope 是研究范围，不能机械地将公司、股票、行业、主题全部作为每条查询必须同时满足的硬过滤；具体查询分别寻找直接资料与相关背景，仍服务同一 `run.scope`。背景关联不得被改写成“该政策直接点名 NVDA”，有歧义的名称/代码保留不确定性。
- **两类真实报告口径（2026-10-27 Owner 确定，配置见 `config/tasks.yaml`）**：`market_intelligence_brief` 是情报更新型市场简报，scope 不含 companies/symbols（无单一可命名主体，全部显式查询都是 direct 角色），objective 要求区分本次窗口新增与历史背景、宏观 Fact 带观测期呈现（不得当作研究时点）、行情缺失时如实呈现缺口；`spacex_company_research`（SpaceX，Nasdaq SPCX，CIK 0001181412）首条查询显式含公司名与代码（direct），行业/政策查询为背景角色，公司结论要求原文明确指称公司或 SPCX，不得凭航天/行业标签推断， filings 与行情来源未接入时缺失计为覆盖缺口。交易日复盘口径未启用；两者复用同一 Evidence Selection 与 Research Agent 流程，不另建引擎。
- 一个维度可以在本次无适用资料；必须表示“没有找到/未能获取/尚未扫描完整”中的实际情况，不能为满足取材额度制造内容或将其他维度的资料强行改类。
- **检查信息覆盖偏差**：热门大公司、英文资料与成熟行业通常有更多报道；数量多不代表更具投资价值、证据更独立或研究更确定。取材记录一手资料可得性、指标历史长度、独立原始来源分布、语言/地区覆盖及关键数据缺口；不凭新闻条数自动对公司评级，也不因资料少认定其基本面差。
- **原始资料与反向观点并重**：关键财务/监管事实优先获取一手申报、公司公告与监管文件；二手观点也可用来寻找解释和反面证据，但保留源头关系，不能只采纳支持既有结论的新闻。

### 候选获取、去重、排序与预算

取材设计按以下顺序组织，职责均归于 Repository、Worker 和 Task，当前实现状态见[运行说明](quickstart.md)：

1. **新增集合**：按 `window` 调用 `search_changes`，寻找本次新入库、完成处理后新可用的 Document/Fact 及有意义的修订；按稳定引用和变化类型去重。`as_of` 是参考时间，不附加“所有输入必须在启动前已取得”的限制。旧资料晚到归入本次增量，仍保留真实 `published_at`、来源明确给出的 `source_updated_at`。
2. **背景集合**：按 Scope + Objective 使用 `search`，通过 pgvector 文本语义召回加元数据过滤、Fact 结构化查询，读取适用的历史 Document/Fact 和已有 Analysis，不受增量窗口限制。区分 `role = new_information / historical_context`；同一资料可被不同 Run 复用；向量索引失败或积压需计入覆盖缺口。
3. **统一合并与去重**（不区分来源是 RSS、API 还是研究过程中的外部搜索；仅影响本次研究输入，不删除原始存储）：
   - **Document + Fact 同时命中**：以 Fact 的结构化字段表达该项已整理事实，并关联其来源 Document 的原文/响应位置；原文仍可补充该 Fact 未覆盖的其他信息，**不能整篇排除**。同一 Fact 经片段检索与结构化两条查询路径返回时只出现一次。
   - **重复原文/转载**：相同来源的重复抓取由入库幂等处理；已确认内容相同或转载同一原始出处的不同 Document 合为一组证据，保留各份来源及链接。仅靠向量相似、主题或标题相同不得认定为重复。
   - **同指标跨来源**：指标、主体、统计期间、单位与口径一致且数值一致时，可合并为一组事实并保留各来源引用；**数值冲突、版本不同或口径不同必须并列保留，不能平均或静默覆盖**。
   - **独立观点与其他事实**：同一事件下独立观点、反证和同一 Document 中其他不重叠的事实继续保留；仅在已确认同一源头时按一份独立证据计数。
   - **增量与背景重复**：同一事实同时进入 `search_changes` 和 `search` 时统一复用其引用，不重复加入研究输入；原始候选计数、合并后证据组数分别记录。
4. **候选优先级**：按研究目标相关性、增量价值、事件/数据修订、来源、观点差异和时间适用性排序，使用标签、pgvector 语义召回及 Fact 结构化查询，不引入复杂打分系统。**取材裁剪只作用于研究输入，不用于限制已配置来源采集。**
5. **维度覆盖与裁剪**：从本次需要的研究维度中分配软预算，优先覆盖核心问题、重大新变化、关键事实及重要反面证据，避免某个高频新闻主题或同一转载来源耗尽全部输入；在严格的总 Token/候选扫描预算内选择可追溯的代表性文档片段、完整数值点/序列引用及历史结论。维度预算按 Task 可调，空维度不强制填充。裁剪的是**本次模型输入**，不删除 Repository 数据。
6. **引用与快照**：取材结果保存唯一 ID、来源、版本、选入原因、原始证据链、确定性排序/规则版本、实际采用的正文片段/数值引用、各维度计数和截断原因。研究 Agent 收到这个快照，而不是任意数量的全文。

**去重后输出必须保留每组证据对应的全部原始 Document/Fact 引用，报告不能将同一来源的 Document 与它的 Fact 计为两条独立支持证据。** 未参与事实抽取的 Document 依然可进入候选；索引、分类不确定或失败不得等同于不存在事实。

**预算与覆盖统计必须区分三个数量：** `candidates_discovered`（查询实际发现的候选）、`candidates_screened`（在本次规则下已完成筛选）、`selected_count`（入选 Evidence Package）。还要记录 `unexamined_count`、查询是否分页/触顶 `retrieval_truncated`，以及各来源的可用性；**扫描上限达到时不能把“只扫描了部分候选”标为全部已覆盖**。候选多但完成筛选后未入选，属于有意取舍；根本没有扫描/来源不可用属于覆盖缺口，必须分别说明。包内的 `selected_count` 不应等同于独立证据组数量，转载组合并需单独统计。

统计只覆盖实际执行的查询。无法确定剩余数量时，`unexamined_count` 留空并说明未知，不能填 0，也不为得到一个精确总数而全库解析或穷举；`retrieval_truncated` 仍反映实际触顶或中断情况。

扫描触顶或中断后保留查询及分页进度，后续继续扫描；进度保存与游标推进遵循[执行设计](execution.md#研究增量窗口与共享资料引用)。原始资料长期保留，已选或未选资料均可被后续研究复用，不设置全局“已消费”状态。

### Evidence Package 数据契约

下例展示 V1 的完整形态；V0 包可省略 `changes_window`、Fact 和增量扫描字段，但必须保存实际使用的 Document 版本、Chunk 引用与已知覆盖缺口。

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
    change_watermark_start: 120
    change_watermark_end: 183
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

**Evidence Package 是本次例行选择的只读快照，不是本 Run 的全部输入。** V2 授权补证采用后追加到 Run 的 `research_inputs`（Analysis 保存采用时的冻结副本），字段与时间关系以[执行设计](execution.md#研究增量窗口与共享资料引用)为准；初始包的 `selected_chunks` / `evidence_groups` / `coverage` 永不被追加输入改写，Agent 输入、引用白名单、报告详情与单条证据接口共用“初始选择 + 已追加输入”的同一合并视图。

V0 当前实现使用每次 Run 一份持久 Evidence Package：保存 objective、`run.scope`、`as_of`、规则版本、预算、每条实际 Query 及角色（`direct | background`）、所选 Chunk 的 Document/parse batch 版本与原始位置、选择理由、索引覆盖和缺口。有界语义查询无法得知候选总数时，`candidates_discovered` 与 `unexamined_count` 为 `null`；每条 Query 单独记录返回数、截断和缺口。直接资料与背景资料分开查询及标记，不要求 Scope 全部维度同时 AND。包通过持久 Chunk 引用读取正文，不复制整篇原文。

**V1-06 当前实现：** 同一 Evidence Package 现在同时收录本次固定变化窗口内新可研究的 Document 与历史语义背景。`select_evidence` 先用 `stocks.changes.changed_candidates()` 按 Document 归并本页变化，再读取该 Document 当前成功解析批次的 Chunk 和已生成的 Fact，然后执行原有语义查询；同一 Document 的增量与背景命中合并为一个 `evidence_groups[]` 条目，同一个 Fact 无论经哪条路径出现都只保留一次。每组记录 `role`（`new_information` / `historical_context`）、`match_role`（`direct` / `background`）、`kinds`、`reasons`、`chunk_ids`、`fact_refs` 与增量水位；Reporting 仍以持久 Chunk 加 Document 版本作为引用单元，Fact 通过 `fact_refs` 关联回其来源 Chunk 或 JSON `evidence_ref`，因此 Document 与其 Fact 不会被计成两条独立支持。仅凭标题或向量相似不做转载合并，属于后续能力。

统计同时记录 `candidates_discovered`、`candidates_screened`、`selected_count`（实际选入的 Chunk 数）、`evidence_group_count`（合并后的证据链组数）、`filtered_out_count`（已筛选但未入选）与 `unexamined_count`（未扫描）。筛选后未入选与未扫描分开表达；任一查询或原始变化页触顶时 `candidates_discovered` / `unexamined_count` 保持 `null`，不填 0。`selected_count` 与 `evidence_group_count` 不同正是转载/合并后的结果，报告不能把组数当作独立来源数。分类失败不参与召回过滤：只要存在可读当前 Chunk 或结构化 Fact，资料仍可进入研究。

纯结构化 API 响应（无可用 Chunk）只以 Fact 组出现，`ref` 为 `null` 并保留每个 Fact 的 `evidence_ref`。仅凭标题或向量相似不做转载合并；V2-03 起按来源自己声明的原件合并，见下；基于已存快照的历史报告比较由 V2-04 实现，同样见下。

**V2-03 当前实现：** 采集时把来源声明的原件记在 Document 内容版本上（[来源声明](data.md#来源关联去重与政策资料)），取材阶段只用这个声明合并，不做相似度猜测。`document_origins()` 一次读回本次选中 Document 的声明，`group_origins()` 据此把多份资料归为一个 **origin group**：`coverage.origin_groups[]` 记录 `origin_id`、`kind`、`key`、`url`、`declared_sources`、`document_ids`、`source_ids`、`document_count`、固定的 `independent_source_count = 1` 与 `merged`；每条 `selected_chunks[]` 与 `evidence_groups[]` 另带 `origin_id`（组内还有 `origin_group_size`），所以报告和证据详情既能说明「这是同一原件」，也仍能逐份打开各副本。覆盖统计新增 `origin_group_count` 与 `merged_origin_group_count`：`selected_count`、`evidence_group_count`、`origin_group_count` 是三个不同数量，报告不能用前两个冒充独立来源数。**没有声明的资料记为 `kind = single_document`、`key = document:<id>`，表示来源关系未知，不等于已确认唯一。**

**同批数据内部闭环（V1-06 当前实现补充）：** 历史语义入选的 Document 与增量路径同样携带其已保存 Fact——`select_evidence` 在语义查询轮内对新增入选 Document 用 `stocks.facts.facts_for_documents()` 有界批量加载，经同一去重与冲突标记投影后附入 `evidence_groups[].fact_refs`；同一 Fact 经两条路径命中只计一次，无 Fact 的文档照常入选，被 token 预算淘汰的命中不附加 Fact。

**同指标比较（V2-03）：** Evidence Package 另存 `metric_comparisons[]`。同一 `(subject_name, symbol, metric_id)` 出现两个及以上 Fact 时生成一条对比，分别标记 `value_conflict`（同一 unit/currency/观测期下取值不同）、`basis_mismatch`（单位或币种不同）与 `period_mismatch`（观测期不同），并逐个列出各来源的 `value`、`unit`、`currency`、`period_start`、`period_end`、`available_at`、`statement` 与 `document_id`。同一个 Fact 在 `evidence_groups[].fact_refs` 中带上 `comparison_id` 与三个标记，模型输入与报告看到的是并列值本身。**不做平均、不做静默覆盖、不把冲突或未确认值标为已核实**；只有一条 Fact 的指标不生成对比，也不因此被判定为「已核实」。

**补证 Fact 的最终视图（V2-01/V2-03）：** 初始 `evidence_groups[].fact_refs` 与本次 Run 实际采用的补证输入 `research_inputs[].fact_refs` 都保存完整冻结 Fact 投影。研究 Agent 的模型输入、确定性同指标比较、Report 与报告 API 使用同一个合并视图：按 `fact_id` 去重，初始组在前、追加输入在后，因此同一 Document 被两条路径命中只计一次，而该 Document 上新增的 Fact 仍会补入。每个 Document 的 Facts 只随它第一个可读 Chunk 出现一次；只有 Fact、没有可读 Chunk 的补证组仍只以结构化证据存在，不能作为 finding 引用。比较使用冻结投影中的值、单位、期间与可获知时间，不重新读取当前 Fact 行，也不改写初始 `Evidence Package.metric_comparisons[]`；报告 API 另返回由该视图计算的 `final_metric_comparisons[]`，`metric_comparisons[]` 继续只表示初始选择。

**报告呈现（V2-03）：** `render_report()` 在「资料覆盖限制」之前确定性生成 `## 附录：来源独立性与指标口径`：先写本次选中的独立原件数与覆盖的 Document 版本数、列出确认同源的合并组，再逐个列出同指标取值与差异类型，并明确说明这些值按发布原样并列、未平均、未标记已核实。该段落来自已保存的 Evidence Package 与 Analysis.coverage，不依赖模型输出；模型只负责解释影响与不确定性。段落在 20 个 origin group / 20 条对比处截断，其余仍保存在 Evidence Package 中。

**历史报告比较（V2-04）：** `GET /api/reports/{report_id}/comparison?other_report_id=...` 只比较两份**已保存**报告各自的快照：Analysis 的 findings / open questions、Evidence Package 的 `scope`、`as_of` 与 coverage，以及各报告真实引用的 Document 版本与 Fact。比较集合由该报告已保存的 Analysis/Findings 引用确定，并与它自己的冻结输入相交；入选但未被引用的材料、或同 Document 上未被引用的其它 Fact 不进入比较。Document 身份与 Fact 原值来自冻结引用投影，不读取当前 Fact 行，也不因来源后来重处理、出新版本或新增 Fact 而改写旧侧。它**不重新研究、不调用 Tool、不读取当前最新数据、不改写旧报告**，响应 `notes[]` 也把这个边界写明。两侧按 `created_at`、`report_id` 排序，`ordering.requested_report_id` 与 `ordering.swapped` 说明请求的报告是否就是较早一侧；失败或未完成的 Run 不能充当成功历史报告（409），同一报告自比返回 400，未知报告返回 404。

- **结论差异**：只有 `kind` 与规范化 `statement` **完全相同**的 finding 才算持续，改写过的条目按「一条消失、一条新增」处理；持续项标记 `uncertainty_changed`、`rationale_changed`、`evidence_changed` 并给出两侧的 uncertainty。比较只说明差异，不把后来的结论说成较早 Run 已经知道的内容。
- **证据差异**：Document 以来源自己声明的原件（V2-03 的 `source_id` + `dedupe_key`）为键，来源没有声明时退回 `document:<id>`；同一 Document 被多段引用只出现一次，标出 `version_changed` 与 `content_changed`，并分别列出两侧可打开的 Document 版本。Fact 差异按 `(subject_name, symbol, metric_id)` 分组，标出 `value_changed`、`basis_changed`、`period_changed` 并给出两侧的真实取值、单位、币种与观测期；**不平均、不覆盖、不标为已核实**。
- **覆盖与晚到资料**：两侧分别给出 `selected_count`、`evidence_group_count`、`origin_group_count`、`merged_origin_group_count`、`fact_count`、候选统计、`research_input_count` 与缺口，并给出 `gaps_added` / `gaps_removed`；`unexamined_count` 只从 Evidence Package 读取，未知保持未知、不补 0。`late_material` 列出晚到一侧在较早报告 `as_of` 之后发布或采集的 Document，把差异解释为真实来源变化。两侧 Task 或 Scope 不同、或较早 `as_of` 未推进时给出 `warnings[]`。
- **边界**：每组差异最多返回 50 条并带 `truncated`，完整快照仍在各自报告与 Evidence Package 中；不建 Finding 主数据或长期关系图，市场、行业与个股共用同一接口。

**V2-01 起的可引用边界：** 补充检索取得的资料只有在统一入库、解析并成为持久 Chunk 后才能支撑 finding；Fact-only 的补充组（例如纯 JSON 响应）仍进入证据组与覆盖统计，但不能作为引用对象，相关内容只能以 open questions 或覆盖说明表达。该限制对初始选择与补充输入一视同仁。

### Agent 补充检索与故障边界

本节授权外部补证从 V2 起适用；V0/V1 的研究只使用已保存的 Repository 资料。

- 研究 Agent **先**通过 Repository 的只读检索补齐问题；已有可用材料不能因为方便而默认重查外部。资料不足、缺失、遗漏、不够新、来源冲突或确需交叉核实时，可在 Task 明确授权下调用外部 Tool 查询额外资料。实际采用的原文或结构化响应经统一 Source/Repository 流程登记、留存原始证据与真实时间，生成可追溯引用，再按权限和预算约束加入本次 `research_inputs`；不得把新资料标记为 Document 的“已研究”状态，也不得覆盖原始证据包快照。
- 运行中取得的补充资料可以供本次分析使用，时间标示遵循[执行设计](execution.md#研究增量窗口与共享资料引用)。例如 08:00 启动研究、08:03 下载昨天的公告，保存原文、真实时间并完成本次所需处理后即可引用；运行中取得的新发布或更新内容也可使用，但不得伪装成参考时间之前已知的信息。
- 新补充的资料复用同一入库和后台处理步骤，在本次预算内取得所需片段或 Fact 后加入实际输入；只等待本次需要的文档，不要求清空全库积压。处理失败则保留原件与错误并说明缺口，不绕开证据保存流程直接使用临时工具输出。
- 取材失败与无候选不同：检索超时、分类积压、数据源断更、候选扫描不完或版本无法确定时，分别记录原因和受影响维度。允许生成明确说明覆盖不足的报告，不为补齐栏目捏造信息；失败、积压及继续扫描归于 Task/Run 和控制台管理，游标推进遵循[执行设计](execution.md#研究增量窗口与共享资料引用)。
- Evidence Package 在下游 Agent 失败时保持可复查，重试默认沿用已保存的输入、版本和选择规则。若仍需补证，沿用同一套授权工具与入库流程追加输入，并记录实际新增引用，不改写原包或既有报告。
- **冻结引用按保存身份读取，而不是按当前指针读取。** 初始选择与已追加输入记录的是当时的 Document 版本、Chunk 与 `parse_batch_id`；同一 Document 之后重新解析并切换当前批次时，这些已保存引用仍从其真实的不可变批次读取，不因批次已不是 current 而失效，也不被替换为新批次的片段。新 Run 的正常取材仍只选择当前成功批次，因此该放宽只适用于已冻结的运行输入。Chunk、Document 版本或 `parse_batch_id` 不匹配、批次未成功完成或记录已不存在时仍然失败，不回退当前批次，也不伪造替代证据。
- 同一 Task Run 无论研究市场、行业、股票或混合主题，都调用同一个 `repository.select_evidence`；不新增研究专属的采集器、独立 Evidence 服务、DAG 或复杂打分调度器。

**补证闸门与生命周期（V2-01）：**

1. **先查 Repository。** 已保存的增量与历史证据由 `select_evidence` 一次取齐；只有资料缺失、过期、存在冲突或需要交叉核实时才允许外部调用。已配置但本次不需要的 Tool 不会被调用，不得为了“更完整”例行重查外部来源。
2. **计划与执行分离。** 研究 Agent 只提出结构化补证请求（要解决的问题 + 有界参数），权限、预算、超时与来源登记由 Stocks 决定；请求的调用数、参数与去重规则见[执行设计](execution.md#tools-与远程-mcp)。
3. **先保存再引用。** 采用的响应走统一 Source/Repository 入库（`ingest_document()`），只对该 Document 运行 parse → classify → facts 所需阶段，成功后再追加 `research_inputs`；不等待全库积压清空，也不绕过入库直接使用临时输出。复用同内容 Document 时，已完成的阶段只跳过自身，继续检查后续所需阶段；解析未完成或不可用仍在此止步，不继续分类或 Fact。Fact 明确以 `NOT_APPLICABLE` 跳过不记为缺口，等待、依赖跳过或失败仍如实记录。
4. **发现与证据分开。** 搜索结果与摘要只是发现渠道，不能作为引用；只有取得可持久定位的 Chunk 才能支撑 finding。没有原文的补充结果记录为缺口或保留为 open question。
5. **不可改写。** 初始 Evidence Package 不被重写，已保存的旧 Analysis/Report 保持原引用与生成时间；重试沿用来源 Run 的初始包与已追加输入，只追加真正新取得的材料。重试读取这些冻结引用时不受“当前解析批次已变化”影响，但仍核对 Chunk、Document 版本与批次的真实归属。

**补证缺口与失败语义：** 补证不改变既有缺口分类——仍区分“已耗尽检索范围”“资料/来源不可用”“有界扫描被截断（剩余未知，不填零）”“已筛选未入选”“处理/索引失败”“部分内容”与“无报告（最终 Agent 或 Analysis 失败）”。补证新增的失败一律按显式缺口记录，至少覆盖以下类别：未授权/未知/停用的工具、参数非法、重复查询、预算耗尽、超时、传输或 RPC 失败、映射结果不合法、只有摘要没有可引用原文、计划不可用或计划不合法，以及补充文档的入库/解析/分类/Fact 失败。材料缺失绝不写成“事实不存在”，未运行的检索不写成“已确认无变化”；具体代码在归属实现中固定，本表不承诺未实现状态。

## Research Agent 的输入、行为与有界补证

Research Agent 设计为普通可配置的 Agent 角色，执行 Research Task 中的一个研究步骤；**不是独立的调度服务、固定的报告类型或额外的 Research Engine**。市场、行业、个股、跨主题研究统一使用相同的 ResearchContext 和输出契约，通过 Objective、Scope、可选 Strategy 与证据包体现差异。

**输入 ResearchContext：**

| 字段 | 契约 |
| --- | --- |
| `run_id`、`objective`、`scope`、`strategy_ref?` | 对应 Task Run 的不可变配置快照；研究什么、面向何种视角 |
| `as_of`；V1 起增加 `changes_window` | 研究参考时间及 V1 例行增量范围，含义以[执行设计](execution.md#研究增量窗口与共享资料引用)为准；V2 授权补证不受严格历史回放限制 |
| `evidence_package_ref` | 指向[统一研究取材](research.md)已保存的取材快照，不要求全文重复塞入 Prompt |
| `previous_analysis_refs[]` | 可复用的历史 Analysis 或报告引用；允许为空，不要求逐条 Finding 关系 |
| `coverage` | 取材/来源缺口、未扫描候选、分类积压等；缺口不是“没有发生变化”的证据 |
| `allowed_tools[]`、`budget` | 由可信 Task 配置授权的 Repository/远程工具与有限调用/Token 预算；模型只能引用这些 ID，不能新增工具、修改参数边界或提升预算 |
| `research_inputs[]` | 本 Run 实际采用的最终输入台账：初始 Evidence Package 边界条目 + 已追加的补充 Document 版本、Chunk/Fact 引用、Tool 调用身份与时间关系；只追加、不重写 |

**研究行为：**

1. 从本次新增变化入手，结合历史资料识别与 Objective 相关的事实、指标修订、事件与政策变化，不逐篇按新闻顺序复述。区分“新公开的旧事实”“既有指标的修订”“当前发生的现实变化”和“不同来源的观点”。
2. 根据相关性、变化程度、可能的影响路径、证据强弱、历史研究差异和待解决问题，判断哪些内容对**本次研究**重要。重要不等于利好、利空或确定性；没有显著新变化也是有效结论，不能为凑栏目发明新观点。
3. 检查 Evidence Package 的关键缺口，主动追问重要判断最可能错在哪里、哪些证据可以推翻它、是否存在其他合理解释；必要时用已授权 Repository 只读 Tool 进行少量反证与背景补充检索。找不到反证不意味着已证明正确；缺少来源、反面证据或指标时可带不确定性结束，不能捏造数据。
4. 将研究结果表示为统一的 Analysis：可独立阅读的文章标题、摘要与主题章节，以及关联的 `findings[]`、`open_questions[]`、引用证据和覆盖范围；文章与结构化发现由同一次生成完成，不把 Markdown 作为唯一资产。
5. 可以引用历史报告比较变化，但不需要逐条 Finding 历史关系表；任何比较都以真正的来源变化为依据。

**补充检索调用与终止条件：** Agent 只能提出已授权工具中的结构化请求，说明 `purpose`、允许的过滤条件及需要查证的问题；系统执行超时、调用次数、Token 预算及来源登记约束。未授权、未知、已停用或参数非法的请求在发出网络请求前即以显式缺口拒绝，不会因为模型要求就放宽权限。对同一检索无进展、重复查询或预算耗尽时停止，保留 `open_questions`；不会无限尝试，也不创建隐式 Agent 子任务。新的补充证据按“先入库、再处理、后追加”的顺序进入本 Run 的最终 `research_inputs`，保留初始 Evidence Package 原快照。

**输出校验：** 只检查所需结构、证据引用存在性、来源时间与版本标示、调用权限和必要字段；来源时间按实际资料展示，不以 `published_at <= as_of` 作为拒绝运行中补证的条件。不用文字长短、情绪方向或模型自评分数硬编码研究判断。非法引用或结构错误允许有限重试，不得静默编造事实。

研究完成后自动保存 Analysis 和 Report，不设置逐份人工复核、批准发布或强制第二来源确认的前置流程。Owner 按需阅读报告与查看证据；尚未核实或有冲突的信息如实标明。

## Analysis 与 Report

Research Agent 从去重后的 Evidence Package 形成 Analysis，再生成 Report。**不要求逐条 Finding 主数据、长期 Finding 关系图、独立 Verifier、核查状态机或多轮聚合算法**。

**Analysis 保存：** 本次 Run、目标与范围、`as_of`、增量窗口、实际 Document/Fact 证据引用及资料缺口；模型一次生成的文章标题、摘要、主题章节、结构化发现与待解问题。当前严格输出必须包含 `title`、`summary`、`sections`、`findings`、`open_questions`、`evidence_refs`，不接受旧的仅摘要与发现列表协议。

- `title` 与 `summary` 为非空文本；`sections` 至少一章，每章包含非空 `heading`、非空 Markdown `body` 和 `finding_ids[]`。主题章节按研究问题组织，正文自身表达重要理由和会改变结论的不确定性，不以内部编号或发现类型充当文章标题。
- `findings[]` 每项包含本次结果内唯一且非空的 `id`、简明非空 `title`、`kind`、`statement`、`rationale`、`uncertainty`、`evidence_refs`。`kind` 为 `fact`、`source_opinion` 或 `model_inference`；每项至少引用一个本轮授权持久 Chunk，不为每项新建主数据表。
- 章节的 `finding_ids` 只能引用本次 `findings`，同一章内不能重复；每个发现至少被一章采用，同一发现可以支持多个主题章节。章节无结构化发现时允许空关联数组。关联 ID 只在结构化结果和交互中使用，不写进面向读者的正文。
- 顶层和发现内的引用均按 `(chunk_id, document_id)` 校验，只允许初始 Evidence Package 选择或 V2-01 授权补证追加的持久输入。保存时补全原有 `parse_batch_id`、`document_version`、`source_id`、`position`，并以两处引用的去重并集作为 Analysis 的实际引用。未授权引用或无效关联均拒绝，不能静默丢弃。

引用通过 Chunk/Fact 回到 Document 的内容版本与原件，具体定位以[数据契约](data.md#后台解析片段与索引)为准。既有 Analysis 可供比较，不能代替其原始证据来确认一个新事实。后端只检查结构、引用授权和确定性边界，不用字数、关键词或固定段落配额判断文章质量。

**Report 保存：** 使用 Analysis 的实际文章标题，按摘要和主题章节顺序确定性生成 Markdown 正文，保留待解问题、真实数据引用及必要来源/版本。章节来源由其关联发现的授权引用得到，按冻结研究输入的 `(document_id, chunk_id)` 首次出现顺序去重并分配一致编号；重复引用不增加编号，开合与筛选不改变编号；导出中的编号直接链接到所引用 Document 版本的原件端点，来源清单还包含仅在顶层声明的引用。来源独立性、原始值并列比较与资料缺口作为补充材料保留。市场、行业和个股共用报告契约，不强制固定栏目；无显著变化时可以如实说明。将原始数据、媒体观点与模型推断明确区分，资料缺失或冲突时直接说明，不能自动宣称“已核实”。详情 API 返回保存的 `analysis.title`、`analysis.sections` 和带 `id/title` 的发现供页面展示，不在读取时重新生成文章。

报告在成功保存后可被后续 Task 参考，失败不覆盖最后成功报告；V2-04 的历史报告对照按本文契约实现，只比较两份已保存报告的自身快照，不建 Finding 主数据或长期关系表。图表如需生成，数字必须来自 Repository 实际记录，而非 LLM 虚构。

当前实现要求每个 finding 明确标记为 `fact`、`source_opinion` 或 `model_inference`，至少引用一个本轮可用持久 Chunk（初始 Evidence Package 选择或 V2-01 授权补证追加的输入），并保存理由与不确定性；只有 Fact 或只有摘要的补充内容不可引用。输出结构和引用在模型调用内校验；非法引用或文章关联按 Research Agent 路由有限重试或回退，最终仍非法则 Run/Step 失败，不静默写报告。保存边界复用相同校验，重试复用的成功步骤同样不能绕过引用授权和章节关联检查。初始选择与追加输入的合法引用都为空时 Run 仍可成功：保留标题、摘要和至少一个诚实说明限制的章节，`findings`、顶层引用和各章 `finding_ids` 必须为空，不得虚构结论或裸 finding。Analysis 与由其确定性生成的 Markdown Report 在同一成功事务中保存；下游失败保留 Evidence Package 和此前成功的 Analysis/Report。新 Document 版本不会改变旧报告记录的 Document/Chunk 版本与原件。模型与 `0001_initial.py` 同步定义标题、章节及 JSON 数组约束，只验证全新库初始化，不增加迁移、回填或旧协议兼容。
