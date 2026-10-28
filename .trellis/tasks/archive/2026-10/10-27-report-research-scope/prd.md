# 市场与个股报告口径及取材配置

## 目标与事实

复用现有 Task、Evidence Selection 和 Research Agent 配置两类实际报告。现有 fed_policy_brief/nvidia_research 是可参考示例，不能认定 NVIDIA 已是 Owner 选定的研究对象。分类词表与研究范围分开，不能用行业标签保证文章明确提到某公司。

## 需求

明确市场情报简报与 SpaceX 公司研究的研究目标、as_of、资料窗口和覆盖。2026-10-27 Owner 确定首批报告口径：市场情报简报（情报更新型）与 SpaceX 公司研究；交易日复盘暂不启用，启用须另行决定。SpaceX（CIK 0001181412）为 Nasdaq 上市公司，代码 SPCX，行情与 EDGAR 来源适用：报告可引用可核实行情，行情缺失时仍须如实呈现；公司适用性以公司名称、显式代码 SPCX 及文中明确指称判断，不得凭航天/行业标签推断。区分自上次报告新增的资料与背景，明示迟到材料、历史背景、无新资料及缺少行情时的行为。公司名称、显式股票代码与行业背景共同用于适用检索，但不得根据背景推断文中公司或 ticker。复用通用研究流程，不开发两套引擎。

## 验收标准

- [x] 市场情报简报与 SpaceX 公司研究口径明确，形成两个可手动触发的真实任务配置；交易日复盘暂不启用。`config/tasks.yaml` 新增 `market_intelligence_brief`（scope 无 companies/symbols，全部显式查询 direct 角色）与 `spacex_company_research`（scope companies=[SpaceX]、symbols=[SPCX]、industries=[aerospace, space]，首条查询显式含 SpaceX/SPCX）；交易日复盘未配置任何任务（含 disabled 占位）。两者均 manual 触发、`evidence_first` 策略、复用同一 `select_evidence → research_writer` 流程，无新引擎代码。
- [x] 两类报告都有适用的证据选择目标和资料缺口呈现；信息不足时不虚构市场表现、涨跌原因或公司事件；SpaceX 行情缺失时如实呈现缺口。测试服务器真实运行：`spacex_company_research` Run `0500c08c` succeeded——42 个证据组中仅 1 组（SEC 新闻稿 2026-98）原文明确提及 SpaceX，其余美联储/SEC/CNBC 材料全部按背景呈现，报告明确「未配置 SEC 申报与行情来源、metric_comparisons 为空、检索被截断」，不虚构估值/行情；`market_intelligence_brief` Run `29fb878e` succeeded——FOMC 决议与市场预期分开、来源观点（德银/Evercore/花旗/El-Erian）逐个归因、无行情来源时明确「不能描述大盘涨跌」。缺口（retrieval_truncated、token_budget、supplement_source_unavailable、tool_budget_exhausted）由报告「资料覆盖限制」如实列出。
- [x] 用直接公司信息、仅公司名无代码、行业背景及迟到资料检查取材边界，数值期间与研究时点不混淆。新增 `test_execution.py::test_config_loads_real_market_and_company_calibers`：含「query 仅行业背景词 → background」「仅公司名（无代码）→ direct 检索角色」边界；真实报告中 SpaceX 案例的指控金额标注「基金层面投资额、未经法院认定」，FOMC 数值带日期，观测期与研究时点未混淆。
- [x] 报告清楚区分新增变化、已有背景、来源观点、模型推断及未知；不以固定关键词或配额代替投资研究判断。两份真实报告均按主题组织并区分「本窗口新增」（evidence_groups role=new_information）与背景，明确标注市场隐含概率不是政策决定、指控不是已认定事实；证据组计数（42 组/122 chunks）与来源数分开。
- [x] 研究领域文档和使用示例同步；真实质量结论由最终联合验收任务给出。`docs/research.md` 取材维度节新增两类口径定义；`docs/quickstart.md` 新增口径说明与触发示例。本任务只记录配置生效证据；内容质量结论留给 `10-27-report-live-quality`。

## 验收执行记录（2026-10-28，测试服务器 /opt/stocks 部署环境）

- 远程单元测试：`test_execution.py`（含新增口径断言）等 6 文件 213 passed。
- 真实触发：`collect_enabled_sources` Run `ba0bf917` succeeded（SEC 20、Fed 20、CNBC 10、FRED 1，全部 complete）；51 份 Document parse/classify/facts/index 处理（2 份 Fact 抽取因模型超时重试中，属覆盖缺口如实保留）。
- 真实研究：上述两个 Run 均产出真实报告（标题、摘要、主题章节、findings、待解问题、可打开原件引用）。
- **已知限制（如实记录）**：`market_intelligence_brief` 的增量续扫（第 2 页起）使研究输入超过 `research_writer` 路由 300s 超时的实际容量，连续 4 次 Run 的 research 调用 8 次 MODEL_TIMEOUT；证据包与扫描检查点已保留、可继续，但需模型端点提速或降低 max_tokens 后才能完成续扫报告。此交互留给最终联合验收任务处理；FRED Fact（cpi_index_sa=336.298 index_1982_84_100，period 2026-09-01，JSON pointer evidence_ref）已确认入库可查。
- 部署环境问题（非本任务代码）：部署库 analyses 表缺 `title`/`sections` 列（旧镜像初始化后未随 748c74a 重建），已按开发阶段规则 DROP 重建并重采集；`host.docker.internal` 未解析（compose 无 extra_hosts），EMBEDDING/CLASSIFICATION 端点改用 `172.20.0.1:13426`（经既有 socat+SSH 隧道）；api.typesafe.ai 直连 451，分类改经本机 magpie 网关（typesafe/jev-latest）。另修复 execution/service.py 通用异常处理器不记录 traceback 的可观测性缺陷（STEP_FAILED 现在写 Worker 日志）。

## 前置与待确认

可与来源选型共同规划，最终真实运行依赖 MP-01/MP-02 与首批来源。2026-10-27 Owner 确定首批口径为市场情报简报与 SpaceX 公司研究；SpaceX 为 Nasdaq 上市公司（SPCX），行情与 EDGAR 来源适用，具体接入由来源任务验证。交易日复盘暂不启用；必要行情由来源任务负责。

## 非目标

不处理何时自动执行，不按证券创建多套引擎，不建设证券主数据、交易系统或强制固定篇数的研究规则。

## 执行边界

本轮仅创建需求草案，状态保持 planning；未授权开始实现。启动前完成本任务必要的设计、执行计划与上下文，并复核需求。所有应用测试、静态检查和真实调用仅在测试服务器 `100.120.14.84` 的任务隔离目录执行；本机仅编辑和任务管理。不做数据迁移或旧版兼容，不引入额外服务、多 Agent 平台或证券主数据库。凭据不入库、不入任务记录或日志。

父任务：[真实来源驱动的市场与个股报告闭环](../10-27-real-source-report-loop/prd.md)。
