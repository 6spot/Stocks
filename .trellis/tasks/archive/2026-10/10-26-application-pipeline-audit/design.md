# 审查设计

## 审查方法
以一次手动端到端执行为主线，先验证正常路径，再审查阻断点。追踪同一批采集数据到处理结果、研究输入和报告引用，避免把各模块独立成功当作闭环成功。定时调度排除在本轮范围之外。

## 模块与入口
| 环节 | 现有入口 | 核对重点 |
| --- | --- | --- |
| 手动入口与启动配置 | compose.yaml、config/tasks.yaml、config/sources.yaml、config/models.yaml、Task 触发 API | 采集/研究入口、Worker、凭据、来源和模型可用性 |
| 任务与后台执行 | backend/src/stocks/worker.py、execution/executor.py | 手动 Run 领取、后台处理、超时、失败与恢复 |
| 采集入库 | collection/、repository.py、s3.py | 原文、唯一性、发布时间、采集时间、事务与重复触发 |
| 后台处理 | processing/runner.py、processing/service.py、processing/processors.py、classification/service.py | 阶段状态、失败重试、标签及 Fact 的持久化和就绪条件 |
| 研究与选证 | evidence/、research/service.py | 标签过滤、as_of、时间窗口、增量水位、处理中或缺失数据 |
| 报告与交互 | report API、frontend/src/features/research/、frontend/src/features/runs/ | Run 与 Report 的关系、引用、失败可见性和页面闭环 |

## 必须追踪的边界
1. 手动触发到 Run：页面/API 能否创建或返回活跃 Run，Worker 能否领取并执行。
2. Run 到 Document：部分来源失败与部分写入如何表达，重跑是否产生重复或遗漏。
3. Document 到处理结果：后台领取、阶段失败、超时及重启后是否还能处理；标签、Fact 与处理状态是否一致。
4. 处理结果到 Evidence：哪些状态可被选取，分类和标签字段是否一致；新增及更新数据是否被增量遗漏。
5. Evidence 到 Report：空证据、模型错误、工具错误、无效引用时 Run 和报告如何保存与呈现。
6. 操作闭环：采集 Run 完成是否意味着数据可供研究，如何判断后台处理就绪；研究过早触发或处理失败时能否诊断和恢复。

## 执行前事实与执行结果
- 当前配置存在手动采集与研究任务；已完成同批受控闭环，真实模型/来源联合运行未验证，证据见 audit.md。
- Worker 包含 Run 执行与 Document 后台处理入口；本轮通过实际执行函数与 API 验证交接，未启动长期 Worker 进程。
- 定时示例未启用与本轮目标无关，不列为缺陷或阻塞。

## 验证策略与限制
在指定测试服务器隔离目录验证一条同批数据的手动闭环，记录 Collection Run、Document、处理批次、分类标签、适用的 Fact、Research Run 和 Report 的关联。优先使用可用的真实来源和已授权模型；受控来源或假模型可定位内部边界，但只能报告为受控验证，不能据此宣称真实应用已完整跑通。先验证正常路径，再针对实际断点选择最小相关测试。配置读取仅输出开关及字段是否存在，不输出密钥；外部依赖不可用时明确停在哪一段。

## 新增范围：专用分类模型
- 现状：models.fast 与 models.research 共用 provider chat 和 MODEL_NAME；classification 路由 primary=fast、fallbacks=[]，当前没有配置回退到研究模型。
- 目标：分类模型拥有独立 provider、模型名、请求地址及密钥，通过服务器环境变量配置，研究/Fact 模型配置保持原职责。使用 CLASSIFICATION_API_BASE_URL、CLASSIFICATION_API_KEY、CLASSIFICATION_MODEL_NAME；官方协议 typesafe，base URL 为 https://api.typesafe.ai/v1，请求路径 /systemone，默认模型名示例 jev-latest，使用 Bearer 认证。
- 原代码边界：backend/src/stocks/decision.py 最初仅接受 openai_compatible，调用 ModelClient.research_chat。本轮已增加 typesafe 原生适配；Jev 的 state/questions 决策不能直接作为 Chat Completions 使用。
- 官方协议：POST /v1/systemone，{model,state,questions}，返回 {model,answers,usage}。单选为 choice，候选键在 criteria，响应 choice；独立多标签及 should_extract 使用 noul（0..1 真概率），不得把概率当 bool。按配置阈值转换，所有问题身份、类型、候选和数值须严格校验。
- 公司名和股票代码为开放实体：使用独立 classification_entities 生成模型角色（复用现有 chat provider、研究模型），只抽取原文明确提及的实体和原文依据，不以词表、Watchlist、正则或证券主数据代替。该步骤是显式职责拆分，不是 Jev 故障回退。
- 不硬编码分类业务判断或置信度门槛；沿用 taxonomy 中的分类词表和实体约束，新增阈值配置并校验范围。原文以数据传入，不能更改系统指令。
- 返回合并为现有 ClassificationDecision，保留原文定位/输入批次及两种模型调用的身份、用量，现有 Processing 失败可见性与重试不变。调用缺失、异常或实体输出无效时失败，不保存空成功。
- 完整接入涉及环境示例、Compose 变量透传、provider/分类适配、文档及最小相关远程验证，不能只修改 models.yaml 后宣称已可用。

## 交付与回滚
产物为任务目录中的 audit.md 和验证记录。除明确授权的 Jev 原生接入和分类配置变更外，审查不修复其他业务代码或部署生产服务；临时数据与服务只归属当前隔离目录，按测试规范清理本任务资源。其他修复需求从问题清单另定范围。
