# 审查导航与调查记录

## 当前执行状态
- Jev 官方原生接入与独立配置已实现；公司/股票开放抽取保留独立生成模型角色。
- 指定服务器同批受控完整 smoke 已通过，PostgreSQL/pgvector 与 S3 为真实隔离依赖；此 smoke 的外部来源/模型 HTTP 为受控响应。随后独立 Magpie 探针已真实调用 Jev，不能把两次运行拼成全部真实外部依赖的完整闭环。
- 两个原有问题已远程复现：MP-01 自定义配置路径交接不一致，MP-02 历史语义证据漏带已有 Fact。本任务记录而不修复。
- 结论与实际验证以 `audit.md`、`verification/README.md` 为准；下述“最初”段落保留调研起点，不代表当前实现状态。

## 调研开始时的静态事实
- 用户已明确先验证手动闭环，定时调度不在本轮范围内。config/tasks.yaml 存在手动采集与研究入口；定时示例未启用不计作问题。
- backend/src/stocks/worker.py：包含调度、Run 执行与文档后台处理入口；最初尚未证明完整链路，后续已完成受控闭环。
- 调研起点尚未连接服务器或测试；后续已在规定的隔离环境执行验证。本任务始终未启用定时任务或部署生产服务。

## 领域文档读取要求
以下原始文档较长，不直接注入全文。Agent 必须按审查范围主动分段读取，不能把上下文注入的截断内容当作完整契约；只引用本导航也不能代替原始规范。
- docs/data.md：Source/Connector、Document、处理状态、分类标签、Fact、Repository、时间口径和数据质量。
- docs/execution.md：手动 Task/Run、Worker、重试恢复、模型 Provider 和工具权限；本轮跳过 schedule 专项。
- docs/research.md：Evidence Selection、增量/水位、as_of、Research Agent、Analysis/Report 和引用。
- docs/quickstart.md：启动配置、任务定义、Worker、数据库和存储前提；所有命令仍须服从指定测试服务器限制。

## 待读源码与测试
业务源码优先检查手动 Task 触发 API、backend/src/stocks/worker.py、execution/executor.py、ingestion/、processing/、evidence/、research/service.py 和报告 API。
相关候选测试为 backend/tests/test_worker_processing.py、test_processing_retry.py、test_processing.py、test_classification.py、test_change_watermarks.py、test_incremental_evidence.py、test_research.py、test_report_api.py；先检查已有覆盖，只选择能够验证具体结论的用例。
前端优先检查 frontend/src/features/runs/、frontend/src/features/research/ 及其 API 调用。

## 专用分类模型调查
- 最初 backend/src/stocks/decision.py 的 SUPPORTED_DECISION_PROTOCOLS 只有 openai_compatible；本轮新增原生 typesafe。
- 最初 config/models.yaml 的 classification 路由到 fast，与 research 共用 chat/MODEL_*；本轮改为独立分类配置，classification_entities 明确复用生成模型，均没有隐式研究模型回退。
- TypeSafe 官方接口文档：https://docs.typesafe.ai/api/system-one ；Choice 文档：https://docs.typesafe.ai/primitives/choice 。公开协议为 POST /v1/systemone，输入 model/state/questions，固定决策类型返回；不能等同于 Chat Completions 任意 JSON Schema 生成。此结论为外部文档调查，未调用真实接口验证。
- 用户已指定官方原生接口。官方新增核对：noul 返回 0..1 真概率（不是 boolean），choice 候选为 criteria，答案在 answers.<id>。默认模型示例 jev-latest；HTTP 请求须显式传 model。
- 依据 https://docs.typesafe.ai/primitives/noul 、https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook 和官方 Introduction，没有文档化的开放 text/extract 类型；候选选择不能当作生成未知公司或 ticker。采用独立生成模型实体角色保留已有开放抽取。
- SSH BatchMode 认证成功；初次在指定隔离目录获取文档时 curl 出现 TLS EOF（exit 35），通过 web_search 核对。后续用户授权 Magpie 代理，36 个真实问题经当前适配器成功，响应 jev-1.14.0；见 research/magpie-live.md。

## 证据等级
确认问题需给出具体位置、触发条件和影响。代码推断、受控测试通过和实际部署验证分开报告。未验证候选问题不计入已确认缺陷。
