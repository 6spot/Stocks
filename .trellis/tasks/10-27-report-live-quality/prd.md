# 真实来源到报告的端到端质量验收

## 目标与事实

证明首批真实资料经标准手动入口生成有用的市场及个股报告，并能核查结论与原文的关系。已有受控流程、真实 Jev 文档和真实 Tavily 客户端调用只作前置参考，不替代本次联合验证。

## 前置条件

MP-01、MP-02 修复通过相关检查；首批真实来源已接入；市场和个股任务的对象及时间口径已明确。分类可见性单独验收，并在父任务最终复核中核对。若前置尚未完成，只登记阶段结果，不把本任务标为完整通过。

## 验收标准

- [x] 在测试服务器当前任务隔离环境，以标准入口手动完成真实采集、原件保存、解析、真实 Jev 标签及必要实体/生成/Embedding 调用、检索和报告，记录实际依赖与配置身份。
  真实采集 Run `c49fdf96`（SEC/Fed/CNBC/FRED，0 失败）；处理阶段 parse/classify/index 53 completed；
  真实 Jev `jev-1.14.0` + 实体抽取、`bge-m3` 1024 维；研究模型 `deepseek-v4.1-flash`。依赖与配置身份见
  `acceptance.md` 第 1 节。
- [x] 至少形成一份市场情报报告及一份目标公司报告，保存 Run/Document/Report 身份、研究 as_of、模型请求/报告身份、耗时、可获得的 usage、预算消耗和缺口；未知金额保留 unknown/null。
  本轮：市场简报 `e3a6b8fe` → report `fe3ea7e6`（as_of 2026-10-28T01:38:54Z，3 次调用，562,419 token，310s）；
  SpaceX `62fa1697` → report `d1546495`（521,672 token，233s）。供应商未返回金额，`cost_usd` 保持 null 且
  `cost_unknown_calls` 如实计数。
- [x] 关键结论逐条检查支持它的证据；数值、单位、期间及公司关联准确，引用可访问实际原件，不用搜索摘要冒充全文。
  逐条对回原文：FOMC 25bp/3.75%-4%/12–0、SEC DERA 208 宗 IPO/$137B、CNBC CPI 3.7%/核心 2.6%/+0.3pp、
  SEC 2026-98 近 $3,000,000 基金层面损失、CNBC "frontier models" 引语、NSSL $1.6B 合同、EDGAR S-8/S-1/Note 18。
  `/api/documents/{id}/original` 返回真实字节（含 Tool 补充文档，报告已注明是抽取文本而非全文）。
- [x] 取样检查论坛观点、重复转载/冲突、公司直接信息与行业背景、历史 Facts、迟到资料以及关键遗漏；自然样本不足时明确未覆盖，不伪造真实事件。
  来源观点逐条标注（Monaghan/Guha/Ryan/花旗）；公司直接信息与背景分离（122 个入选 chunk 中仅 1 份含
  "SpaceX"）；历史 Facts 随 `historical_context` 组进入输入（13 组中 9 组带 `fact_refs`）；补充输入标
  `collected_after_as_of`；转载合并与同指标冲突在自然样本中未出现，已在清单标为未覆盖。
- [x] 实际研究输入与最终报告均检查，不能仅以引用标识有效或报告成功生成断言内容正确。
  同时核对 `evidence_packages`（选中/分组/覆盖计数/`retrieval_truncated`）、`task_runs.research_inputs`
  与 `reports.content`，并用 SQL 回查原文片段（如 `capital call deficiency`、`12 – 0 vote`、`3.7%`）。
- [x] 外部成功按真实调用范围如实记录，受控替代、不可达、未取得权限、成本未知及模型地区限制单列。沿用官方配置和授权测试网关边界，不擅改长期部署。
  成本未知单列；补充 Run 的 `tool_budget_exhausted`/`tool_call_duplicate` 作为缺口记录；本轮的
  超时调整（300s→600s）是容量值修改并已在 `config/models.yaml` 注释与本记录说明。
- [x] 若本轮研究启用 Tavily 补证，另验证真实 supplement Run → 保存 → 报告引用；若未启用，明确未覆盖，不能宣布已有客户端成功就完成补证验收。
  `fcfc59c4`：真实 Tavily MCP → 2 份原文入库（`tavily_web`，T3，complete，真实 URL）→ parse/classify/facts →
  `research_inputs` 追加 2 条 `input`（`collected_after_as_of`）→ 报告 `ca8e39e4` 引用 `[E57][E58][E59]`。
  过程中修复两处真实缺陷（不可引用 Chunk 泄漏、Tool 来源被 enabled 拦住），见 `acceptance.md` 第 6 节。
- [x] 最终清单区分通过、失败、未覆盖及明确排除项，并将内容问题归回所属子任务，形成父任务集成复核输入。
  `acceptance.md` 第 8 节列明四类清单；两处缺陷属本任务执行中发现的研究输入/补证边界问题，已在同任务修复并
  补回归测试；增量续扫、转载合并、镜像重建等遗留项已标明。

## 非目标

不验证定时调度，不默认全量回归，不以临时测试替换正式凭据或持久部署，不把此前排除事项重新纳入强制验收。

## 执行边界

本轮仅创建需求草案，状态保持 planning；未授权开始实现。启动前完成本任务必要的设计、执行计划与上下文，并复核需求。所有应用测试、静态检查和真实调用仅在测试服务器 `100.120.14.84` 的任务隔离目录执行；本机仅编辑和任务管理。不做数据迁移或旧版兼容，不引入额外服务、多 Agent 平台或证券主数据库。凭据不入库、不入任务记录或日志。

父任务：[真实来源驱动的市场与个股报告闭环](../10-27-real-source-report-loop/prd.md)。
