# 真实来源驱动的市场与个股报告闭环

## 目标与授权范围

用户希望从 RSS、论坛及 API 获取股票市场资料，经过 Jev 分类和必要处理后保存，并手动生成市场或个股报告。2026-10-27 用户授权根据整体评估创建任务；本轮仅建任务，不等同于批准任何子任务开始实现或选定供应商。

## 已确认基础

现有代码具备 RSS/基础 HTTP API 采集、原文保存、原生 Jev 标签判断、独立实体抽取、结构化数据、语义检索以及报告和引用回溯。默认来源以 SEC/美联储为主，不能据此宣称市场覆盖完整。已有受控完整流程及部分真实依赖验证，但未完成本轮要求的真实市场和个股报告联合验收。

MP-01 与 MP-02 是已知未修复缺陷，见[既有审查](../archive/2026-10/10-26-application-pipeline-audit/audit.md)。真实 Jev、Tavily 的独立成功不得替代本任务验收；此前范围排除仍适用。

## 子任务与需求归属

| 优先级 | 子任务 | 独立交付 |
| --- | --- | --- |
| P1 | [配置一致性](../archive/2026-10/10-27-report-config-consistency/prd.md) | 修复 MP-01，确保新增来源和模型使用同一组配置 |
| P1 | [历史 Facts 完整性](../archive/2026-10/10-27-report-historical-facts/prd.md) | 修复 MP-02，历史语义证据带入已有结构化数据 |
| P1 | [首批真实来源](../10-27-report-real-sources/prd.md) | 选型并接入真实 RSS、API 和讨论来源，明示限制 |
| P1 | [报告口径与取材](../10-27-report-research-scope/prd.md) | 明确并配置市场情报与个股研究目标、时间和覆盖 |
| P2 | [分类依据可见性](../10-27-report-classification-visibility/prd.md) | 可以检查标签、公司/代码、依据及处理状态 |
| P1 | [真实质量验收](../10-27-report-live-quality/prd.md) | 真实采集到市场及个股报告的联合证据与内容核查 |

## 推进关系

优先修复两项已知缺陷；来源选型和报告口径可以同步规划。真实来源适配应使用修复后的配置入口；真实质量验收以两项修复、首批来源及报告配置完成为前置。分类展示可独立推进，其最终检查并入集成复核。父子链接不代表自动执行依赖，子任务也须记录前置条件。

## 跨任务验收

- [x] MP-01/MP-02 有对应远程回归证据。
  MP-01：commit `0432e6a`，Worker/CLI 路径统一到 Settings，远程回归通过。
  MP-02：commit `be6764c`，语义路径经 `facts_for_documents` + 去重/冲突标记携带 Facts，3 个回归测试；
  本轮真实 Run `62fa1697` 复核：13 个 `historical_context` 背景组中 9 组携带 `fact_refs`。
- [x] 首批来源、目标公司及报告时间口径已明确；不能用默认示例冒充 Owner 已选定的产品配置。
  `config/sources.yaml`：`sec_press_releases`、`fed_press_releases`、`cnbc_markets`、`fred_cpiaucsl`（均 enabled）；
  `config/tasks.yaml`：`market_intelligence_brief`（无 companies/symbols）与 `spacex_company_research`（SpaceX/SPCX，
  首条查询 direct）。commit `d2c0fd8`，`docs/research.md` 记录两类口径。
- [x] 手动完成真实资料获取、保存、Jev 分类、检索及市场和个股报告；保存可核对的来源与 Run/Document/Report 标识。
  采集 `c49fdf96`；市场简报 `e3a6b8fe` → report `fe3ea7e6`；SpaceX `62fa1697` → report `d1546495`；
  Tavily 补证 `fcfc59c4` → report `ca8e39e4`。证据包、Document 版本与 `/original` 均可回查。
- [x] 报告区分事实、来源观点与推断，数字的单位/期间明确，引用可回原文；缺失的行情或资料如实呈现。
  逐条核对：FOMC 25bp/3.75%-4%/12–0、SEC DERA 208 宗 IPO/$137B、CNBC CPI 3.7%/核心 2.6%、
  SEC 2026-98 近 $3,000,000（基金层面、指控阶段）、CNBC "frontier models" 引语、NSSL $1.6B、
  EDGAR S-8/S-1/Note 18。未配置行情来源时报告明确写“无法评估市场具体涨跌”，未编造价格。
- [x] 来源摘要/全文、实体识别限制、重复/冲突及关键遗漏均有实际检查记录；受限项列明，不计通过。
  分类可见性：SEC 4 公司实体 + 逐条原文引用、FRED 零实体不推断、CNBC 输入截断、失败保留旧结果与重试闭环。
  研究侧：122 个入选 chunk 中仅 1 份含 "SpaceX"（直接信息与背景分离）；转载合并与同指标冲突在自然样本中
  未出现，已在验收清单标为未覆盖；Tool 补充材料标 `collected_after_as_of`。
- [x] 最终集成复核汇总各子任务证据与剩余问题，不因单个真实模型或 MCP 成功而宣布全链路验收。
  见下节“集成复核结论”：两个真实缺陷（不可引用 Chunk 泄漏、Tool 来源被 enabled 拦住）在本轮修复并补回归；
  仍受限项：增量窗口只扫 1/7 页、部署镜像未重建（热修随容器重建丢失）、3 份 CNBC facts 阶段超时、
  转载/冲突未取得真实样本。

## 集成复核结论

| 子任务 | 状态 | 证据 |
| --- | --- | --- |
| 配置一致性（MP-01） | 通过 | `0432e6a` + 远程回归 |
| 历史 Facts（MP-02） | 通过 | `be6764c` + 真实 Run `62fa1697` 的 `fact_refs` |
| 首批真实来源 | 通过 | `018f4a3`/`5681b9e`，采集 `c49fdf96` 四来源 0 失败 |
| 报告口径与取材 | 通过 | `d2c0fd8`，两份 Task 真实产出报告 |
| 分类依据可见性 | 通过 | `b35acea`，真实页面核对 SEC/FRED/CNBC 与重试闭环 |
| 真实质量验收 | 通过（含受限项） | `acceptance.md`，本轮 5 个真实 Run |

修复缺陷：Fact 来源 Chunk 未入选时 `chunk_id` 泄漏进模型输入（导致 `MODEL_RESPONSE_INVALID`）；Tool 来源
`enabled: false` 被补充入库拒绝（导致全部补证失败）。research 超时 300s→600s。

剩余问题（不在本轮验收范围或需后续处理）：部署镜像仍是旧代码，本轮修复靠 `docker cp`；容器重建会回退，
需要一次成功的 `compose build`。增量窗口 `(0,644]` 每个 Run 只扫一页，需续扫 Run 才覆盖完整窗口。
同源转载合并与同指标冲突尚无真实样本。3 份 CNBC 文档 facts 阶段 `MODEL_TIMEOUT` 待重试。

## 不在范围内

定时调度、cron/时区调度验收、交易执行、自动按证券拆任务、多 Agent 平台、证券主数据、通用论坛爬虫平台、付费服务自动订阅及此前排除的 SEC 全量 companyfacts 链路。行情用于报告证据，不建设实时行情系统。

## 待推进时明确

2026-10-27 已定：免费来源（允许免费 API key）；首批报告口径为市场情报简报与 SpaceX 公司研究；目标公司 SpaceX 为 Nasdaq 上市公司（SPCX，CIK 0001181412，上市状态当日经 SEC EDGAR 核实）；首批来源候选与凭据申请清单见[来源子任务](../10-27-report-real-sources/prd.md)。仍待定：交易日复盘是否启用；均在对应子任务规划中解决，不阻塞本轮任务登记。复杂子任务的 design.md、implement.md 及所需上下文将在进入实现前补齐，本轮不宣称规划已收敛。

上级：[Stocks 路线图](../10-20-stocks-roadmap/prd.md)。领域规范沿用 [数据](../../../docs/data.md)、[执行](../../../docs/execution.md)、[研究](../../../docs/research.md)及[界面](../../../docs/interface.md)，本任务不建立第二套规范。
