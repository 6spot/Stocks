# 分阶段验收

本篇只定义各阶段的可验证完成条件。数据、执行和研究契约分别以[数据设计](data.md)、[执行设计](execution.md)和[研究设计](research.md)为准；系统边界见[架构总览](architecture.md)。设计目标不等于当前实现，已可运行能力见[开发运行说明](quickstart.md)。

## V0：真实研究闭环

V0 证明“少量真实资料 → 原件与可检索证据 → 手动综合研究 → 可阅读、可追溯报告”。Owner 可以手动采集、手动运行一种研究目标；V0 不承诺定时运行、Fact 抽取、上次运行以来的无遗漏增量扫描或完整管理控制台。

- **真实原件**：至少一个已配置的真实来源能获取文章或公告的实际正文。RSS 链接/摘要只是发现信息；全文不可得时保存已取得内容、标记不完整，不把摘要冒充原文。配置范围内的内容不按当前关注股票逐篇过滤。
- **身份与版本**：同来源同资料的重复原始字节复用当前版本；原始字节变化产生新 Document 版本与 S3 快照。仅上游更新时间变化而原始字节不变时不虚增版本。每个版本保留原始来源、发布时间、上游更新时间（若提供）、采集时间、哈希及原件引用；旧版本仍可打开。
- **可检索证据**：适用正文产生带原文位置的 Chunk 与 pgvector Embedding；语义检索结合来源/时间过滤。股票代码、指标编号等短字符串有有界精确查询路径。索引失败保留原件、显示缺口，不把未命中解释成资料不存在。
- **手动研究**：Owner 从当前已保存资料运行一项 Research Task。取材保存实际使用的 Document 版本、Chunk、选择理由与已知覆盖缺口；Research Agent 产出结构化 Analysis 和 Report，报告中的事实与数值引用真实片段。资料不足或没有显著变化时如实说明，不凑结论。
- **证据阅读**：Owner 可通过简单页面或 API 读取报告、打开引用片段与对应原件，看到来源、时间和版本。旧报告在新版本入库后仍指向旧证据。HTML 原件以安全文本或下载方式提供，不执行脚本。
- **运行边界**：采集或研究失败不删除已保存资料，也不覆盖最后成功报告。V0 只报告本次查询实际覆盖范围，不写“已扫描完上次以来全部变化”。未经认证的早期 API 仅绑定本机，不对公网开放。

**V0 端到端验收（2026-10-23 执行）：** 在测试服务器 `100.120.14.84` 的隔离目录与独立数据库上，用真实来源与真实模型跑通整条链路。**前提如实记录**：模型经本机 magpie 网关转发，chat 走 `group/openai`（网关路由；本轮响应提供所需结构化字段），embedding 走 `workbuddy/bge-m3`（1024 维）；因此当前向量空间为 1024，与此前 1536 维设计不同。

- **真实原件**：Fed 与 SEC 公开 RSS 各采集 20 条，40 份 Document 全部带 S3 快照，平均原件 86KB，正文 Chunk 平均 994 字——是公告正文而非 RSS 摘要。SEC 在 `user_agent` 换成真实联系邮箱后返回正常；此前发生过 403，现有证据不能确定占位邮箱是唯一原因。
- **身份与版本**：对同一来源重复采集，Document 数保持 20→20，原始字节相同不增版。对一份被报告引用的 Document 显式重处理，生成 attempt 2 且 attempt 1 的 Chunk 全部保留（同一文档 4 个 Chunk 分布在 2 个批次）；旧批次不进当前检索，`embeddings=112` 少于 `chunks=114` 正是这个差异，`document_embedding_states` 对当前批次仍记 `completed 2/2`。
- **可检索证据**：语义检索命中且相似度有区分（利率政策查询 0.618/0.557/0.551），跨来源查询同时返回 Fed 与 SEC 结果；`--source-id` 过滤生效；精确检索 `FOMC` 走不依赖向量的短代码路径，返回 3 条。
- **手动研究**：Run `7b8959e9-67f6-4d86-92ff-c0ea2a89495e` 状态 `succeeded`，产出 Report `92d9b19b-45a3-45ec-b28e-7540f382920b`：Summary 728 字、6 条 findings、3 个 open questions，12 条 `evidence_refs` 指向 7 个不同 Chunk、4 份不同 Document，每条都带 `document_version` 与 `parse_batch_id`。
- **证据阅读**：`GET /api/reports/{id}` 返回 6248 字正文与 50 条证据，`cited_chunk_ids` 为 7 条；`GET /api/documents/{id}/original` 以 `text/plain; charset=utf-8` + `x-content-type-options: nosniff` 返回 85KB 原件，不执行脚本；未认证访问原件与报告均返回 401。
- **AC-2（旧报告指向旧证据）**：对报告引用的一份 Document 显式重处理后重新读取同一报告——7/7 旧引用仍可解析、所核对的引用文本前 40 字符一致、报告正文与重处理前逐字相同。此项为同版本重处理，不是上游字节改变后新版本入库的实测，未比较完整 Chunk 文本。
- **运行边界**：把 chat 端点改成不可达地址触发失败，得到 `MODEL_ROUTE_SNAPSHOT_INVALID` 与 `MODEL_CONNECTION_FAILED`；失败前后 Document 20→20、Report 1→1，已保存资料未被删除、最后成功报告未被覆盖。

**仍未完成**：SEC EDGAR submissions 的完整研究链路（本轮已补真实采集与四字段映射，全文解析超限，见 V2）；调度长期运行与第三方限流下的有界终止；真实 MCP 与返回费用的端点。以上均按 Owner 安排留给最终验收。

## V1：日常运行

V1 在 V0 的证据链上增加定时采集、定时研究、结构化 Fact、失败恢复与受保护的日常界面。

- **独立调度**：YAML Task 可按明确时区/间隔调度采集与研究；Source 只定义获取范围和可选更新预期，不定义第二套采集周期。采集一百篇新资料不会生成一百份报告；市场、行业、个股 Task 复用同一 Repository 和 Research Agent。
- **统一处理**：RSS、媒体与配置的 API 在各自来源范围内收录新资料及明确更新；所有取得的内容走统一入库与处理流程。后台解析、分类/标签、`should_extract`、按需 LLM Fact、结构化 API 字段映射及向量索引分别记录状态。分类或抽取失败不删除原件，也不阻塞已解析片段的索引。
- **Fact 与数字**：一次 API 原始响应可以产生多条引用其字段路径的 Fact；文本 Fact 引用 Document 版本及原文位置。数值保留单位、统计期、披露时间和口径。重复处理幂等，修订值及其来源版本并存；未知单位不猜测，冲突值不静默平均。
- **统一取材**：Document 和其衍生 Fact 同时命中时，按同一证据链关联，不能算两份独立支持。直接提及某公司与行业/宏观背景分开；分类标签错误或缺失不应成为唯一的召回障碍。研究报告继续显示已知来源、索引和扫描缺口。
- **可靠增量**：每个 Task + Scope 使用按提交可见顺序推进的变化水位。07:59 开始写入、08:01 才提交的资料未被 08:00 扫描看到时，进入后续窗口；晚到的旧公告保留真实发布时间，仍进入新可研究变化。失败报告或未完成扫描不推进游标；分页进度可继续，范围变更重新初始化游标。
- **运行恢复**：同一 Task 同时最多一份待执行/运行 Run。有限重试后仍失败的采集、解析、分类、抽取、索引或研究可定位并由 Owner 重试；已完成步骤不重复制造资料。中断的 Document 阶段不自动接管，Owner 确认旧进程停止后才释放，已提交的解析批次与向量批次保留并只续做缺失部分。来源无更新、请求失败与处理积压分别呈现。
- **日常界面与部署**：受保护的 Overview、Intelligence、Tasks、Research 和基础 Runs 视图能查看来源、处理状态、报告及证据，并手动触发/重试。Web/API 跨设备开放前完成访问控制；按目标 ARM + Docker Compose 环境验证部署。报告失败保留最后成功报告。

**V1 端到端验收（2026-10-23 执行）：** 在 V0 同一隔离环境（独立库、自建 SeaweedFS 对象存储、真实模型经 magpie 网关）上验证以下日常运行行为。

- **独立调度**：`scheduled_collection`（`every: 15m`）启用后，首轮 `action=registered` 并持久化 `next_due_at`；未到期轮次返回 `not_due` 且不建 Run；把 `next_due_at` 拨到过去后返回 `triggered` 并真实执行成功。`scheduled_market_brief` 的 `cron: "0 8,20 * * *"` + `timezone: America/New_York` 的配置与实现已核对；本轮没有实际等待 cron 跨时区触发。Source 配置未定义第二套采集周期。
- **统一处理**：parse/classify/facts/index 四阶段独立记录状态（`/api/processing` 返回 `by_stage` 与 `by_status` 分布，实测 `parse` 2 completed/18 pending 等）。真实 LLM Fact 抽取跑出 3 条 Fact，`evidence_ref` 带 `chunk_id`+`position`+`parse_batch_id`。
- **Fact 与数字**：抽取出的 Fact 保留 `subject_name`、`unit`、`period_start/end`、`available_at`、`attributes`；数值与单位为空时保持空而不猜测。
- **统一取材**：一次 Run 的 3 条 `evidence_refs` 实际只来自 2 个不同 Chunk、1 份 Document。这证明引用分布，不能单独证明 Fact 与 Document 命中后的合并计数；该契约由相关集成测试覆盖。
- **可靠增量**：用两个连接检查晚提交——A 插入未提交时 B 的 SQL 查询看不到（0 条），A 提交后查询可见（1 条），水位在提交前保持 23。此处未证明该资料经应用变化流进入后续证据包。失败 Run（坏端点 `MODEL_ROUTE_SNAPSHOT_INVALID`）后游标保持 23 不动，即使计数器已到 24；随后成功 Run 把游标推进到 24。
- **运行恢复**：已有活跃 Run 时调度返回 `skipped_active_run`，不重复创建；活跃 Run 数始终 ≤1。`/api/processing?status=failed` 返回 200 且 `items: 0`，`counts` 给出分阶段积压。
- **日常界面**：`/api/overview`、`/documents`、`/reports`、`/tasks`、`/runs`、`/processing`、`/sources` 认证后全部 200，未认证全部 401。`/api/sources` 把 `status`、`request_success`、`no_update` 作为独立字段返回，与 `processing_backlog` 分开，来源无更新、请求失败与处理积压可区分。

**2026-10-27 范围收敛**：Owner 已跳过真实移动设备与物理 ARM 验收，移出公网 HTTPS 发布验收；应用 HTTP、代理/CDN 终止 TLS。qemu 证据保留为模拟环境结果，不标为硬件通过（见「待最终验收」）。

**已实现与待补的边界（截至 V1-09）：** 独立 API/Worker 进程、manual/schedule/retry 统一原子触发、`cron`+时区与 `every` 固定锚点的持久 due 调度、跨 Task 失败隔离、最小 `collection` Task 接入、Owner 显式确认停机后的结束/重试，Document 四阶段（parse/classify/facts/index）统一待办、独立有界文档处理槽位、有限自动重试、中断恢复与 `stocks.processing status|retry|reprocess`，轻量分类、通用 Fact 与 `http_api` Source Mapper 已在当前代码实现。可靠增量已实现提交可见水位、Task + Scope 独立游标、固定 `(start,end]` 窗口、按冻结窗口条件先过滤后分页的原始变化读取、TaskRun 版本化扫描检查点、正常 Run 恢复最新有效未完成检查点、retry 复制来源窗口与进度，以及最终 Research Agent Step/Analysis/Report/游标/Run 成功的原子提交。V1-06 已实现同一 Document 的增量与历史证据合并、Fact 与来源 Chunk 关联、`evidence_groups` 持久化以及 discovered/screened/selected/证据组/filtered-out/unexamined 的分项覆盖统计；跨 Document 转载识别与跨源冲突解释由 V2-03 实现，见下。V1-07 已实现环境凭据驱动的单 Owner 会话、受保护报告/原件/操作、稳定游标分页、Overview/Sources/Tasks/Runs/积压/检索接口，以及来源采集结果、更新预期和后处理积压的分离展示；自动化验收覆盖未认证、过期/损坏会话、CSRF、重复触发、确认结束、来源失败、安全原件和分页边界。V1-08 已实现受保护的 Overview、Intelligence、Tasks、Research、Runs 响应式 Web 控制台，包含运行时响应解码、内存 CSRF、统一会话恢复、URL 承载检索与详情、分段处理状态、历史引用证据优先阅读、受保护原件入口和 Owner 确认后的恢复操作；测试服务器上的类型检查、类型与行为测试、生产构建、生产依赖审计及桌面/手机浏览器 smoke 已通过。V1-09 已完成单机 Compose 生产形态：Nginx 提供前端并同源代理 `/api`、`/health`，生产容器不常驻 Node；API、Worker、PostgreSQL 不发布主机端口，只有 Web 入口可绑定；健康检查、SPA fallback、认证、CSRF、原件保护和模型配置缺失时的稳定错误已在实际服务栈验证。2026-10-23 补做 ARM：在测试服务器安装 qemu `binfmt` 后，`api` 与 `web` 镜像均构建为 `linux/arm64`，容器内 `aarch64` 可运行，全新库 `alembic upgrade head` 与 `alembic check` 无漂移，四服务全栈 `healthy`，入口、登录、受保护读取、处理筛选与 CSRF 边界均在该 arm64 栈上通过；同轮发现并修复 Nginx 只在启动时解析 `api` 上游、导致容器重建后 Web 持续 502 的缺陷（`deploy/nginx.conf`，提交 `7c334c4`）。ARM 实机、真实移动设备视觉验收、跨设备登录与公网/TLS 发布已由 Owner 于 2026-10-27 移出本轮验收，模拟结果不等于目标硬件通过；真实外网采集与 magpie chat/embedding 闭环已验证；真实 MCP 客户端已有独立证据，完整补证 Run 仍缺真实证据。返回费用端点、第三方 S3 和 SEC 完整 JSON 链路按文末范围决定处理。

## V2：研究深化

V2 在日常运行稳定后改善资料缺口、来源冲突和历史比较的研究能力。

- **授权补证闸门**：Repository 已保存资料充分时不发生任何外部调用；资料不足、过期或冲突时只允许调用显式授权的 Tool。未授权、未知、已停用或参数非法的请求在发出网络请求前被拒绝，注入的请求不能扩大工具集合、预算或研究范围。
- **先保存再引用**：实际采用的新资料先按统一来源/版本流程保存为 Document/Fact/Chunk，再供本次及后续研究引用；能据此定位到原件字节。未入库的搜索摘要或只有 Facts 的响应不能冒充全文证据支撑 finding。运行中取得的资料显示真实发布时间、获取时间与相对 `as_of` 的关系。
- **有界失败与重试**：Tool 失败、处理超时、预算耗尽或无进展时在限定轮次内终止，保留显式缺口，不静默成功；无任何可引用证据时 Run 仍可成功，但报告只表达缺口与不确定性。重试保留初始 Evidence Package 并只追加真正新取得的输入，不改写已保存报告。
- **来源独立性与冲突**：确定转载同一原文的资料按一组证据处理，保留各原件；仅标题或向量相似不能强制合并。独立相反观点及数值冲突并列呈现，热门公司的报道数量不能自动增加结论确定性。
- **历史和覆盖**：报告比较基于实际原始证据变化，说明以前的判断与当前证据差异；提供更完整的候选扫描、来源可用性、模型费用和异常处理视图。未扫描候选数量未知时保持未知，不伪填零。

**已实现与待补的边界（截至 V2-01）：** Tool 目录（`config/tools.yaml`，默认 `tools: []`）、授权解析与 Run 授权快照校验、远程 MCP Streamable HTTP 客户端（协议握手、会话、预算、SSE、结果映射、URL/DNS 安全与日志脱敏）、有界补证计划与执行（≤2 轮、每轮 ≤4 请求）、统一入库后追加 `research_inputs`、报告合并视图与重试保留已在当前代码实现，并在测试服务器以单元/集成测试与全新开发库的 `alembic upgrade head` + `alembic check` 验证。截至 2026-10-27，真实 Tavily 服务已通过生产 MCP 客户端调用并独立归档；完整研究 Run 的真实补证入库、引用与应用限流/预算终止尚未验证，见文末「待最终验收」。先前缺凭据的历史状态不再作为当前阻塞。以上验证只在测试服务器 `100.120.14.84` 的隔离目录执行，无凭据时标记未验收，不回退到本机或模拟结果。

### V2-02 更多真实来源与内容解析适配

- **格式适配**：JSON（含 `+json`）、XML（含 `+xml`）与 PDF 在后台解析阶段产生带定位的片段——JSON 用 JSON Pointer、XML 用元素路径、PDF 用页码与页内字符范围；数值与单位按原文文本保留，超限/损坏/无文字层按稳定错误码记为失败，不伪造空正文。
- **统一入口**：RSS/Atom 条目指向 PDF 时按文章获取并原样入库；格式化解析、分类、Fact 与索引仍走同一阶段链路，不按渠道分叉。
- **来源配置**：新增公开的 Federal Reserve 新闻 RSS，并给出需要 Owner 确认联系邮箱、CIK 与访问政策的 SEC EDGAR submissions API 示例（保持 `enabled: false`）。

**已实现与待补的边界（截至 V2-02）：** 三种格式的解析与定位、PDF 文章获取、上表的来源配置样例已在当前代码实现。测试服务器 `100.120.14.84` 隔离目录中的单元/集成测试覆盖格式定位、数值与单位保留、失败与超限码、PDF 文章获取与阶段结果；真实来源核对用隔离数据库与对象存储完成——公开 Federal Reserve 新闻 RSS 采集 20 条真实条目入库，逐个核对 S3 原件字节哈希与数据库 `content_hash` 一致；一份真实 HTML 原件解析为 `semantic-text-v2` 批次，全部片段范围可回到解码原文（42/42，21 个不同元素路径），数值逐字保留且无新增数字，普通重跑复用同一批次，显式重处理生成 attempt 2 并保留旧 Chunk；一份真实的 Fed 执行行动 PDF（189,823 字节）经文章获取通道入库后解析为 8 页、15 个 Chunk，每个页内片段按字符一一映射回该页文本。待补验收：联系邮箱已配置，2026-10-23 已补做 Apple CIK 的真实 JSON 入库、四字段映射与重复复用；完整 JSON parse 超过 5,000 值上限，全文研究链路与更多指标/CIK 未验收（样例保持 `enabled: false`）；调度触发的长期运行与限流行为未验收。未实现：XML 的字段映射（XPath Mapper）、OCR、PDF 表格结构重建。以上验证只在测试服务器隔离目录执行，不回退本机。

### V2-03 来源独立性与冲突证据呈现

- **只按来源声明合并**：页面 `<link rel="canonical">`、实际取得文章的绝对地址、feed 的绝对 http(s) `id`/`guid` 归一化后作为原件合并键；feed `<source>`/`atom:source` 只作为转载出处说明，标题、摘要、关键词、向量相似与 opaque（非 URL）标识都不合并。来源什么都没声明时记录为未知，不写成「已确认唯一」。
- **逐份保留可打开**：确认同源的多份 Document 仍各自保留 Chunk、Fact、原件与引用，覆盖统计区分 `selected_count`、`evidence_group_count`、`origin_group_count`，报告不能用资料条数冒充独立确认次数。
- **冲突并列**：同一 `(subject_name, symbol, metric_id)` 的多条 Fact 生成一个对比条目，分别标记 `value_conflict`（同口径取值不同）、`basis_mismatch`（单位/币种不同）与 `period_mismatch`（观测期不同），逐个列出各来源取值、单位、币种、观测期与可用时间；不平均、不静默覆盖、不标为已核实。
- **确定性报告段落**：`render_report()` 在 Coverage 之前生成 `## Sources and Conflicts`，说明独立原件数、列出确认同源的合并组与同指标各来源取值；内容来自已保存的 Evidence Package 与 `Analysis.coverage`，不依赖模型是否复述。

**已实现与待补的边界（截至 V2-03）：** 来源声明的采集与归一化（`stocks.origins`，含 canonical 链接解析）、`Document.extra.origin` 存储与 `document_origins()` 读回、`group_origins()` 与 `metric_comparisons()`、Evidence Package 的 `origin_groups` / `origin_group_count` / `merged_origin_group_count` / `metric_comparisons` 字段、报告确定性段落，以及 Web 报告页的来源独立性卡片、同指标取值对比卡片和「同一原件（N 份）」标记已在当前代码实现。测试服务器 `100.120.14.84` 的隔离目录中以 PostgreSQL/pgvector 集成测试覆盖确认转载合并、标题相似但不误合并、未声明来源记为未知、数值冲突与口径/观测期差异、报告段落内容，及报告详情接口的来源字段；全新开发库 `alembic upgrade head` + `alembic check` 无差异；前端通过类型检查与行为测试。真实来源核对（只读取公开页面、不写库）确认合并键在真实数据上的行为：Fed 与 SEC 公开 RSS 各 20 条真实条目分别得到 20 个互不相同的结果原件键，没有误合并；两份真实文章正文中，Fed 页面（85,178 字节）未声明 canonical，落到 `article_url` 且与条目地址一致，SEC 新闻页（64,615 字节）声明了 canonical 链接并与文章地址一致，因此记为 `canonical_url`；两个 feed 都未提供 `<source>` 转载说明。**范围决定（2026-10-24）：** 真实「多来源转载同一原文」成对样例与真实来源同指标冲突值不再作为验收目标（见文末「待最终验收」的范围收敛说明）。系统只按显式声明合并、不做跨站正文相似度推断，该行为已由受控测试覆盖；事件关系库、来源真值评分与独立核查平台明确不在范围内。以上验证只在测试服务器隔离目录执行，不把未运行的检查写成通过。

### V2-04 基于原始证据的历史报告比较

- **只读已存快照**：`GET /api/reports/{report_id}/comparison?other_report_id=...` 只读取两份报告各自的 Analysis、Evidence Package 与引用 Document 版本，不重新研究、不调用 Tool、不改写旧报告；失败或未完成的 Run 不能充当成功历史报告（409），同一报告自比返回 400，未知报告返回 404。
- **结论与证据差异**：只有 `kind` 与规范化 statement 完全相同的 finding 才算持续，改写过的条目按「一条消失、一条新增」显示，持续项标记不确定性与引用变化。Document 按来源自己声明的原件合并，同一版本被多段引用只出现一次，标出 `version_changed` / `content_changed`，两侧各自的原件都可打开。
- **修订与晚到资料**：同指标 Fact 按 `(subject_name, symbol, metric_id)` 比较，标出 `value_changed` / `basis_changed` / `period_changed` 并给出两侧真实取值，不平均、不静默覆盖；晚于较早报告 `as_of` 发布或采集的 Document 列在 `late_material`，解释为真实来源变化而不是先前已知。
- **覆盖如实呈现**：两侧 coverage、缺口增减与 `unexamined_count` 分开显示，未知保持未知；Task 或 Scope 不同、较早 `as_of` 未推进时给出 `warnings[]`；每组差异截断于 50 条并带 `truncated`，完整快照仍在各自报告与 Evidence Package 中。

**已实现与待补的边界（截至 V2-04）：** 后端 `compare_report_sides()` 与上述报告对照接口、Web 报告页的「历史报告对照」卡片（默认选中同一 Task 中最近的一份成功报告）已在当前代码实现。测试服务器 `100.120.14.84` 的隔离目录中以 PostgreSQL/pgvector 集成测试覆盖两侧排序与请求侧交换、结论新增/消失/持续与不确定性变化、Document 版本与内容变化、同指标修订取值、覆盖缺口与未知未扫描数量、晚到资料、自比 400、未知报告 404、失败 Run 409 及旧报告引用不变；前端通过类型检查与行为测试（默认预选、两侧原件链接、无对照对象时不显示卡片）。该任务不涉及数据库结构变更，全新开发库 `alembic upgrade head` + `alembic check` 无差异。**范围决定（2026-10-24）：** 2026-10-23 已补做同一 Task 两份真实成功报告的接口与内容对照，详见下文「V2 本轮集成验收」。「同一 Document 新版本被后续报告引用」与「同一指标跨报告修订」的真实成对样例不再作为验收目标：该场景需长期轮询等待偶发改稿，对当前手板不成立（见文末「待最终验收」的范围收敛说明）。逐条 Finding 关系图、严格历史知识回放与虚构价格/收益对比明确不在范围内。以上验证只在测试服务器隔离目录执行，不把未运行的检查写成通过。

### V2-05 覆盖费用与异常分析及 V2 验收

- **报告可定位覆盖与调用**：报告页显示资料缺口、研究覆盖（候选发现/筛选/入选、独立原件、未扫描候选、补证输入）与本次 Run 的模型用量，并可跳转到该 Run 的步骤与模型调用；未知的候选剩余与未提供的费用保持未知，不显示为 0 或已覆盖。
- **用量只来自实际记录**：Run 与步骤显示实际 Provider、模型、Token、耗时、回退原因及供应商返回的费用；`cost_usd` 只取供应商返回值（`usage.cost` / `usage.cost_usd`），没有返回时显示未知并给出未提供费用的调用次数，不用本地价格表推算。
- **覆盖聚合取自已存证据包**：Overview 的「研究覆盖」取最近一份成功报告的证据包，失败 Run 不充当成功历史；「近期模型用量」按最近若干个 Run 的已保存调用确定汇总。两者都不重新检索、不调用模型。
- **异常与积压复用既有操作**：重试、继续扫描、确认结束 Run 与处理积压仍由既有服务承担，本任务不新增调度、告警或独立监控平台。

**已实现与待补的边界（截至 V2-05）：** `model_calls` 新增 `provider` 与 `cost_usd`（只改 0001 初始化定义，不做增量迁移）；供应商响应中的 `usage.cost` / `usage.cost_usd` 按 `numeric(14, 6)` 保存，未返回时保持空。`GET /api/runs/{run_id}`、`GET /api/reports/{report_id}` 与 `/api/overview` 新增确定性 `usage` 汇总（调用数/成败/回退、Token、耗时、`cost_usd`、`cost_known_calls`、`cost_unknown_calls` 与有界的按模型分解），Overview 另给出来自最近一份成功报告证据包的「研究覆盖」（候选发现/筛选/入选、证据组与独立原件、未扫描候选、补证输入与来源/索引缺口），失败 Run 不充当成功历史；Web 的 Overview、Runs 与报告页显示这些字段，未提供费用显示为未知并标出次数，不显示为 0。测试服务器 `100.120.14.84` 隔离目录（独立 PostgreSQL `55442`、自有 seaweedfs bucket `18339`）中：`test_coverage_usage_api` 3 passed（未知候选剩余与未知费用保持未知、失败 Run 不充当最近覆盖、Run 与报告返回 Provider/费用/缺口），`test_providers` 9 passed（含供应商返回费用、未返回保持未知、非数值费用被拒绝），`test_execution` 63 passed（含新增全流程用例：受控 Provider 响应下执行一次 Run 后 ModelCall 记录 Provider 与供应商费用、步骤 usage 带费用与未知计数），`test_console_api` + `test_report_api` + `test_report_comparison` 与 `test_evidence_conflicts` + `test_collection` + `test_research` + `test_api` 通过；全新空库 `alembic upgrade head` + `alembic check` 无差异；前端 `npm run typecheck`、`npm run test`（8 文件 15 tests）与 `npm run build` 通过。**待补验收（不阻塞，留给最终验收）：** 费用是否可得取决于供应商响应，当前配置的 OpenAI 兼容端点通常不返回 `cost`，真实返回费用的第三方端点与真实远程 MCP 服务仍未接通；2026-10-23 已补做真实连续两份报告的 Agent 内容与接口复核；调度/限流下的长期有界终止仍属 V2 阶段待验收项。自动投资评分、动态成本路由与独立监控平台明确不在范围内。以上验证只在测试服务器隔离目录执行，不把未运行的检查写成通过。

### V2 本轮集成验收（2026-10-23）

本轮全部运行验证在测试服务器 `100.120.14.84` 执行，数据库与真实报告库分离。新库 `alembic upgrade head` 与 `alembic check` 无漂移；9 个 V2 相关测试文件 **133 passed**，另 `test_facts.py` **40 passed**，合计 **173 passed**，两个批次各有同一 Starlette/httpx 弃用警告。初次新库未初始化的 setup errors 已通过先初始化修正，不是产品修复。授权/MCP、转载冲突、版本修订及供应商货币费用的这些结果使用受控响应，不能外推为真实第三方场景。

- **真实报告对照已补做**：同一 Task 的 Report `f123a0bf-0003-4f99-a264-8527200148b2` 与 `9e9d97b2-07f3-4751-ad4a-2cce67aee741` 正反比较均成功，交换请求仍保持时间顺序；Finding 与 Open Question 各 3 新增、3 消失、0 持续，Document 1 持续，Fact 变化与晚到 Document 均为 0。逐条读取后确认两份均为同一 SEC 公告的改写，新增/消失来自精确 statement 匹配契约，不能解释为现实变化。读取/比较前后 9 张相关表的行数与全部记录哈希不变，ModelCall 仍为 2 条。持续 Finding、Document 新版本和真实指标修订仍未在这对报告中出现。
- **真实用量与未知费用**：两次调用的 prompt/completion 为 20701/865 与 20204/834，耗时 11430ms 与 9930ms；费用均为 null，各标记 1 次未知费用，未显示为 0。真实返回费用、失败/回退调用聚合仍没有本轮真实供应商样本。
- **SEC JSON 部分链路已补做**：只在隔离配置启用 Apple `CIK0000320193`，原件入库成功，4 个字段以 `source_mapping` 映射并保留 JSON Pointer、版本与原件哈希，重复采集复用 version 1。完整 JSON 的 parse 因超过 5,000 值上限而返回 `STRUCTURED_CONTENT_TOO_LARGE`，没有生成完整 Chunk/Embedding；字段映射成功不能当作全文解析成功。仓库样例仍为 `enabled: false`。
- **真实 XML 与 PDF 边界**：Fed XML 实际获取 14,813 字节，parser 生成 125 segments/12 chunks 并保存 `xml_path`。本轮 Fed PDF 请求 25 秒超时，未记为通过；此前 V2-02 的真实 PDF 证据仍保留。

详细命令、报告 ID、各项边界、旧记录校正和保留资源见 [本轮验收记录](../.trellis/tasks/10-20-stocks-v2/research/acceptance-2026-10-23.md)。这段记录反映 2026-10-23 的证据；后续 Tavily 客户端成功与 Owner 范围收敛以文末为准，长期调度/限流及完整真实补证 Run 仍未关闭。

## 所有阶段共同遵守

- 原文、RSS、网页和工具返回都是数据，不能改变系统指令或扩大 Tool 权限；来源等级、媒体观点和模型推断均不能冒充已核实事实。
- 不建设证券主数据、历史代码身份映射或按证券自动拆分 Task；公司和股票代码只用作研究条件与检索标签。
- 不做采集前投资相关性审批，不建设独立全文检索引擎、自动交易、复杂 DAG、多 Agent 自主协商或多租户平台。
- 开发阶段变更结构直接修改当前模型和初始化定义，按需重建开发数据库；不写旧版本迁移或兼容逻辑。运行时的原始证据、引用与版本仍须保留。

## 待最终验收

按 Owner 已确认的范围决定，集中登记已验证、移出范围与仍缺证据的项目。范围收敛不算通过，父任务归档不等于所有真实运行验收完成。统一记录见 [pending-acceptance.md](../.trellis/tasks/10-20-stocks-roadmap/pending-acceptance.md)，六项决定见 [最终范围决定](../.trellis/tasks/10-20-stocks-roadmap/final-acceptance-scope.md)。

- **真实模型闭环与真实外网采集**（V0-07 AC-1）：**已于 2026-10-23 执行并通过**，证据见 V0 章节「V0 端到端验收」段落。模型经本机 magpie 网关（chat=`group/openai`，embedding=`workbuddy/bge-m3` 1024 维），SEC 联系邮箱已换为真实地址。仍缺调度长期运行、应用第三方限流与预算耗尽的观察；SEC 完整 JSON 研究链路已于 2026-10-27 移出范围，本轮真实 submissions JSON 采集与四字段映射不是 companyconcept 验证。
- **真实远程 MCP 与授权工具**（V2-01）：**真实客户端接通已完成**，见 [Tavily 验证记录](../.trellis/tasks/archive/2026-10/10-27-v2-real-mcp-tavily/verification/README.md)。生产 `McpClient` initialize/list/call 成功（4.184 秒）；完整研究 Run 的真实补证入库、取材与报告引用尚未执行。Tavily 返回搜索摘要，不冒充完整网页；探测遇到 429 不证明应用持续运行时的恢复与有界终止。
- **真实来源联系方式与 SEC 映射**（V2-02）：联系邮箱及 submissions JSON 入库、四字段映射与重复采集已有证据；完整 JSON 解析超过 5,000 值上限失败。Owner 于 2026-10-27 移出全量 companyfacts/完整 JSON 研究链路验收；companyconcept 单指标按需取数是可选方案，未在既有证据中实测，不把原 submissions 记录改名为 companyconcept 成功。
- **真实同源转载与冲突样例**（V2-03）：**不在范围（2026-10-24 决定）**，不再作为待验收项。系统只按显式声明（`<link rel="canonical">`、文章 URL、Feed `<source>`）合并，不做跨站正文相似度推断，同源合并与数值/单位/期间冲突已有受控测试覆盖。2026-10-24 探测未找到任何显式声明指向他源同一原件的真实样本（Fed/SEC 官方 RSS 无 `<source>`；Google News 只给自身聚合跳转，且其条目需依赖未公开接口解跳转、多数目标站点返回 401/403，拿不到正文时只能存 `completeness=partial` 的标题/摘要，无法支撑 finding）；因此不把聚合跳转解析纳入采集。探测详情见 [probe-2026-10-24](../.trellis/tasks/archive/2026-10/10-24-v2-real-source-samples/research/probe-2026-10-24.md)。
- **真实报告差异样例**（V2-04）：**不在范围（2026-10-24 决定）**，不再作为待验收项。跨版本对照、指标修订与晚到资料标注已有受控与接口级测试；同一 Task 两次真实成功报告的 Agent 内容与接口对照已于 2026-10-23 完成。该场景要求长期轮询同一批 URL 等待偶发改稿/勘误，对当前手板投入产出不成立，Owner 明确不纳入。2026-10-24 探测也确认无合格样本：库内 22 份真实 SEC 文档与线上字节 22/22 不同，但归一化正文 22/22 完全一致（唯一差异为尾部防爬脚本随机串，如 2026-107 为 680 词对 680 词、旧 63889 字节对新 63879 字节）；Apple CIK JSON 字节与库内一致；Treasury 逐日数值属不同 `record_date` 记录而非同文档修订。保留现有能力与测试，不得把模板噪声当作来源修订。
- **真实返回费用的模型端点**（V2-05）：**移出范围（2026-10-27）**。不再要求提供返回费用的真实端点；未返回费用保持 null/未知，不写 0，不按 token 估价，继续使用 token 预算。
- **调度与限流下的长期运行**（V2-02、V2-05）：**仍缺真实证据，未移出范围**。保留调度触发的长期采集与研究、cron/时区真实触发、应用第三方 429 与预算耗尽下有界终止的观察要求。36 小时容器 uptime 只证明运行时长，不能关闭这些要求。V1 晚提交仅验证 SQL 可见性，未证明应用变化流进入后续证据包；已有受控测试不能改写为该真实场景通过。
- **真实移动设备与 ARM 实机**（V1-08、V1-09）：**跳过硬件验收（2026-10-27）**。真实移动设备视觉与跨设备登录不再作为交付条件；2026-10-23 qemu `binfmt` 下 arm64 构建、全栈与新库无漂移证据保留，Nginx 上游地址更新缺陷已修复（`7c334c4`）。模拟不是物理 ARM 通过。
- **范围收敛说明**（2026-10-24）：上列 V2-03「真实同源转载与冲突样例」与 V2-04「真实报告差异样例」经 Owner 确认**移出验收范围**，不是「已验证通过」，而是主动缩小范围。理由：前者需要他站显式声明转载同一原件，探测未找到此类真实来源，且系统按设计不做跨站正文相似度推断；后者需要长期轮询同一批 URL 等待偶发改稿/勘误，对当前手板投入产出不成立。两项的现有能力（显式声明合并、跨版本对照、指标修订与晚到资料标注）继续由受控与接口级测试覆盖，不得因移出范围而删除测试或改写为真实来源证据。上述两项范围决定继续有效；2026-10-27 的六项收敛另见下条，不自动关闭长期运行或完整补证 Run。
- **发布与存储范围（2026-10-27）**（V1-09）：公网 HTTPS 验收移出；应用提供 HTTP，由代理/CDN 终止 TLS，公开发布仍遵守访问控制与 secure cookie 配置。自建 SeaweedFS 已满足使用，不再要求接入第三方 S3。两项均为范围收敛，不宣称额外供应商或公网入口通过。
- **当前词表与 Jev 单文档重处理**：已在隔离环境经 Magpie 完成（实际 jev-1.14.0，3.429 秒），见 [收尾验收记录](../.trellis/tasks/archive/2026-10/10-27-final-acceptance-closeout/acceptance.md)。`decision_model` 不是原生 Jev 身份证据，不声称生产 41 篇均为原生 Jev；成功重处理覆盖当前 classification JSONB，失败保留上次结果，不生成新的 parse batch。测试代理不改变生产官方端点配置。
- **既有实现缺陷**：MP-01（自定义 API/Worker 配置路径不一致）、MP-02（历史语义文档漏带已有 Fact）已于 2026-10-28 修复并归档，见[配置一致性验收](../.trellis/tasks/archive/2026-10/10-27-report-config-consistency/verification.md)与[历史 Facts 修复](../.trellis/tasks/archive/2026-10/10-27-report-historical-facts/prd.md)；原始记录见[既有审查](../.trellis/tasks/archive/2026-10/10-26-application-pipeline-audit/audit.md)。报告闭环的论坛来源、增量续扫、CNBC facts 重试与行情源缺口登记在 [PA-4](../.trellis/tasks/10-20-stocks-roadmap/pending-acceptance.md)。
