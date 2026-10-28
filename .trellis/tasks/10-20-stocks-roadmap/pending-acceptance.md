# 延后的验收项（跨任务登记）

路线图父任务持有的待补验收清单。这里登记的项**不是遗漏**，而是明确延后到统一验收轮次；
任何阶段收尾不得把下列项当作已通过。

真实闭环的原始登记与探测过程保留如下。当前模型无需重新提供凭据；chat 与 embedding 已可独立配置并实际运行。

## PA-1：V0 真实端到端闭环（V0-07 AC-1）

- **状态**：**已解除（2026-10-23）**。Owner 提供真实联系邮箱并以本机 magpie 作模型网关后，
  真实闭环已执行完成，证据记录在 `docs/validation.md` 的 V0 章节「V0 端到端验收」段落，
  V0 父任务的阶段退出条件已相应勾选。以下保留原始登记与探测过程供追溯。
- **原始状态**：延后，Owner 决定放到最后统一验收（2026-10-21）。
- **未执行的原因**：测试服务器 `100.120.14.84` 与本工作区都没有模型 Provider 凭据。
  `config/models.yaml` 通过 `${MODEL_API_BASE_URL}`、`${MODEL_API_KEY}`、`${MODEL_NAME}`、
  `${EMBEDDING_MODEL_NAME}` 取凭据，缺失时 `providers.resolve_env()` 直接失败。
  缺 Embedding 就没有真实 pgvector 索引，缺 chat 模型就没有真实 Research Agent。
- **已实测排除的其他阻塞**：对象存储可写可读（本任务自建 seaweedfs）、pgvector 可用、
  SEC Feed 返回 `HTTP 200`、API 只绑 `127.0.0.1`、只读证据 API 与版本/失败边界均已验证。
- **补验收时要做的事**：
  1. 提供一组 OpenAI 兼容凭据，需同时支持 `/chat/completions` 与 `/embeddings`，
     且 embedding 维度与当前固定向量空间一致（`embedding_space.py` 现为 1024）；
     或改用自建模型并同步修改向量维度与初始化建表定义（开发阶段直接改当前结构，不写迁移）。
  2. 把 `config/sources.yaml` 的 `owner@example.com` 换成 Owner 认可的真实联系邮箱
     （SEC 对缺少联系身份的自动客户端返回 403）。该邮箱会随请求头发给 SEC，需 Owner 先确认。
  3. 按 `docs/quickstart.md` 顺序跑：真实采集 → 解析 → 索引 → 手动研究 Task →
     `/api/reports` 读取该报告并打开其引用的原件；记录 `run_id`、脱敏计数与实际响应。
  4. 核对 AC-2 在真实数据上同样成立：新版本入库后旧报告仍指向旧片段与旧原件。
- **已归档证据**：`.trellis/tasks/archive/2026-10/10-20-v0-07-evidence-api-acceptance/`
  的 `acceptance.md` 与 `research/verification-2026-10-20.md`。

### 历史探测：当时的 magpie 路径不可用（已由后续解除记录取代）

以下是向量空间仍为 1536 维时的历史判断，不是当前阻塞。有限网关探测只说明当时路由响应未满足应用结构，不能证明官方 DeepSeek 不兼容；`group/openai` 是网关路由名，不是官方 OpenAI API 模型标识。模型列表缺少 embedding 项也不能证明端点不提供 embedding。当前已采用独立 chat/embedding 配置与 1024 维空间，不再适用下文的 1536 维前置条件。

Owner 提议用本机 `127.0.0.1:3425` 的 magpie 代理（当前 DeepSeek 路由）跑 V0 闭环。实测结论是
**不能支撑**，因为两个硬阻塞各自都会直接中断链路；未把代理路径当作已可用方案。

- **严格结构化输出不成立。** `providers.research_chat()` 对每次模型调用都发送
  `response_format: {"type": "json_schema", "strict": true}`，并用 `validate_content`
  做确定性校验。以分类、Tool 计划、研究 writer 三份真实 schema 各发多轮：
  - `group/openai` 3/3 严格符合 schema（可用）；
  - `workbuddy/deepseek-v4.1-flash`、`group/deepseek` 0/3，忽略 schema 并自由发挥成
    `{"category": "Business"}`、`{"label": "company"}` 等形状，会被
    `ClassificationInvalid` / `Tool plan does not match the required schema` 直接拒绝。
  - 即 Agent 输入和输出都无法通过，研究 Run 必然失败。
- **没有 1536 维 embedding。** `/v1/models` 的 21 个模型没有一个是 embedding 模型；
  唯一能返回向量的名字是 `workbuddy/bge-m3`，实测 **1024 维**，且 `dimensions: 1536`
  参数被忽略。`providers._embedding_routing()` 与 `repository` 都硬性要求
  `VECTOR_DIMENSIONS`（1536），直接不匹配；`qoder`/`anyrouter` 均 404 声明
  “only chat completions are served”，`codex` 系列为 `/v1/responses` 无 embeddings。

**若要复用该代理链跑 PA-1**，需同时满足以下之一，均尚未具备：

1. 换到真正支持 strict `json_schema` 与 1536 维 `/embeddings` 的端点（例如官方
   OpenAI 的 `group/openai` + `text-embedding-3-small`）；`group/openai` 值得先复核
   它是否同时支持 `/embeddings`（本次只验证了 chat 侧）。
2. 或接受把当前向量空间改成 1024 维（改 `embedding_space.py`、`models.py` 建表定义、
   `config/models.yaml` 的 `dimensions` 与测试常量），并接受 V0-07 AC-1 只剩 chat 侧能用
   真实模型、embedding 仍为真实供应商返回。开发阶段改当前结构即可，不写迁移。

本次未修改任何代码、配置或数据库，也未启动远程容器；探测只在本机读取代理响应，
不属于项目验证结果。

### 2026-10-23 解除记录

两项前置均已具备并实际执行：

- **模型凭据**：经本机 magpie 网关，chat 用 `group/openai`（Codex Plus 订阅，用应用真实
  taxonomy 指令与分类 schema 实测 3/3 严格符合；`group/deepseek` 实测 0/3，会缺 `reason`
  字段，不可用）；embedding 用 `workbuddy/bge-m3`（1024 维，语义区分度正常）。
  因该模型为 1024 维，向量空间与 pgvector 列定义已同步下调至 1024（提交 `fcbadb6`）。
- **联系邮箱**：`config/sources.yaml` 的 4 处 `user_agent` 已从 `owner@example.com` 换成
  `6spot07@gmail.com`，SEC 采集由 403 恢复为正常入库。

执行结果：Fed 与 SEC 各 20 条真实采集、40 份 Document 全部带 S3 快照、重复采集不增版、
显式重处理保留旧批次 Chunk、语义与精确检索可用且跨来源、Run `7b8959e9` succeeded 并产出
6248 字报告（12 条引用指向 7 Chunk / 4 Document，均带版本）、重处理后旧报告 7/7 引用仍可
解析、所核对的引用文本前 40 字符一致、报告正文保持一致；当时未比较完整 Chunk 文本，也未实测上游字节变化后新版本进入后续报告。失败路径不删资料、不覆盖最后成功报告。

## PA-2：V0-03 解析器缺陷（正文直接位于 article/main/body 时逐字符换行）

- **状态**：2026-10-22 已修复并验证，不再是待验收项。
- **修复**：`_walk_evidence()` 统一走 `_walk_block()`，并按新增的 `CONTAINER_ELEMENTS`
  （`article`/`body`/`html`/`main`）把容器自身的连续文本与行内元素合并成同一个证据片段；
  处理器版本提升为 `semantic-text-v3`，已完成的旧批次不会被复用，下一次解析会生成新批次。
- **验证**：测试服务器 `100.120.14.84` 隔离目录中 `tests/test_processing.py` 38 passed、
  解析/采集/处理重试/仓库/冲突相关 117 passed、后端全量 475 passed、版本提升后
  批次复用相关 5 个文件 151 passed；另取 2 份真实 Fed 与 2 份真实 SEC 新闻正文复核：
  修复前 Fed 两页分别出现 18/17 个单字符片段（`S\nk\ni\np...`），修复后 0 个单字符片段、
  来源范围回查 0 处不一致。
- **回归**：`tests/test_processing.py::test_text_directly_inside_container_stays_one_evidence_run`
  与 `::test_inline_text_directly_inside_container_keeps_order_and_ranges`；契约同步到
  `docs/data.md` 与 `.trellis/spec/backend/testing-guidelines.md`。
- **出处**：`.trellis/tasks/archive/2026-10/10-20-v0-08-document-collection-fixes/acceptance.md`
  及 `.trellis/spec/backend/testing-guidelines.md` 的"固定 HTTP 响应"条目。

## PA-3：V1/V2 本轮验收与统一剩余清单（2026-10-23）

**已补做：** V1 相关测试 111 passed 及真实调度/游标/认证行为；V2 相关测试
133 + 40 = **173 passed**，全新隔离库初始化无漂移；同一 Task 两份真实报告的 Agent 内容与
双向接口对照、9 张相关表的读取前后哈希不变；SEC Apple JSON 真实入库、四字段映射与重复复用；
Fed XML 真实字节的 parser 定位。详见 [V2 本轮记录](../10-20-stocks-v2/research/acceptance-2026-10-23.md)
与 `docs/validation.md`（仓库根路径）。历史运行证据不等于阶段全部退出；当前范围见 [最终范围决定](final-acceptance-scope.md)。

| 剩余项 | 现有证据与缺口 | 后续所需条件 |
| --- | --- | --- |
| 移动设备视觉、跨设备登录 | **2026-10-27 跳过**，只有浏览器/受控证据 | 不再要求硬件验收，不记为真机通过 |
| 物理 ARM 与公网 HTTPS | **2026-10-27 移出**；qemu arm64 证据保留为模拟结果 | 应用 HTTP、代理/CDN TLS；不宣称硬件或公网 HTTPS 已通过 |
| 真实远程 MCP | **真实客户端接通已完成并归档**，见 [Tavily 验证](../archive/2026-10/10-27-v2-real-mcp-tavily/verification/README.md) | 完整补证 Run 的入库、取材和引用仍缺真实证据；429 探测不代替应用限流验收 |
| 真实来源转载与冲突 | **不在范围（2026-10-24 决定）**。同源合并及数值/单位/期间冲突的受控测试已覆盖实现 | 无需后续条件。理由：系统只按显式声明（`<link rel="canonical">`、文章 URL、Feed `<source>`）合并，不做跨站正文相似度推断；2026-10-24 探测未找到任何显式声明指向他源同一原件的真实样本（官方 RSS 无 `<source>`、聚合器只给 Google 跳转、benzinga/streetinsider/globenewswire/investing 403、marketwatch/barrons 401、区域性 Fed 页面 canonical 均指向自身）。不把 Google News 聚合跳转解析纳入采集：其条目需依赖未公开接口解跳转，且多数目标站点返回 401/403，拿不到正文时只能存 `completeness=partial` 的标题/摘要，无法支撑 finding。扩大发现面应改接可获取全文的权威源。记录见[探测记录](../archive/2026-10/10-24-v2-real-source-samples/research/probe-2026-10-24.md) |
| 真实跨报告版本/指标修订与晚到资料 | **不在范围（2026-10-24 决定）**。跨版本对照、指标修订与晚到资料标注已有受控与接口级测试；真实两报告为同一公告改写 | 无需后续条件。理由：该场景要求长期轮询同一批 URL 等待偶发改稿/勘误，对当前手板投入产出不成立，Owner 明确不纳入。2026-10-24 探测也确认无合格样本：库内 22 份真实 SEC 文档与线上字节 22/22 不同但归一化正文 22/22 完全一致（唯一差异为尾部防爬脚本随机串），Apple CIK JSON 字节与库内一致，Treasury 逐日数值属不同 `record_date` 记录而非同文档修订。保留现有能力与测试，不再追求真实样本验收；不得把模板噪声当作来源修订 |
| SEC 全文与更多指标范围 | **2026-10-27 移出完整 JSON/companyfacts 链路**；submissions JSON 四字段映射已验证，parse 超限失败保留 | companyconcept 可按需取单指标，但未实测，不将 submissions 证据改写为其成功 |
| 返回货币费用的模型端点 | **2026-10-27 移出**；网关真实费用未知 | 不再要求新端点；保持 null/未知，不填 0，不按 token 估价 |
| 第三方对象存储 | **2026-10-27 移出**；自建 SeaweedFS 已满足使用 | 现有 S3 兼容证据不外推到第三方 |
| 长期调度、限流与预算终止 | **仍待验证，未移出范围**；到期执行、活跃 Run 去重、失败游标不推进有证据 | 真实持续调度、cron/时区与应用 429/预算耗尽观察；36h uptime 不能关闭 |
| 当前词表/原生 Jev 的真实文档重处理 | **单文档隔离 Magpie 重处理通过：jev-1.14.0，词表 2026-10-27**，不宣称 41 篇已使用原生 Jev | 见 [收尾验收记录](../archive/2026-10/10-27-final-acceptance-closeout/acceptance.md)；`decision_model` 本身不证明模型/协议身份 |

六项范围收敛已获 Owner 授权，不再索取对应硬件/供应商条件。完整 Tavily 补证 Run、长期调度/限流/预算终止仍未验证，不能被容器运行时长关闭。V1 晚提交仅验证 SQL 可见性，未证明应用变化流进入后续证据包。MP-01/MP-02 已于 2026-10-28 修复并归档，见 [配置一致性](../archive/2026-10/10-27-report-config-consistency/verification.md) 与 [历史 Facts](../archive/2026-10/10-27-report-historical-facts/prd.md)；原始记录仍见 [既有审查](../archive/2026-10/10-26-application-pipeline-audit/audit.md)。当前无需重新提供 chat/embedding 凭据或联系邮箱。
本轮 Fed PDF 请求超时，未算通过；此前 V2-02 的真实 PDF 证据另有记录。

**范围收敛（2026-10-24）：** 上表两项标注「不在范围」的条目经 Owner 确认不再作为验收目标，
从待验收清单移除；它们不是「已验证通过」，而是主动缩小范围，后续不得重新登记为待验收项，
其余项目按 2026-10-27 的 [最终范围决定](final-acceptance-scope.md) 分类处理，未明确移出的真实运行要求继续保留。分类成功重处理覆盖当前 JSONB，失败保留旧 classification，不创建新 parse batch；不把旧 parse/报告证据保留说成分类自动历史化。Owner 授权父任务归档是工作收尾，不等于这些缺口通过。

**证据精度校正：** V0 的旧引用文本当时只核对前 40 字符；V1 晚提交检查仅证明 SQL 可见性，
未验证该条资料经应用变化流进入后续证据包；两 Chunk/一 Document 的引用分布不能单独证明
取材合并计数。相应契约保留相关集成测试证据，当前描述不再把窄范围观察写成完整真实链路。

**保留现场：** 真实报告库 `stocks_both`、API `127.0.0.1:18091`、SeaweedFS 与 magpie 隧道保留。
本轮验收库 `stocks_v2_acceptance` 和 bucket `stocks-v2-acceptance` 独立保留，不新增容器；
远程结果在 `/tmp/stocks-v2-acceptance-20261023`。清理四条手工 Acme Fact 前已备份真实库，
不把其无效 Chunk 引用算作真实来源证据。凭据、数据库备份及行内容不提交到仓库。

## PA-4：真实来源到报告闭环的剩余项（2026-10-28）

**已完成：** 六项子任务均已归档；真实四来源采集 → Jev 分类 → 检索 → 市场与 SpaceX 报告 → 真实 Tavily 补证引用已形成联合证据，详见[集成复核](../archive/2026-10/10-27-real-source-report-loop/acceptance.md)。**部署镜像已重建：** 2026-10-28 以离线 wheelhouse 构建 api/worker、重建 web，容器从新镜像重建后源码标记在位、health 200；此前 `docker cp` 热修随容器重建丢失的风险已解除。来源级代理已实现并完成 Reddit RSS 真实入库（3 篇 `partial`），见[代理验收](../archive/2026-10/10-28-collection-proxy/acceptance.md)（归档后路径）。

| 剩余项 | 现状与缺口 | 后续条件 |
| --- | --- | --- |
| 论坛/讨论来源 | 2026-10-28 Owner **接受 `partial` 粒度**（Feed 标题+帖子正文；Reddit HTML 403，无评论页全文）。来源级代理已实现并完成 3 篇真实入库，S3 原件哈希核对一致；`reddit_stocks` 默认关闭，测试部署启用。3 篇 parse/classify/index 均完成；Jev 实际 `jev-1.14.0`，实体抽取走测试 Magpie（`deepseek-v4.1-flash`）。预算保留修复后，Run `f726f3e0` 选中 Reddit v2（`345870d4…`），direct 8 条、background 5 条，报告 `87a74b0b` 引用 Reddit 讨论；v2 chunk 已清理 JSON/HTML 包装。此前 Run `61df76f4` 的 30k 预算被增量变更占满，语义查询零选中。facts：1 篇 completed（0 条）、1 篇 skipped、1 篇 `MODEL_RESPONSE_INVALID` 失败 | 粒度决策已关闭；启用需 `COLLECTION_PROXY_URL`。剩余仅 facts 1 篇失败；不影响已完成文本检索/分类与报告引用 |
| 增量窗口续扫 | `(0, 644]` 每个 Run 只扫描 1/7 页，`change_scan_completed=false`，报告以 `retrieval_truncated` 明示 | 连续续扫 Run 覆盖完整窗口，并核对证据包与报告缺口 |
| CNBC facts 超时 | 3 份 CNBC 文档的 facts 阶段 `MODEL_TIMEOUT`，尚未重试 | 重试并确认 facts 状态与数据；未完成前不宣称这 3 份已抽取完成 |
| 行情/价格来源 | 未配置任何 quote 源。2026-10-28 验证现有 `TWELVE_DATA_API_KEY` 可用：basic 计划 8 次/分钟、800 次/天，SPY 日线与 NVDA quote 成功；当前 `SourceMapper` 只按固定 pointer 读取单个字段，日线数组需要扩展映射或每 symbol 一个来源 | Owner 确定首批数据范围（指数/板块/关注股日线、宏观利率、公司行动等）；未接入前不把市场表现或涨跌归因写成已覆盖 |

真实同源转载/冲突样例仍按 2026-10-24 决定移出范围，不因本清单重新登记。

## 复用说明

补验收轮次开始前，先读本文件、`docs/validation.md` 的「待最终验收」清单与各阶段父任务
`notes`，再决定是否需要新建任务；不要在 V0/V1 子任务里另起一套"真实闭环已验收"的表述。
