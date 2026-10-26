# 标签词表与手动研究闭环：验收

## 结果

代码、默认配置与操作文档已实现；指定服务器相关回归 **128 passed**，全新数据库初始化/模型一致性检查成功。同批三文档闭环分别完成全受控模式与真实 Jev 分类模式，原始日志和关联证据见 [verification/README.md](verification/README.md)。用户已确认提交方案，功能提交为 77c86ff；没有生产部署或推送。

## 配置职责

- `config/taxonomy.yaml` 管全局标签的规范 ID、显示名称、定义和同义词。Jev 与完整生成式分类消费同一语义；输出仍保存规范 ID。修改语义改变配置指纹，完成文档需要显式 reprocess。
- `config/tasks.yaml` 管研究目标、公司/代码、行业/主题范围和自然语言 queries；已加入 `nvidia_research` 手动示例。实际配置加载测试通过。
- 公司和代码从原文明示内容开放抽取，不建立证券主数据库、不由行业或研究范围补出代码。全局分类不汇总所有 Task 的关键词；新 Task 可以复用既有文档及标签。
- 研究目前依靠正文语义查询和变化窗口取材；Scope 不是全标签精确过滤器。direct/background 表达查询用途，不能证明文章点名公司。

## 同批运行结果

三篇材料均为虚构 Northstar Silicon/NSIL 测试文章，**不是 NVIDIA 真实新闻**。NVIDIA 示例验证配置可加载；完整运行使用同构的虚构公司配置，避免把合成数值当作真实证券事实。

| 材料 | 真实 Jev 分类 | 实体角色结果（受控） | Fact 阶段 |
| --- | --- | --- | --- |
| 明示 Northstar Silicon（NSIL）与季度收入 | company / earnings；semiconductors、hardware | 公司及 NSIL，原文 quote 可核对 | completed，保存一条虚构 42 million USD 收入 Fact |
| 只有 Northstar Silicon 公司名 | company；semiconductors、hardware | 只有公司，`symbols=[]` | `should_extract=false`，数据库确认 skipped / NOT_APPLICABLE |
| 美国半导体需求与封装背景 | industry；semiconductors、hardware | `companies=[]`、`symbols=[]` | `should_extract=false`，数据库确认 skipped / NOT_APPLICABLE |

真实 Jev 均返回 `jev-1.14.0`，三次调用各 36 个问题、各一次 attempt，无 fallback。原生调用合计 18,473 input / 2,417 output / **20,890 tokens**，客户端记录耗时合计 4,445ms，费用未知。模型原始答案、请求语义和双角色调用身份都已保存；没有为通过验收修改真实答案。

真实分类模式的关键关联：

- Collection Run：`c69740b2-a591-45ba-b364-30d3f2eeb6fe`。
- Research Run：`91385cb2-234e-49ab-8316-8db532ee453b`。
- Report：`e5b1c502-0b13-4d6c-8828-7ab3f8fff8fa`。
- 收入 Fact：`3ce9d2a6-8151-4baf-b1c7-b47fc11b0f5e`。

三篇均完成真实 S3 原件写入、真实 PostgreSQL 持久化、解析和向量索引；研究实际输入包含三篇及适用 Fact，保存的总结引用三篇。逐条检查 Document/version/Chunk、证据详情、原件响应 SHA-256 和下载字节一致。可阅读 [实际保存的报告](verification/taxonomy-flow-20261026170028-3be571/report.md) 与 [精简关联摘要](verification/acceptance-summary.json)。

报告仍保留 `no_background_selected` 覆盖提示：本批三组材料通过增量扫描进入 `new_information` 组，而不是单独 `background` 组。行业正文实际已入研究输入并被引用；这不证明后台语义组计数、检索相关性或 Scope 精确过滤已实现/验证。原始提示与报告没有被改写或删除。

## 证据边界

- **真实运行**：API 登录/CSRF、手动触发、Worker 执行函数、采集与处理管道、PostgreSQL/pgvector、S3、报告保存/读取、引用及原件下载；第二次完整模式额外使用真实 Jev。
- **受控依赖**：来源 Feed/正文、实体生成、Fact 抽取、embedding 数值、研究总结响应。固定向量只验证索引/取材管道，不证明语义排序质量。报告生成响应与材料均为合成内容，不能据此评价真实研究模型的投资判断。
- **未验证**：真实来源与所有生成/向量模型联合闭环、真实 NVIDIA 公司材料、浏览器界面、长期 Worker 进程、Compose 构建及生产部署。`external_success=false`。
- 全受控模式与真实 Jev 模式是两次独立同批运行，不把两个 Run 或前一任务独立成功拼成全部真实闭环。

## 复核与交付状态

独立代码复核仅发现 quickstart 错把分类字段/实体 quote 描述为已在 Web 展示。已改成实际已登录文档 API 与准确字段路径，明确当前 Web 尚未展示；未为本任务增建 UI。工程规范补充“候选语义须进入模型请求和完整配置指纹”的可执行约定。

前一任务 MP-01（自定义 Worker 配置路径不一致）和 MP-02（历史语义文档已有 Fact 未入包）仍是已确认未修复问题；本任务没有扩大修复范围。三文档脚本显式对齐 Worker/API 路径，使用本批新材料，不声称覆盖这两项缺陷。

验收脚本最初三次失败属于测试设施/脚本：缺少 S3 测试凭据、使用错误登录路径、Source fixture 不符合真实 schema；都发生于真实 Jev 调用之前。修正后先完成全受控闭环，再完成真实 Jev 模式。详见 [执行说明](verification/execution-notes.md)，不把失败写成通过。

最终独立证据复核确认三类实体/Fact 结果、所有原件哈希验证与受控/真实边界一致；复核时 README 尚有旧的“待完成”段落，主会话已在收尾时同步为实际结果（见 research/acceptance-review.md 的时序说明），未修改应用代码或重跑已通过检查。

本任务两个容器、匿名 PG 卷、整个远程目录及私有凭据已清理，SSH 转发关闭，详见验证 README 与 cleanup.log。具体分组见 commit-plan.md；用户已按项目 Phase 3.4 回复“行”确认，验收记录、归档与日志收尾按该方案执行。
