# 设计：市场与个股报告口径及取材配置

## 结论

不开发第二套研究引擎。市场情报简报与 SpaceX 公司研究都是 `config/tasks.yaml` 中
`type: research`、`trigger: manual`、`strategy: evidence_first` 的 Task，走同一条
`select_evidence → research_agent` 流程与同一个 `research_writer` Agent。本任务的
交付物是**配置 + 口径文案 + 边界检查**，不是新代码路径。

## 两个真实任务

### 1. `market_intelligence_brief`（市场情报简报，情报更新型）

- **scope**：`regions: [US]`、`topics: [macro, policy, market_sentiment]`。不设
  companies/symbols——这正是「情报更新型」与个股研究的结构区别：`_query_role`
  对没有 companies/symbols 的 scope 全部给 direct 角色。
- **objective**：明确三类可报告内容（宏观与政策变化、监管与市场结构事件、市场
  情绪与结构性话题），并要求区分「本次窗口新增」与「历史背景」；无新资料时如实
  写明，不虚构市场表现或涨跌原因。
- **取材**：显式 queries 覆盖宏观/政策与市场结构两条线，并把已接入的结构化数据
  （FRED 宏观 Fact）与 press 类来源一并列为主题；`source_ids` 不设白名单——
  口径由查询语义决定，不由硬编码来源清单决定（开发规则 5）。
- **资料窗口**：增量窗口由既有 ChangeWindow 机制决定（首次 Run 从头扫描，
  后续 Run 沿用成功游标），不新增窗口配置；`as_of` 是研究参考时间，迟到材料
  按 `published_at` 真实呈现，行为已在 research.md 定义，配置不重复定义。

### 2. `spacex_company_research`（SpaceX 公司研究）

- **scope**：`companies: [SpaceX]`、`symbols: [SPCX]`、
  `industries: [aerospace, space]`、`regions: [US]`、
  `topics: [company, launch_industry, policy]`。CIK 0001181412 由来源任务负责
  接入（EDGAR/行情来源），任务配置不复制主数据。
- **objective**：明确「直接公司信息（原文提及 SpaceX/SPCX）」与「行业、政策、
  供应链等背景」分开检索与呈现；公司适用性以公司名称、显式代码 SPCX 及文中
  明确指称判断，不得凭航天/行业标签推断；行情或 EDGAR 材料缺失时如实呈现缺口，
  不虚构公司事件或市场表现。
- **取材**：第一条 query 显式含 `SpaceX SPCX`（direct），其余为发射产业、
  商业航天政策、太空经济等背景 query（background）。`_query_role` 用词边界正则
  匹配 scope 中的 companies/symbols 判定角色，配置即生效，无代码改动。
- **行情口径**：本任务不接入行情来源；`select_evidence` 无行情 Fact 可取时，
  覆盖缺口按既有机制进入报告（`gaps`、`unexamined_count`、coverage 呈现），
  报告必须写「缺少行情」而不是跳过。行情来源由来源任务后续接入，配置届时只加
  Source，不改研究任务。

### 交易日复盘：不启用

不创建 disabled 复盘任务占位。现有 `scheduled_market_brief` disabled 示例保留
（它演示 schedule 触发形态），复盘类任务等 Owner 明确启用决定后再配置。

## 边界检查（验收标准 3 的落实）

用受控测试覆盖 `_query_role` / 配置加载边界，真实 Run 只需覆盖直接命中与缺口：

| 场景 | 期望 |
| --- | --- |
| query 含 `SpaceX` 或 `SPCX` | direct |
| query 只含公司描述词（如 launch industry）无公司名 | background |
| scope 有 companies 但 query 全无命中 | background（不整包拒绝） |
| 仅公司名无代码的材料 | 仍 direct 检索；实体是否提及由分类/实体依据决定，不推断代码 |
| 数值期间与研究时点 | Fact 保留 period/available_at，报告引用冻结投影，既有 V2-03 机制，不新增 |

直接/背景角色的既有语义（`research.md` V1-06）保证行业背景命中不会被改写成
「该公司被直接提及」；任务配置把 SpaceX/SPCX 写进 scope 与首条 query，使角色
判定有依据。

## 配置之外的最小代码改动

无。`_normalize_queries`/`_query_role`、Evidence Selection、ChangeWindow、
覆盖缺口呈现全部已实现（MP-02 后历史语义 Document 也携带 Facts）。新增两个
Task 配置 + `test_execution.py` 的配置加载与角色断言 + 文档使用示例更新。

## 权衡与不做的事

- **不加 `source_ids` 白名单**：口径由 objective/queries 决定；来源增减是
  来源任务的职责。若未来某个任务确需限定来源，配置面已支持。
- **不新增「情报更新型/深度研究型」strategy 枚举**：`evidence_first` 是唯一
  真实策略，两类报告的差异由 scope/queries/objective 表达，与「不开发两套
  引擎」的 PRD 要求一致。
- **不把 CIK/代码写进任务配置**：证券身份只作为检索标签存在于 scope 与来源
  配置，不建主数据（开发规则 2/3）。
- **不用固定配额或关键词代替研究判断**：queries 表达检索意图，报告结构由
  `research_writer` 的既有 prompt 约束（区分新增/背景、不虚构、引用证据）。

## 验证方式

1. `test_execution.py`：新增配置加载断言（两个 Task 的 trigger/scope/queries
   角色、交易日复盘不存在），复用既有 `test_config_loads_manual_nvidia_research_*`
   模式。
2. 远程真实触发：测试服务器 `/opt/stocks` 部署环境已有真实 SEC/Fed 材料与模型
   key。重建镜像载入新配置后，手动触发两个 Task，检查 Evidence Package 的
   query roles、coverage 缺口（SpaceX 无直接材料时报告必须如实说明）、报告
   区分新增/背景/推断。真实质量结论归最终联合验收任务，本任务记录配置生效证据。
