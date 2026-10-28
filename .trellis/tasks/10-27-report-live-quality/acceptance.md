# 验收执行记录：真实来源到报告的端到端质量验收

执行环境：测试服务器 `100.120.14.84` 的部署实例 `/opt/stocks`（API / Worker / PostgreSQL / Web 真实运行，配置经 `/opt/stocks/config` 挂载），时间 2026-10-27T18:00Z–2026-10-28T02:10Z。所有凭据仅从 `/opt/stocks/.env` 现场读取，未写入本文件或日志。

## 1. 依赖与配置身份

| 项 | 实际值 |
| --- | --- |
| 采集来源 | `sec_press_releases`（RSS）、`fed_press_releases`（RSS）、`cnbc_markets`（RSS）、`fred_cpiaucsl`（HTTP API，`${FRED_API_KEY}`） |
| 报告 Task | `market_intelligence_brief`（无 companies/symbols，全 direct）、`spacex_company_research`（SpaceX/SPCX，首条查询 direct） |
| 研究模型 | `deepseek-v4.1-flash`（provider `chat`，经 tokenhub），research 超时 600s、retries 1 |
| 分类 | Jev `jev-1.14.0`（typesafe）+ 实体抽取 `deepseek-v4.1-flash`；词表 `2026-10-27` |
| Embedding | `bge-m3`，1024 维（经测试网关隧道） |
| Tool | `tavily_search`（远程 MCP，`tavily_web` Source，T3） |

## 2. 采集与处理（真实入口）

| 步骤 | 身份 | 结果 |
| --- | --- | --- |
| 采集 Run（修复前） | `7c45c829` | 失败：CNBC feed TIMEOUT；FRED `HTTP_STATUS 400` |
| 采集 Run（修复后） | `c49fdf96` | **成功**：discovered 51 / ingested 0 / reused 1 / skipped 50 / failed 0，四来源全部正常 |
| 处理状态（采集后） | `document_processing_states` | parse 53 completed；classify 53 completed；index 53 completed；facts 48 completed / 3 failed / 2 running |
| 遗留失败 | `fef1fecf`、`8062bde9`、`26e581d1` 的 facts 阶段 | `MODEL_TIMEOUT`（3 次尝试，300s 时代），可由控制台重试；不影响已解析片段检索 |
| 文档总量 | `documents` | 60 份（SEC/Fed/CNBC/FRED + 2 份 Tool 补充） |

FRED 400 的根因是**部署镜像仍是旧代码**：`SourceConfig.resolved_query()`（commit `018f4a3`）不在容器内，`api_key` 以字面量 `${FRED_API_KEY}` 发出。以 `docker cp` 把当前代码同步进 api/worker 容器后 FRED 恢复（`reused: 1`，同一 payload）。同理，镜像重建仍待后续处理，热修随容器重建会丢失。

## 3. 报告产物

| 报告 | Run | Report | as_of | 生成时间 | 证据 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| 市场情报简报（首轮） | `29fb878e` | `cfc4a225` | 2026-10-27T19:47:11Z | 2026-10-27T19:57:01Z | 122 chunk / 42 组 / 42 origin | 成功 |
| SpaceX 公司研究（首轮） | `0500c08c` | `cd98ce98` | 2026-10-27T19:47:11Z | 2026-10-27T19:49:17Z | 122 chunk / 42 组 | 成功 |
| 市场情报简报（本轮） | `e3a6b8fe` | `fe3ea7e6` | 2026-10-28T01:38:54Z | 2026-10-28T01:44:14Z | 55 chunk / 27 组 / 27 origin | 成功 |
| SpaceX 公司研究（本轮） | `62fa1697` | `d1546495` | 2026-10-28T01:38:54Z | 2026-10-28T01:42:57Z | 52 chunk / 29 组 | 成功 |
| SpaceX + 真实 Tavily 补证 | `fcfc59c4` | `ca8e39e4` | 2026-10-28T01:49:03Z | 2026-10-28T02:03:09Z | 56 chunk / 28 组 + 2 补充输入 | 成功 |

usage（本轮，供应商未返回金额 → `cost_usd: null`，`cost_unknown_calls` 如实计数）：

| Run | 调用 | prompt | completion | 合计 token | 耗时 |
| --- | --- | --- | --- | --- | --- |
| `e3a6b8fe`（市场） | 3（2 计划 + 1 研究） | 512,648 | 49,771 | 562,419 | 310s |
| `62fa1697`（SpaceX） | 3 | 491,372 | 30,300 | 521,672 | 233s |
| `fcfc59c4`（SpaceX+补证） | 4（2 计划 + 1 超时 + 1 成功） | — | 18,810 | — | 研究 147s，另 1 次 600s 超时 |

## 4. 内容核查（逐条对回原文）

| 报告结论 | 引用 | 原文核对 |
| --- | --- | --- |
| 2026-09-16 FOMC 加息 25bp 至 3-3/4–4%，12–0 通过 | `23278144`（fed） | 原文："raise the target range ... by 1/4 percentage point to 3-3/4 to 4 percent"、`by a 12 – 0 vote` — 一致 |
| 2026 上半年 208 宗 IPO 募资 >$137B（2025 同期 180 宗 / >$27B）；后续发行 557 宗 >$111B | `db66899a`（SEC DERA） | 原文逐字一致；报告注明这是历史统计，不是实时情绪 |
| 市场预期 10 月按兵、12 月路径分歧 | `924fe12b`（CNBC） | 原文 "Markets expect the Fed to hold benchmark rates steady"；报告标注为来源观点/市场预期 |
| 9 月 CPI 年率 3.7%，核心 2.6%（较 8 月 +0.3pp） | `924fe12b` | 原文 "annual rate at 3.7% in September ... core CPI pointed to a 2.6% rate ... climbing 0.3 percentage point from August" — 一致 |
| SEC 2026-98 指控 MGM 及 CEO，涉 SpaceX pre-IPO；基金丧失近 $3,000,000 投资 | `86cff9af`（SEC） | 原文 "repeatedly failed to address a capital call deficiency ... in connection with its investment in SpaceX, resulting in the fund forfeiting its nearly $3,000,000 investment"；报告明确这是**基金层面、指控阶段**金额，不是 SpaceX 估值 |
| CNBC 报道引述基金经理把 SpaceX 与 Anthropic/OpenAI 并称 "frontier models" | `66376e4c`（CNBC） | 原文引语逐字一致，报告标注为受访者个人观点且与航天业务无关 |
| 补充材料：2026-07-29 太空军 NSSL Phase 3 两份合同合计 $1.6B | `bcba67c1`（tavily_web） | 原文 "awarded two major contracts ... National Security Space Launch Phase 3 ... combined $1.6 billion" |
| 补充材料：S-8（2026-08-04）、S-1（2026-05-20）、424(b)、10-Q 引用与 Note 18 关联方交易 | `138e1702`（tavily_web，SEC EDGAR） | 原文含 "FORM S-8"、`Registration Statement on Form S-1, initially filed ... May 20, 2026`、"Note 18 to the consolidated financial"、PricewaterhouseCoopers 审计说明；报告只称"摘录/引用"，未当作报表数字 |

引用可访问性：`GET /api/documents/<id>/original` 对补充文档（1,358 bytes，`text/plain`，Tavily 抽取文本）与 Fed 新闻稿（84,741 bytes）均返回 200 与实际字节；报告链接指向真实原件，未用搜索摘要冒充全文（补充材料本身是抽取文本，报告已注明"摘录"）。

## 5. 取样专项

| 项目 | 结果 |
| --- | --- |
| 论坛/评论观点 | CNBC 报道被识别为来源观点并逐条标注（Monaghan、Guha、Ryan、花旗观点），与官方事实分开；未出现观点被写成事实 |
| 重复转载/冲突 | 本轮证据 `merged_origin_group_count = 0`（未出现确认同源的合并组）；同指标对比仅在 SpaceX 补证 Run 出现 1 组（`civil_penalty` OTC Link LLC $575,000，两值一致），报告按"一致"并列，未平均、未标已核实 |
| 公司直接信息 vs 行业背景 | SpaceX 报告明确只有 1 份配置来源直接点名（SEC 2026-98），其余航天/汽车/AI 背景材料"不能作为关于该公司的陈述"；`select_evidence` 复核：122 个入选 chunk 中仅 1 份文档含 "SpaceX" |
| 历史 Facts（MP-02） | `62fa1697` 证据包中 `historical_context` 组 13 个背景组有 9 个携带 `fact_refs`，6 个直接组有 3 个携带——历史语义入选的 Document 确实带出已保存 Fact |
| 迟到资料 | 补充输入在 `research_inputs` 标 `collected_after_as_of`；报告正文明确"collected_at 晚于 as_of，不能替代配置来源，也不能消除覆盖缺口" |
| 关键遗漏 | 两份报告均声明未配置行情/申报来源，价格、估值、发射与订单等不可回答；市场简报说明无价格数据 |
| 增量扫描完整性 | 本轮三个 Run 各扫描窗口 `(0, 644]` 的一张 100 条原始变化页（after_watermark 300/400/500），`change_scan_completed=false`、`status=paging`，报告以 `retrieval_truncated` 明示；续扫检查点保留 |

## 6. 外部补证（Tavily）

真实链路已验证：`fcfc59c4` 的 Tool 计划调用 → 真实 Tavily MCP → 2 份原文入库（`tavily_web`，T3，`complete`，真实 URL：GuruFocus 合同报道、SEC EDGAR S-8）→ 只对这两份 Document 跑 parse/classify/facts → 追加 `research_inputs`（2 条 `input`，`timing=["collected_after_as_of"]`）→ 报告 `ca8e39e4` 引用 `[E57][E58][E59]`。

发现并修复两处真实缺陷（本轮代码变更）：

1. **不可引用 Chunk 泄漏进模型输入**：Fact 的来源 Chunk 若不是本次入选片段，其 `chunk_id` 仍出现在模型可见的 `fact_refs` 中，模型据此引用后被 `allowed_refs` 校验拒绝（`MODEL_RESPONSE_INVALID`，`ea1e424e`/`8077cd79`/`05306616` 连续复现）。修复：`_agent_evidence` 只向模型暴露本次入选的 Chunk 指针（`chunk_ids` 过滤 + `_citable_fact_projection`），保存的 Fact 与证据包不改写。
2. **Tool 来源必须启用**：`tavily_web` 按设计 `enabled: false`（不参与采集循环），但补充入库走 `select_sources()` 要求 enabled，导致所有补证以 `supplement_source_unavailable` 失败。修复：补充入库按 id 直接查配置，Source 的启用只约束采集循环。

同时把 research 超时由 300s 提到 600s（`config/models.yaml`，加载器上限仍为 600s）：实测 300s 下大证据包研究调用反复超时（`9315e439` 两次 300s 超时；`05306616` 亦失败），600s 后 `fcfc59c4` 第 2 次尝试 147s 成功。

## 7. 验证命令

- 远程隔离目录 `/tmp/stocks-v04-live-0001`（`TEST_DATABASE_URL` 指向 `stocks-v04-scope-pg` 的独立库 `live_quality_0001`，`Base.metadata.create_all` 建表 + `CREATE EXTENSION vector`）：
  - `pytest tests/test_execution.py tests/test_research_supplement.py` → 89 passed
  - `pytest tests/test_report_api.py tests/test_incremental_evidence.py tests/test_evidence_conflicts.py tests/test_research_supplement.py` → 54 passed
  - `pyflakes` 对 `execution/service.py`、`research/supplement.py`、两个测试文件 → clean
- 部署环境：上述 5 个真实 Run 与报告，`/api/runs/{id}`、`/api/documents/{id}`、`/api/documents/{id}/original` 均以 Owner 会话核对。

## 8. 清单

**通过**

- 真实采集（四来源，0 失败）→ 入库 → 解析/分类/索引 → 检索 → 市场与个股报告全链路
- 报告身份、as_of、模型、token、耗时、缺口可查；金额未知保持 null
- 关键结论逐条对回原文（FOMC、IPO 统计、CPI、SEC 指控、CNBC 引语、补充材料）
- 公司直接信息与行业背景分离；指控 vs 事实、观测期 vs 研究时点区分
- 历史 Facts 随历史语义证据进入研究输入（MP-02 生效）
- 迟到资料标注与"不替代配置来源"的处理
- 真实 Tavily 补证 → 入库 → 报告引用闭环

**失败/已修复**

- 采集 Run `7c45c829`：CNBC TIMEOUT + FRED 400（镜像旧代码）→ 同步当前代码后 `c49fdf96` 通过
- 研究 Run `ea1e424e`、`8077cd79`、`05306616`、`9315e439`：`MODEL_RESPONSE_INVALID` / `MODEL_TIMEOUT` → 见第 6 节两处修复 + 超时调整
- 3 份 CNBC 文档的 facts 阶段 `MODEL_TIMEOUT`（未重试）

**未覆盖**

- 增量窗口 `(0, 644]` 仅扫描 1/7 页，需要后续续扫 Run 才能覆盖完整窗口
- 同源转载合并（`merged_origin_group_count = 0`）与同指标冲突在自然样本中未出现，未做真实冲突验证
- 交易日复盘口径未启用
- 部署镜像未重建，当前修复以 `docker cp` 存在于运行容器；容器重建会回退

**排除**

- 定时调度、时区/cron 验收
- 行情/实时价格来源、SEC 全量 companyfacts
- 物理手机端验收
