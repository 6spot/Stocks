# Task、Agent 与 Tool 执行契约

本篇只负责 Task / Run 的执行、触发、调度、模型配置和工具调用；研究语义及取材输出见 [研究设计](research.md)，数据模型见 [数据设计](data.md)。返回 [架构总览](architecture.md)。

**阶段边界：** V0 由 Owner 手动触发采集和一项研究，保存实际输入、引用和报告；查询当时已保存的资料，不使用“上次成功以来”的增量游标。V1 增加独立定时 Task、后台处理、可靠增量窗口及恢复。V2 增加研究中的授权外部补证和更丰富的模型/Tool 能力。下文定时与窗口规则从 V1 起适用。

## Task 执行契约

Task 属性：

| 属性 | 语义 |
| --- | --- |
| id / name | 稳定标识、可读标题 |
| type | `research` 或 `collection`；其他类型尚未实现 |
| strategy | 可选策略 ID；无策略的采集任务正常运行 |
| trigger | `manual`，或 `{kind: schedule, …}`：cron 必须带 IANA `timezone`，或 `every` 固定间隔秒/分钟/小时/天；两者互斥 |
| scope | 研究 Task 的市场/地域、公司名称、股票代码、行业与主题范围；在 Task 顶层 YAML 唯一定义，Run 固定为 `run.scope` 快照。采集 Task 不设 scope |
| sources | 采集 Task 的 Source 标识列表；研究 Task 不接受该字段 |
| steps | 有序步骤及参数/上游结果引用 |
| enabled | 是否参与触发；disabled 的 Task 不会被调度，残留的调度记录也会被清除 |

目标步骤种类：Connector 获取、Repository 持久化/查询、解析与分块、Decision Model 分类及抽取决策、按需 LLM 抽取、Embedding 与索引、研究 Agent 调用、授权 Tool 查询及报告保存。**V0 手动获取和研究；V1 用 YAML 定时采集、异步整理数据，并按任务自身设定的早晚/日/周时间生成综合报告；任何阶段都不按文章逐篇生成报告。**

步骤按配置顺序执行；when 仅允许基于已执行步骤的结构化字段或状态判断，不使用自由 Python 表达式或通用 DAG。满足条件才执行；条件不满足为 SKIPPED，不是 FAILED。

研究的取材、游标、分析与报告共用 `run.scope`，步骤不再另定义一份研究范围；补充查询可带具体检索条件以查找直接资料或相关背景，但不能修改 Run 的研究范围。采集轮询由采集 Task 的 `trigger` 唯一调度，Source 只保存获取参数和可选更新预期，不另设第二个调度入口。

**V1 已实现的 Task 子集：** 研究 Task 接受恰好一个 `select_evidence` 与一个最终 `research_agent` 步骤；采集 Task 只接受一个 `collect_sources` 步骤，由 `sources` 指定 Source，不加载任何研究模型路由。`trigger` 接受 `manual` 或 `schedule`。`select_evidence` 从已保存的当前 Chunk 中生成 Evidence Package，并读取固定变化窗口的一张有界原始变化页；可配置最多 8 条 `queries`、每条候选上限 `limit_per_query`、总估算 Token 预算 `max_tokens`、最多 20 个去重的 `source_ids` 以及 `change_page_limit`（默认 100，范围 1–1000）。未配置 `queries` 时按 `run.scope` 生成直接研究和背景查询。自 V1-06 起，增量 Document/Fact 与历史背景 Chunk 在同一 `select_evidence` 中统一合并去重。`research_agent` 只接收该 Run 已保存的 Evidence Package；每个研究 Run 正常只取得一张原始变化页，若未耗尽窗口则保留检查点并由后续 Run 继续。

最终 Research Agent Step、Analysis、Report、成功游标与 Run `succeeded` 在一个事务内提交。**V2-01 起**研究 Agent 可声明 `tools`：列表中的每个 ID 必须是 `config/tools.yaml` 中已启用工具的显式子集，并同时被 Task/Agent 白名单授权；未配置、已停用、越权或未通过参数校验的请求在发出网络请求前拒绝。`tools` 为空时行为与 V1 相同：不读取工具目录，也不执行任何外部 Tool/MCP 调用。`when` 只允许引用更早的 `select_evidence` 结果，并仅支持以下白名单判断：

| `op` | 允许的 `field` | 语义 |
| --- | --- | --- |
| `exists` | 不配置 | 引用的前序步骤执行成功 |
| `nonempty` | `output`、`output.items`、`output.coverage`、`evidence_refs` | 对应结构化结果为非空 |
| `equals` / `not_equals` | `output.truncated` | 与显式布尔值比较 |

条件不满足时保存 `skipped` 的 StepResult。最终 `research_agent` 被跳过时 Run 以 `FINAL_RESEARCH_STEP_NOT_SUCCEEDED` 失败，不得推进成功游标。配置拒绝未知字段、未知步骤/类型、未知 Agent、重复步骤 ID、向前引用、未声明 Schema 关键字、越界或重复的 Agent `tools` 授权，以及无法解析为已启用工具的授权 ID。公开示例位于 `config/tasks.yaml`，分别覆盖成功执行、条件跳过和无可引用证据仍成功并报告缺口。

统一执行结果：

~~~text
StepResult:
  status: succeeded | failed | skipped | cancelled
  output: validated structured payload | null
  evidence_refs: list of durable references
  usage: tokens, duration, cost (where available)
  error: structured error | null
~~~

`usage` 只汇总已保存的调用记录：`cost_usd` 与 `cost_unknown_attempts` 一起出现，费用只来自供应商在本次响应中返回的金额（当前实现读取 `usage.cost` / `usage.cost_usd`，按 `numeric(14, 6)` 保存），**没有返回时保持未知，不用本地价格表推算，也不显示为 0**。Token 与耗时同样只取实际记录；缺失就是缺失。

V0 `EvidencePackage` 的持久字段为：Run、objective、scope、`as_of`、`selection_policy_version`、selection budget、实际执行的 queries、所选持久 Chunk 的 Document/Chunk/parse batch 版本、选择角色与理由、候选/筛选/选中/未检查数量和索引覆盖。候选总数无法从当前有界查询确定时为 `null`，不得填零；每条 Query 单独记录截断及缺口原因。

StepResult、Analysis 与 finding 保存的证据引用只保留可回查的身份字段：`chunk_id`、`document_id`、`parse_batch_id`、`document_version`、`source_id`、`position`。正文、标题、定位器与来源时间通过该引用从 Repository 读取，不在运行结果里复制原文，避免同一份证据随 Run、Analysis 与 Report 重复堆积。

`Analysis` 按 Run 保存 summary、结构化 findings、open questions、实际证据引用与覆盖缺口；每个 finding 的 `kind` 为 `fact | source_opinion | model_inference`，并至少包含一个所选 Chunk 引用、理由与不确定性。`Report` 由已保存 Analysis 确定性生成可阅读 Markdown。引用在保存 Analysis 前再次匹配持久 Document 版本、Chunk 和 parse batch；非法引用按模型响应错误有限重试，最终失败不写入或替换 Analysis/Report。同一 Run 已有 Evidence Package 时重试沿用该快照。

**可引用性的统一底线：** 初始选择与 V2-01 授权补证的证据都只有在统一入库并成为持久 Chunk 后才能支撑 finding；只有 Fact 或只有搜索摘要、没有可定位 Chunk 的补充内容不能作为证据，只能进入 open questions 与覆盖说明。Fact 的适用范围与补充检索边界见[研究设计](research.md#agent-补充检索与故障边界)。

按任务记录配置/策略/模型/提示词快照，便于解释历史研究结果。Task 使用独立运行记录，Worker 与 Web API 分进程；待执行记录及任务运行占用保存在 PostgreSQL。外部调用需有超时、有限重试和幂等键。原始资料及处理状态成功持久化后，Worker 才处理相应待办步骤；这不触发逐篇研究，综合研究仍由自身的 manual/schedule 触发。

V0 Run 在创建时快照 Task 的 objective、strategy、scope、步骤/Agent 配置、research prompt 和 Research Agent 模型路由，也快照 Repository 搜索使用的 Embedding 路由（别名、模型、维度、能力、超时及可用于核对配置是否有变化的端点哈希）。排队与执行解耦后，执行只使用 Run 自己的快照，不静默用新任务定义替换；Evidence Selection 的 `as_of` 取 Run 实际开始执行时间（`started_at`），而非队列创建时间。执行时重新取得环境凭据，并核对当前路由与快照；发生不兼容变化时步骤失败，不静默换成另一模型或向量空间。Task 和 Run 不保存 endpoint 明文、API key 或 Authorization；Agent 的 Tool 授权快照只保存工具 ID 与配置身份哈希，不含 endpoint、凭据或目录原文。每个 StepResult 保存结构化 output、持久证据引用、usage 和结构化 error；ModelCall 保存请求/实际 alias 与模型名、生成该调用的 Provider 键与供应商返回模型名、耗时、Token、供应商返回的费用（没有返回时为空）、回退原因和安全错误，不保存请求或响应正文。

### 同一任务运行与异常处理

- **同一个 Task 同时只允许一个待执行或正在运行的 Run。** 手动触发、定时触发与重试共用同一入口，由数据库原子检查并占用；任务已占用时不再创建新 Run，手动请求返回现有运行信息，定时触发跳过本次。不同 Task 可以独立运行。
- 数据库对 `status IN ('queued', 'running')` 建立每个 Task 唯一的条件索引。手动触发只创建 `queued` Run 并立即输出其 JSON 摘要，不等待执行、不抢占其他进程；重复触发若已有活跃 Run，直接返回该 Run 并以成功退出码结束，不读取 Task 配置、不查询模型凭据。
- 执行由独立 Worker 完成：它在一个事务内用 `FOR UPDATE SKIP LOCKED` 原子领取最旧的 `queued` Run，并写入所有权令牌；多个 Worker 不会重复执行同一 Run，也不会接管其他进程已领取的 `running` Run。Worker 支持单轮执行与带界轮询：到期调度按 `poll_seconds` 独立评估，`max_concurrent_runs` 个执行槽位常驻——刚执行完一份 Run 的槽位立即领取下一份，空队列的槽位按 `poll_seconds` 等待（可被“本轮已入队 Run”提前唤醒）而不是忙轮询，连续意外失败按指数退避（上限为 `poll_seconds` + 60 秒）；开始停止后每个槽位最多再领取一次，因此 `--rounds` 最后一轮入队的 Run 不会被丢下，也不会把停机变成无限追赶。这使得一次长研究调用既不阻塞采集，也不推迟到期调度。单 Worker 并发上限默认 2（可配 1–16）。
- 同一个 Worker 另持有 `max_concurrent_documents` 个独立文档处理槽位（默认 1，可配 1–16）：每个槽位一次领取并执行至多一个已入库 Document 的可领取阶段，完成即尝试下一项，无可执行待办时按同一有界等待/退避规则等待，停止时最多再领取一次。线程池容量覆盖 Run 槽位、文档槽位与调度轮，因此文档积压不会饿死采集/研究，长 Run 也不会阻止已入库资料被解析或索引；未注册处理器的阶段不会被领取，也不会被轮询空转。
- 每个执行上下文使用独立数据库 Session；某一 Task 的配置、模型初始化或执行失败只记录为该 Run 的结构化错误，不终止 Worker 轮次，也不影响其他 Task 的执行。
- 采集 Run 只加载 `config/sources.yaml` 与现有 `collect_source`，不解析 chat/embedding 路由；研究模型配置损坏不影响采集入队与执行，采集故障也不阻止研究读取存量资料。
- Run 未正常结束、Worker 中断或步骤失败时，保留已保存的步骤结果、Evidence Package、原始错误与 ModelCall 记录。Worker 自身不检测死进程、不做自动接管。Owner 必须先确认旧进程已停止，再通过受控 CLI 显式结束该 Run；结束后清除运行占用并保留全部进度与错误。
- 重试创建新的 Run 记录，复用被结束 Run 的配置/模型/提示词快照、失败点之前已成功的 StepResult，并复制其原变化窗口、展示时间、`change_scan_progress` 与 Evidence Package（新包的 `run_id` 属于新 Run，`select_evidence` 输出中的 `evidence_package_id` 同步改指新包），把 `retry_of_run_id` 指向原 Run；重试不重新绑定最新端点。`research_agent` 步骤不复制，Analysis/Report 由重试重新生成，不借用旧 Run 名下的结果。原 Run、其失败步骤、ModelCall、Analysis/Report 与错误继续保留为失败历史，不删除失败证据、不伪装为首次执行。`skipped` 步骤不复制而是重新判断条件，避免把旧判断带入新一次尝试。重试只允许针对 `failed` 或 `cancelled` 的 Run，且仍受同一 Task 单个活跃 Run 约束。
- 结束 Run、写入步骤结果与写入最终状态都核对 Run 状态与所有权；Owner 结束之后，旧执行不能再把 StepResult、Analysis/Report 或 Run 最终状态写成成功，这一步是确定性 fence。`select_evidence` 与 `collect_sources` 会在步骤内部提交自己的派生结果（Evidence Package、Document），因此在执行每一步前以及每个 Source 开始前再核对一次所有权，避免被结束的 Run 启动新的步骤或新的 Source；这层事前检查不持行锁，也不能回滚已经开始的步骤已在自身事务中提交的资料，采集到的原文仍按统一入库规则保留，只是该 Run 不会因此获得成功的步骤结果或报告。结束 Run 的前提始终是 Owner 已确认旧进程停止。
- 解析、分类、抽取、索引和取材积压使用 Task/Run 或 Document 处理状态管理；有限自动重试后，可由 Owner 手动重试或继续处理，不另建积压管理服务。V1-02 已提供 `stocks.processing status|retry|reprocess` 的有界 CLI：`status` 按 Document、阶段与状态查询积压并显示本部署已注册/未接入的阶段，`retry` 只重开失败或未完成阶段，`reprocess` 可显式重跑已完成阶段，运行中阶段必须 `--confirm-stopped`。阶段状态机与重处理边界见[数据设计](data.md#后台解析片段与索引)；交互见[界面设计](interface.md)。

**V1-07 Owner API 恢复入口：** 受保护 API 复用上述 Task/Run 服务，不复制调度或执行
状态机。`POST /api/tasks/{task_id}/runs` 手动触发并返回活跃 Run 或新排队 Run；
`POST /api/runs/{run_id}/retry` 复用失败/已结束 Run 的固定窗口和步骤进度；
`POST /api/runs/{run_id}/continue` 为仍有未完成扫描检查点的研究 Run 创建正常后续 Run，
请求中的 `resumed_from_run_id` 必须再次指明同一来源 Run；
`POST /api/runs/{run_id}/end` 与 `POST /api/processing/retry` 在涉及运行中所有权时都要求
显式确认旧执行已停止。阶段重试只重新排队，实际执行仍由 Worker 领取。手动触发或续扫
需要解析模型路由但环境凭据缺失、路由无效时返回 `503` +
`MODEL_CONFIGURATION_UNAVAILABLE`，使用固定消息且不创建 Run；已有活跃 Run 的重复触发
仍直接返回该 Run，不读取模型配置。
所有变更请求都经过 Owner 会话和 CSRF 校验；API 不把 Run 快照、模型调用错误或
Document 获取错误中的凭据、Provider 端点和响应正文返回给前端。

#### 持久调度状态与漏触发策略

调度配置及其 `next_due_at` 保存在与 Task 相邻的最小记录中（`task_schedules`），不使用纯内存 `last_run` 作为去重依据。并发 scheduler 对同一 Task 的到期判定、触发入队与 `next_due_at` 更新在同一个 Task 级 advisory lock 与事务内完成，越界写入无法只完成一半。

| 情形 | 行为 |
| --- | --- |
| 首次注册（新 Task 或首次启动） | 保存调度配置指纹，`next_due_at` 取当前时刻之后的下一个触发点；首次注册不立即触发 |
| 调度配置变更（cron、时区或间隔） | 一旦调度指纹不同即重新锚定，避免旧配置继续生效 |
| `every` 固定间隔 | `next_due_at` 按持久锚点累加，不按 Run 完成时间漂移；停机或繁忙错过多个触发点时合并为至多一次到期处理，并直接推进到下一未来触发点 |
| `cron` + 时区 | 用受维护的 cron 解析库在任务时区墙上时间计算；夏令时跳过的时刻不触发，重复时刻只在上一次出现时触发一次，保存为 UTC |
| Task 已有活跃 Run | 跳过本次到期（不读取模型配置、不记录配置失败），仍推进 `next_due_at`，不创建第二个 Run |
| Task disabled、从配置移除或改回 manual | 不触发，并删除残留调度记录，避免旧定义继续调度 |
| 单个 Task 配置/模型初始化失败 | 记录安全错误与该 Task 的 `next_due_at` 推进，继续处理其他 Task，不终止调度轮次 |

上述「繁忙跳过、错过合并」的选择是有意的：定时研究是周期性取样，不补历史 Run 风暴；已经错过的时间段仍由后续正常 Run 覆盖其时的资料。


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
| `available_at` | Fact | 该数值/结论成为公开可知的时间；不是 Stocks 入库或抽取时间 |
| `period_start` / `period_end` | Fact | 数值或结论的观测期，可为空 |
| 解析/分类/Fact/索引处理时间 | Document/Fact/Chunk 及处理记录 | 标明各项结果何时可供研究使用；报告引用当时的输入快照 |
| `change_watermark_start` / `change_watermark_end` | Task Run | 本次新增/变更情报的可靠扫描边界，采用 `(start,end]`；只由已提交变化推进 |
| `window_start` / `window_end` | Task Run | 与本次变化水位关联的展示时间，不单独决定候选是否进入窗口 |
| `as_of` | Task Run | 本次研究的参考时间，默认取 Run 启动时间；不限制运行中按需补证，也不表示严格的历史知识截止 |
| 成功增量游标 | Task + Research Scope | 为本任务的下一次运行确定增量起点；不同研究任务/范围互不影响 |
| `generated_at` | Analysis / Report | 本次分析与报告生成时间（当前实现为各自的 `created_at`）；后续资料更新不改写旧报告 |
| `research_inputs` | Task Run / Analysis | 追加型输入台账：初始 Evidence Package 边界条目 + 每个补充取得并采用的 Document 内容版本、Chunk/Fact 引用、Tool 调用身份、选择角色/原因、查询条件与时间关系；Analysis 保存采用时的冻结副本 |

各字段不可互相替代；报告保存其使用快照的真实来源时间与生成时间。

**时间语义：** `change_watermark_end` 固定本次例行增量扫描的终点，`window_end` 仅记录展示时间，`as_of` 标明研究参考时间。这些字段不禁止 V2 研究过程中通过授权工具获取补充资料。运行中取得的资料保存真实来源时间、采集时间和版本，按[研究设计](research.md#agent-补充检索与故障边界)追加到本次实际输入；晚于参考时间发布或更新的资料如被采用，应在报告中明确说明。报告保存当次实际取材和生成时间，后续资料更新不改写旧报告。当前产品不承担严格还原过去某一时刻全部已知信息的历史回放能力。

**窗口生成与推进规则：**

1. 研究 Run 开始执行时固定本次变化水位范围：起点取**同一 Task + 研究范围上一次成功提交结果并完成扫描的终点水位**，终点取本次启动时可确认已提交的最高水位；`window_start` / `window_end` 记录对应展示时间，默认 `as_of` 为 Run 启动时间。第一次运行使用明确配置的初始起点或初始回看范围并据此确定初始水位；更换研究范围后重新初始化，不沿用旧范围游标。待执行期间不提前扫描或推进窗口。Run 在扫描前提交固定窗口与初始 `change_scan_progress`，因此进程在此后中断仍可恢复；尚未开始的取消 Run 不会自行创建窗口。
   若同一 Task + Scope 存在未完成 Run，且其窗口终点仍高于该 Scope 的成功游标，正常新 Run 沿用最新有效检查点的原窗口、展示时间、冻结查询条件和分页位置（按 `created_at DESC`，再以窗口起止与 Run ID 确定性排序），扫描完成后再打开后续窗口；不会选择其他 Task/Scope 的进度，也不会把当前 Run 自己作为续扫来源。原始增量分页只执行 `change_scan_progress.query_snapshot` 中冻结的 `source_ids`、`page_limit` 等扫描条件，当前 Task 配置变化不影响未完成窗口的续扫；当前语义背景查询仍可按当前配置执行。retry 则明确复制来源 Run 的原窗口和进度。本次 Run 的 `as_of` 仍按实际参考时间记录，不将续扫误报为新的扫描范围。
2. 窗口只用于发现已进入 Repository、并在本次可研究增量中首次出现或发生重要修订的资料。文章的 `published_at` 不用于判定是否“已处理”：周二才入库或完成分类的上周五公告，也会作为周二新增可研究信息进入后续 Run。保留资料自身的真实发布时间供报告解释。
3. 对 Document 入库、解析/分类/索引完成、Fact 抽取/解析及内容修订保留可追溯的系统可研究变化。增量窗口以**已提交变化的稳定水位**界定；水位必须按提交可见顺序推进，不能直接把事务开始时的 `collected_at`、自增序列分配时刻或原文发布时间当作游标。Run 取得终点水位时，应保证此前水位的写入已经提交；之后提交的写入即使更早开始，也进入后续窗口。窗口同时保留相应时间用于报告展示。实现可使用同一 PostgreSQL 内按事务串行提交的变化计数，不引入额外服务。
4. `TaskRun.change_scan_progress` 保存版本化扫描状态、原始 `after_watermark`/`after_change_id`、窗口与展示时间、冻结查询条件、页/原始变化/候选 Document/选中计数及已扫描 Document 身份。只有原始窗口耗尽、最终 `research_agent` Step 成功且匹配的 Analysis、Report、Evidence Package 关系完整时，才在最终事务中把 `change_scan_completed=true` 并推进该 Task + Scope 的研究游标；任一写入失败则最终 Step、Analysis、Report、游标和 Run 成功状态一起回滚。扫描未完成时 Run 仍可在报告成功后关闭，但保留可续扫 progress、`change_scan_completed=false` 且不推进游标；最终 Step skipped、报告缺失、关系损坏或所有权失效时 Run 失败。已完成筛选但未选入模型输入的候选不阻塞推进。失败、取消或中断不推进；下次正常 Run 或 retry 覆盖未成功区间。已提交的扫描 Checkpoint 与 Evidence Package 在后续 Agent/report 失败时保留。即使周末无定时运行，周一仍包含周末新增信息。上述进度沿用现有记录，不新增独立扫描服务。
   “完成扫描”针对本次记录的增量查询范围与候选，不表示已穷尽全部背景资料或互联网信息；索引积压和来源缺失仍按实际情况说明。
5. Agent 可以根据明确的 Research Scope、`as_of` 与检索条件访问**增量窗口之外的历史背景资料和以前的 Analysis**，并对比既有结论；这些背景不应错误计作本次新增情报。每个研究 Run 把实际采用的输入留在独立 `research_inputs` 中，以便复现：初始 Evidence Package 是一条边界条目（包 ID + 初始选择快照哈希），之后按发生顺序追加每个补充输入（Document 内容版本、Chunk/Fact 引用、Tool 调用身份、选择角色与原因、查询条件，以及 `published_at`/`source_updated_at`/`collected_at`/`available_at`/观测期等时间关系与晚于 `as_of` 的标注）。该台账只追加、不改写：初始包不被重写，retry 沿用来源 Run 的初始包并只追加真正新取得的输入，已保存的旧 Analysis/Report 永不因后续变化被改写。
6. 同一份资料可参与多个不同的 Task、时间窗口与策略研究，不在 Document 上设置全局 `analyzed`、`last_used_by_task`、`research_window` 或 `consumed_at`。重复性由各自 Run 和输入引用的幂等约束处理。

~~~text
Run A（日报）：增量变化窗口 [上次成功扫描终点, 本次扫描终点)
                 + 查询此前财报、历史政策及过往观点作为背景
                 → 保存本次 research_inputs → 综合报告

Run B（行业周报）：自身独立的增量变化窗口
                 + 可以复用日报引用过的同一 Document
                 → 保存自身 research_inputs → 行业报告
~~~

**两类检索必须区分**：`stocks.changes.search_changes()` 按变化水位读取本次新增/修订的候选证据；`stocks.repository.search_chunks()`／`search_chunks_exact()`（经 `stocks.search` 暴露）按研究时点、Scope 与条件选择所有可用的背景/验证资料。二者均读取同一 Repository，前者不修改资料，也不把研究消费进度写回 Document。研究任务通常通过[统一研究取材](research.md)的 `stocks.research.select_evidence()` 统一执行这两类查询及取材，不需要每种研究自己重写步骤。

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

这里的研究示例仍属于逻辑配置：`window`、`repository.*`、`agent.researcher` 的具体字段尚未全部实现，增量窗口属于 V1-05。**V1-01 已实现的实际配置形式**（见 `config/tasks.yaml`）如下：

~~~yaml
  - id: scheduled_collection
    name: Scheduled SEC Press Release Collection (disabled example)
    type: collection
    trigger: {kind: schedule, every: 15m}
    enabled: false
    sources: [sec_press_releases]
    steps:
      - id: collect
        collect_sources:
          kind: collect_sources

  - id: scheduled_market_brief
    name: Scheduled Market Brief (disabled example)
    type: research
    trigger: {kind: schedule, cron: "0 8,20 * * *", timezone: America/New_York}
    enabled: false
    objective: Summarize recent evidence for the configured market scope.
    scope: {regions: [US], topics: [macro]}
    steps:
      - id: evidence
        select_evidence: {kind: select_evidence, queries: [recent macro evidence]}
      - id: research
        research_agent: {kind: research_agent, agent: research_writer, input: evidence}
~~~

`collect_sources` 直接复用 `stocks.collection.service.collect_source`、`config/sources.yaml` 与既有 Repository 幂等链路，不另写获取/持久化代码；一个采集 Run 处理其 `sources` 中的全部 Source。单篇文章获取或入库失败记为 `failed` 计数并保留在 output，下一轮采集会重试；整个 Source 无法采集（配置、Feed 请求或解析失败）时仍继续处理剩余 Source，但 Run 以 `COLLECTION_SOURCE_FAILED` 结束，保留含各 Source 摘要与错误的 output，便于定位。`objective`、`scope`、`strategy` 是研究 Task 属性；采集 Run 的 `run.scope` 固定为 `{"source_ids": [...]}`，`objective` 取 Task 名称，不包含研究语义。

这里的 `financial_news`、`collection.acquire_content`、Agent ID 与时间都是逻辑配置示例，并非已实现接口；`acquire` 表示任意渠道只取得标题/摘要/链接时需补取正文，已取得全文或完整响应则无需重复下载。**fetch 和 acquire 之间不执行逐篇相关性过滤。** `scope: run.scope` 表示读取当前 Run 的研究范围快照；入库后处理依赖遵循[数据设计](data.md#后台解析片段与索引)，综合研究仍由各自 YAML Task 调度。

## Agent、HTTP 模型与回退

Agent 配置包括：id、role、prompt、model_ref、tools[]，以及按职责需要的结构化输出契约。`model_ref` 指向 `config/models.yaml` 中的 Agent 路由名；该路由的 `primary` / `fallbacks` 是模型别名，不要求与 Agent 的 `model_ref` 同名。**Decision Model 是模型能力类别，不是 Jev、Clef 等特定品牌的别名**：Jev、Cloudflare Clef/Clef-flash 或符合相同需求的其他模型均可配置为分类/决策模型。分类步骤对预定义问题做单选、多选或是/否判断，包括 `should_extract`，并统一归一化为 Stocks 的业务 Schema；如 Provider 返回各选项概率，仅作为决策输入/诊断信息，不自动作为事实可信度或投资评分。Generation Model 则用于按需生成事实抽取结果、研究文本及授权 Tool 调用请求。不同模型类型通过协议 Adapter 接入，不强行假设所有模型都能输出长文、原生工具调用或 JSON Schema。

Provider 定义 protocol、HTTP endpoint、认证方式、默认超时等。**决策模型的有界问题/选项接口与普通 OpenAI-compatible `chat/completions` 并不必然相同**；按实际 Provider 协议通过薄 Adapter 归一化服务端格式。文中的模型名称仅作可配置角色的示例，不代表协议兼容性已经验证或相应 Adapter 已实现。对于不确定、无效或调用失败的决策，保留原文与未完成状态，不能默认解释成 `should_extract=false`。Provider 不承担 Agent 逻辑，任何私有部署、官方服务或模型聚合服务都只是一个 Provider 配置实例，不构成系统依赖，不引入独立模型调度服务。

**V1-03 决策路由实现：** `config/models.yaml` 的 `agents.classification` 声明 `protocol` 与 `routing`；当前只实现 `openai_compatible` 一个 Adapter（复用 `research_chat` 的有界重试、结构化输出与按序回退），其他 `protocol` 在读取配置时直接报错，不假设兼容。分类业务 Schema、受控词表与 Prompt 约束由 Stocks 在 `config/taxonomy.yaml` 维护，不由 Provider 定义；`required_capabilities` 至少包含 `chat` 与 `structured_output`。决策路由与词表都在真正领取 `classify` 阶段时才读取，缺失或损坏只让该次分类尝试失败（`DECISION_ROUTING_UNAVAILABLE` / `TAXONOMY_UNAVAILABLE`，不自动重试），不影响解析、索引与采集。

**V1-04 Fact 执行实现：** `facts` 阶段在真正领取时才解析所需能力：结构化响应（`application/json`/`text/json`）按来源的 Source Mapper 直接映射，不读取生成模型路由；普通文本才需要 `config/models.yaml` 的 `agents.fact_extraction` 路由，并只在当前分类 `should_extract=true` 时运行。缺失 mapper 或生成模型路由时阶段以 `pending` + `PROCESSOR_NOT_AVAILABLE` 等待、分类未决时以 `CLASSIFICATION_PENDING` 等待，两者都不消耗重试次数；真正的模型或映射失败才记 `failed` 并进入既有有限重试。`facts` 不依赖 `parse` 或 `classify` 作为硬前置：它的输入是已存内容本身，`classify` 与它之间是适用性关系而非输入依赖，因此纯数值响应不会因为"无需文本片段"而永远等待解析。抽取结果中任何不在当前解析批次内的 `chunk_id` 都会被拒绝，模型无法把伪造引用写入可检索证据。

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

**V2-01 执行契约：**

- **有效权限 = 交集。** `config/tools.yaml` 中 `enabled` 的目录条目 ∩ Task/Agent 的 `tools` 白名单 ∩ 通过 `input_schema` 校验的请求。模型只能提出结构化请求（`tool`、`arguments`、`purpose` 与要解决的缺口/问题），不能扩大工具集合，也不能修改 scope、`as_of`、增量窗口、预算、凭据、endpoint 或授权；不需要 Provider 原生 function calling，任何能输出受约束 JSON 的模型都可参与。
- **执行边界。** Stocks 校验后执行：先读 Repository，只有资料缺失、过期、存在冲突或需要交叉核实时才发出外部请求；外部只支持远程 Streamable HTTP（`2025-11-25` 握手 + `Mcp-Session-Id`），不实现 stdio。Tool 目录条目的 `source_id` 指向已配置 Source，取回的原件通过统一入库入口登记，Publisher 仍是原网站。
- **有界与终止。** 每 Tool 的调用数/Token/请求与响应字节由 `ToolBudgets` 约束，单 Run 另有轮次与每轮请求上限；预算耗尽、重复查询或无进展即终止，最多再做一次补充计划。补证循环位于既有最终 Research Agent 步骤内，不新增编排层或隐式子 Agent；终止后仍用已有证据完成报告。
- **拒绝与失败。** 未授权、未知、已停用或参数非法的请求在发出网络请求前拒绝；瞬时传输失败只做有限重试；预算耗尽、超时、映射失败与“只有摘要、没有可引用原文”都记录为本次 Run 的显式缺口（`tool_*` / `supplement_*` 代码），不伪装成“资料不存在”，也不丢弃已保存证据。
- **凭据与输出卫生。** 凭据仅通过环境变量提供（`bearer_token_env` 只保存变量名），执行时才读取；endpoint、Authorization、请求与响应正文不进入日志、Run 快照、ModelCall、StepResult 或报告。Tool 返回一律视为数据：原始响应不直接粘贴进报告，只有经统一入库并成为持久 Chunk 的内容才能被引用；持久化只保留 Tool 调用身份与安全元数据。
- **授权快照。** Run 创建时把每个 Agent 的工具授权解析为不含凭据的身份列表写入 `model_snapshot`；执行前重新加载当前目录并比对，授权集合或工具身份发生变化时步骤以 `TOOL_AUTHORIZATION_SNAPSHOT_INVALID` 失败，不静默换用新配置。
