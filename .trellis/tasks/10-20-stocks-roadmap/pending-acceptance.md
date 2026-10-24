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
与 [分阶段验收](../../../docs/validation.md)。V1/V2 阶段退出条件仍未全部满足。

| 剩余项 | 现有证据与缺口 | 后续所需条件 |
| --- | --- | --- |
| 移动设备视觉、跨设备登录 | 响应式实现与认证测试已有，真机未执行 | 真实手机/平板与可访问入口，统一操作 |
| 物理 ARM 与公网 HTTPS | qemu 下 arm64 构建、全栈及恢复已过；不代表实机 | ARM 主机、域名/入口与 TLS 部署条件 |
| 真实远程 MCP | 授权拒绝、预算、保存后引用已有受控测试 | 真实服务地址、授权范围与通过受限文件配置的凭据 |
| 真实来源转载与冲突 | 同源合并及数值/单位/期间冲突测试通过 | 保留公开来源原件的成对样本；**2026-10-24 探测未取得**：官方 RSS 无 `<source>`、聚合器只给 Google 跳转、benzinga/streetinsider 403、marketwatch/barrons 401、区域性 Fed 页面 canonical 均指向自身。需 Owner 指定已知被转载且声明 canonical 的来源，或把聚合跳转解析列为产品扩展（[探测记录](../10-24-v2-real-source-samples/research/probe-2026-10-24.md)） |
| 真实跨报告版本/指标修订与晚到资料 | 真实两报告仅为同一公告改写；相关差异均为 0 | 同一来源的真实版本或指标修订历史及后续报告；受控样例不能代替。**2026-10-24 探测未取得**：库内 22 份真实 SEC 文档与线上字节全不同，但归一化正文 22/22 完全一致，差异仅为尾部防爬脚本随机串；Apple CIK JSON 字节与库内一致；Treasury 逐日数值属不同记录而非同文档修订。需更长观察窗口或指定会改稿的来源（同上探测记录） |
| SEC 全文与更多指标范围 | Apple CIK、4 字段映射已验证；完整 JSON parse 超过 5,000 值上限 | 后续如需此全文链路，先明确受支持范围与解析预算，再实施并验收；不临时放宽安全边界 |
| 返回货币费用的模型端点 | 网关两次真实调用费用均未知；受控返回费用测试通过 | 实际返回 `usage.cost` / `cost_usd` 的端点；凭据经受限配置提供 |
| 第三方对象存储 | 当前实测为自建 SeaweedFS 的 S3 兼容接口 | 目标 S3 服务与 bucket 授权；目前缺少此集成环境 |
| 长期调度、限流与预算终止 | 到期执行、活跃 Run 去重、失败游标不推进有证据 | 持续运行窗口与第三方真实限流/预算事件观察 |

上述来源样本与长期观察是未完成验收项，不一概归为必须由 Owner 人工完成；真机、发布环境
和第三方授权可按既有安排最后集中处理。当前无需重新提供 chat/embedding 凭据或联系邮箱。
本轮 Fed PDF 请求超时，未算通过；此前 V2-02 的真实 PDF 证据另有记录。

**证据精度校正：** V0 的旧引用文本当时只核对前 40 字符；V1 晚提交检查仅证明 SQL 可见性，
未验证该条资料经应用变化流进入后续证据包；两 Chunk/一 Document 的引用分布不能单独证明
取材合并计数。相应契约保留相关集成测试证据，当前描述不再把窄范围观察写成完整真实链路。

**保留现场：** 真实报告库 `stocks_both`、API `127.0.0.1:18091`、SeaweedFS 与 magpie 隧道保留。
本轮验收库 `stocks_v2_acceptance` 和 bucket `stocks-v2-acceptance` 独立保留，不新增容器；
远程结果在 `/tmp/stocks-v2-acceptance-20261023`。清理四条手工 Acme Fact 前已备份真实库，
不把其无效 Chunk 引用算作真实来源证据。凭据、数据库备份及行内容不提交到仓库。

## 复用说明

补验收轮次开始前，先读本文件、`docs/validation.md` 的「待最终验收」清单与各阶段父任务
`notes`，再决定是否需要新建任务；不要在 V0/V1 子任务里另起一套"真实闭环已验收"的表述。
