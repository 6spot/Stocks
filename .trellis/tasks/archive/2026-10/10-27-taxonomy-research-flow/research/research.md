# 标签词表与手动研究闭环：有界技术调研

本次为只读源码调查；未运行任何本地/远程测试、模型调用、数据库查询、部署或提交，未接触凭据、隧道及前一任务的远程目录。已阅读根 AGENTS.md、`.trellis/spec/guides/agent-execution-guide.md` 和本任务 PRD。`task.py current --source` 返回 `(none)`，故依明确派发的 `.trellis/tasks/10-27-taxonomy-research-flow` 作为研究归属，不自行更改任务状态。Research Agent 上级写入边界限定任务 `research/`，所以将用户所需单份 research.md 保存为 `research/research.md`。

## 结论

全局分类词表继续归 `config/taxonomy.yaml`；研究的公司/代码、行业、主题、问题继续归 `config/tasks.yaml` 的 `objective`、`scope` 和 `select_evidence.queries`。不汇总所有 Task 的关键词，不因新建研究任务重新分类旧资料，不建股票主数据库。最低充分实现是补全标签语义、将同一语义提供给两个分类协议、修正语义指纹，并添加可编辑的手动股票研究示例及操作说明。

现有 Research Scope/query 足以表达直接公司取材和行业背景取材，暂不需要新增 Task/Scope schema、数据库字段或 Repository 抽象。必须准确说明：当前语义召回使用 query 检索正文，**没有基于分类标签的 Repository 结构化筛选**；direct/background 是查询路径/候选用途，不是已验证的原文提及事实。如果验收要求“按标签精确筛选研究输入”，该能力尚未实现，需要主会话明确纳入设计，不能用新增标签说明宣称已经完成。

## 已存在的实现与证据

| 范围 | 代码证据 | 当前支持 / 局限 |
| --- | --- | --- |
| 标签数据结构与配置 | `backend/src/stocks/taxonomy.py:46`、`:306`、`:343` | `Label(id, description=None)`；五节固定候选；接受字符串或 `{id, description}`；严格拒绝未知配置字段、重复/非法 ID。没有 name 或 aliases。用户提到的 `classification/config.py`、`types.py`、`schemas.py` 均不存在，实际契约集中于 taxonomy.py。 |
| 分类输出契约 | `taxonomy.py:80`、`:147` | schema enum 和 normalize 均只接受规范 ID；公司/代码是开放抽取，不是词表候选或 watchlist。代码有格式约束，无证券身份解析。 |
| 当前词表内容 | `config/taxonomy.yaml` | categories/event_types/topics 大多有 description；industries/regions 仅 ID；无显示名称、别名。现有 topics 没有 AI，可用 description 或显式 query 表达，也可按任务设计新增一个正式主题。 |
| Jev | `backend/src/stocks/classification/jev.py:14` | category/event 为 choice，其他受控标签为逐项 noul；目前语义来自 `description or id`。候选 key 始终为 ID，有显式无事件候选；无需改协议/概率归一化。 |
| 生成式分类 | `backend/src/stocks/decision.py:77`、`:_chat` | messages 含全局 instructions、文档元数据、excerpt，response schema 含 ID enum；**既不发送每个标签 description，也没有完整标签语义**。仅改 YAML 不会让这个协议知道别名。 |
| 开放实体的证据 | `classification/jev.py:entity_schema/normalize_entities`；`decision.py:_classify_jev` | Jev 有限决策成功后，必需的 `classification_entities` 生成角色读取同一有界标题/正文；每个公司/代码有 quote，quote 必须存在于输入且包含对应值；缺失/伪造 quote 拒绝。只提公司可以 symbols=[]；行业背景不生成公司/股票。生成式单次完整分类当前没有这一逐实体 quote 契约，不能声称两个协议都有相同证据结构。 |
| 分类留痕与指纹 | `classification/service.py:taxonomy_fingerprint`、`:classify_config_fingerprint` | 保存当前 classification JSONB、模型/路由/解析批次与实体证据等。**taxonomy fingerprint 只 hash version 和各节 ID；description/instructions 变化当前不会改变它**。新 name/aliases 若不纳入同样会漏记。 |
| Scope 与存储 | `execution/config.py:TaskConfig`；`models.py:TaskDefinition/TaskRun` | scope 为非空 dict，落 JSONB 并随 Run 快照；没有另一个严格的 Scope model。研究问题来自 objective，显式 queries 最多八条。 |
| 直接/背景查询 | `research/service.py:599` | 显式 query 中出现 scope companies/symbols 时为 direct，其余为 background；无公司/代码 scope 时全为 direct。此角色由查询文本得出，不能证明命中的文章直接提及公司。无显式查询时 companies/symbols/regions/industries 全被派生为 direct、topics 为 background；因此默认派生的行业/地域对个股研究存在角色偏差。 |
| 实际检索过滤 | `repository.py:87`、`:589`；`research/service.py:209` | SearchFilters 仅 source_ids 及发布时间/采集时间；select_evidence 当前只传 source_ids，没有标签、公司或 Scope AND 过滤；语义召回读取当前成功解析批次。分类失败不应让正文从语义召回消失。 |
| 增量路径 | `research/service.py:220–350`、`:450` | 固定变化窗口按 source/page 扫描，合并 Document 和 Fact；没有依据公司/行业标签筛掉其他变化。无语义匹配角色的增量 chunk 还有 direct 默认值。因此 stock Scope 不是“增量文档均已证实与股票相关”。此项只记录，不顺手扩大为前一任务缺陷修复。 |
| 报告与引用 | `research/service.py:validate_selected_versions/parse_agent_output`；`docs/research.md` | Evidence Package 固定 Document/version/parse batch/chunk 引用，Fact 合并进证据链，重复命中去重；输出检查结构和引用，不是所有自然语言投资判断的确定性验证。 |
| 手动流程 | `config/tasks.yaml`；`docs/quickstart.md` | 已有 collect_enabled_sources、研究 task、worker、processing status/reprocess、trigger/show/cursor 和报告证据 API；尚无可编辑的具体股票完整示例。 |

## 推荐的最小当前 schema

只选一个清晰格式：五个词表节中的每项统一为 mapping，要求 `id`、`name`、`description`；`aliases` 是可省略的字符串数组，缺省为空。直接更新现有 YAML 和测试 fixture，删除字符串候选的旧输入分支，不保留平行格式或迁移脚本。

```yaml
industries:
  - id: semiconductors
    name: 半导体
    description: >-
      Semiconductor design, fabrication, packaging, equipment and chip demand.
      Use only when the supplied document substantively supports this industry.
    aliases: [semiconductor, chips, 芯片, 集成电路]
```

`name` 是可读名称；`description` 定义范围与排除条件；`aliases` 只向模型提供常见名称及相关词提示，**不是命中规则或研究事实**。例如 chips 并非在所有语境都指半导体。最低方案不增加 alias→ID 的查询解析器；Task 的受控标签仍书写规范 ID，自然语言查询可写中文/英文名称。没有必要把“相关词”再拆成第二套 keywords/related_terms 字段，也不把公司别名列表塞进全局词表。

校验保持严格：拒绝未知字段、非 mapping 项、空 name/description、aliases 非数组或含非字符串/空白项；同一项 aliases 去重策略建议拒绝重复，避免静默改配置。可对人类文本加合理大小上限；不可用上限/关键词替代语义判断。不需要强制不同标签的别名全局唯一，因为相关词可能重叠且不做确定性解析。

在 taxonomy.py 提供一份候选语义序列化/渲染方法（包含 id/name/description/aliases）。Jev choice criteria 的 value 和 noul instructions 使用它，key 保持 ID。生成式分类 messages 传完整候选结构，并明确别名只供理解、必须输出规范 ID；JSON schema enum 不扩大为 name 或 alias。开放实体生成不需要注入候选词，以免候选语义诱发虚构公司或代码。

fingerprint 使用稳定 JSON 序列化，涵盖 version、instructions、全部 label 的 id/name/description/aliases；保持列表顺序，因为候选顺序影响请求。对 normalized 配置取 hash，不包含凭据。语义修改改变 fingerprint，但不自动重跑已有成功 classification；Owner 通过既有 reprocess 明确触发。规范 ID 变化/删除按现有版本约定更新 version；不用数据库 schema 变更。

## 股票研究示例：复用已有配置

以下是一项 Task，可加入现有 tasks 数组，复用已定义 `research_writer` Agent，无需新 Agent、服务或全部股票任务聚合。来源应按真实启用并已采集的 Source 调整；不要声称现有 SEC/Fed feed 必然有 NVIDIA 财报材料。

```yaml
- id: nvda_research_brief
  name: NVIDIA 公司证据与半导体背景
  type: research
  trigger: manual
  enabled: true
  objective: >-
    研究公司的业绩、需求和主要不确定性；分别说明直接公司证据与行业政策背景。
    行业资料未点名公司时只作为背景，材料不足时明确缺口，不推断股票代码。
  strategy: evidence_first
  scope:
    companies: [NVIDIA]
    symbols: [NVDA]
    industries: [semiconductors]
    topics: [trade]
    regions: [US]
  steps:
    - id: evidence
      select_evidence:
        kind: select_evidence
        queries:
          - NVIDIA NVDA reported results guidance data center demand
          - NVIDIA NVDA SEC filing company announcement
          - semiconductor export controls chip manufacturing policy
          - semiconductor supply chain demand capital spending
        limit_per_query: 8
        max_tokens: 30000
    - id: research
      research_agent:
        kind: research_agent
        agent: research_writer
        input: evidence
```

前两条 query 为 direct，后两条为 background；objective 故意不用公司名，避免未来切换无显式 queries 时每条派生 query 都因 objective 含公司名变成 direct。现有查询角色只是取材路径：若第一条召回仅有芯片政策文本，它仍不是原文点名公司的证据。原文“`NVIDIA (NVDA) reported ...`”允许 companies=[NVIDIA]、symbols=[NVDA] 及 quote；“`NVIDIA announced ...`”允许公司名但 symbols=[]；“`New controls restrict advanced semiconductor exports`”可打 policy/regulation、semiconductors、trade，但 companies/symbols 必须为空，研究可以引用其行业背景，不能写政策点名 NVDA。

最小修正（若本任务涵盖默认派生角色一致性）：无显式 queries 的派生条目复用 `_query_role(query, scope)`，使带公司 scope 的行业/地域查询不再固定 direct；需要覆盖无公司市场/行业研究仍为 direct 的行为。不要由此宣称实现了逐文档提及验证或精确标签查询。显式示例本身无需修改研究代码即可工作。

## 实际应影响的文件与定向测试

必改产品/配置文件：

- `backend/src/stocks/taxonomy.py`：Label schema、严格 loader、共享候选语义。
- `backend/src/stocks/classification/jev.py`：choice/noul 提示消费完整语义，保留原概率/实体校验。
- `backend/src/stocks/decision.py`：完整生成式分类提示带候选语义；保留现有 Jev 双角色与调用痕迹。
- `backend/src/stocks/classification/service.py`：完整语义 fingerprint；不改数据库及原有 JSONB 分类业务字段。
- `config/taxonomy.yaml`：统一 name/description/aliases mapping、补齐行业和地域定义。
- `config/tasks.yaml`：一份可编辑手动股票示例。
- `docs/data.md`：唯一分类词表契约；`docs/research.md`：查询路径和事实提及的边界；`docs/quickstart.md`：可操作分阶段流程与示例。必要的验收场景写 `docs/validation.md`，不复制成第二份 schema。

必要测试优先落现有文件：

- `backend/tests/test_classification.py`：更新 `write_taxonomy`/parse fixture；合法完整语义；错误 name/description/aliases；输出 name/alias 仍被拒绝；`test_one_call_returns_tags_and_the_extraction_decision` 断言真实请求携带全部候选语义；`test_config_fingerprint_identifies_route_and_vocabulary` 覆盖只改 name/description/aliases/instructions 也改变 hash，等价配置稳定。
- `backend/tests/test_classification_jev.py`：`test_native_request_probability_threshold_and_explicit_entity_merge` 验证 criteria value/noul 含名称描述别名、key 不变；沿用原有错误概率、越界 ID、实体 quote 缺失/伪造失败、空公司背景、双角色失败保留旧结果覆盖，不重写前一任务已审过的协议实现。
- `backend/tests/test_execution.py`：仅需验证股票示例随实际配置可加载且复用 research_writer，不增独立 Scope 类型或全局 alias resolver。
- 若改派生角色，更新 `backend/tests/test_research.py:test_select_evidence_derives_direct_and_background_queries`，增加带公司 scope 的 industries/regions 为 background、无公司行业研究为 direct，以及显式 direct/background 两类资料能同时入包且去重。若不改派生角色，现有测试可复用，不能把未覆盖当通过。
- `backend/tests/test_incremental_evidence.py:test_failed_classification_does_not_filter_semantic_recall` 和 Document/Fact 合并相关用例仅在检索行为发生变化时定向运行；不默认全量执行。

`models.py`、初始化建表、Repository 搜索、provider transport、Compose、凭据设置均无需为最小语义方案改动。如决定增加精确标签过滤，才重新评估 repository.py、语义检索入口、SearchFilters 与增量/历史一致性，不能只向一个分支加 AND。

## 推荐验收操作（供主会话后续执行，本批未执行）

所有测试/运行验证限定 `100.120.14.84` **本任务新建隔离目录**，与前一任务真实 Magpie/Jev 验证目录/隧道完全分开。按测试指南准备当前源码包，使用当前任务独立项目名/资源；不读取或记录真实密钥。

1. 先跑最小相关文件/用例：classification、classification_jev 和股票配置加载；只有改研究派生逻辑才加对应 research 用例。检查请求确实带中文 name、description、aliases，ID 枚举不变；仅语义变化会改变 fingerprint。受控传输通过不等于真实模型验证。
2. 建同批三份可追溯固定原文：明确公司+代码、只有公司无代码、只有行业政策背景。按正常 ingest→parse→classify→facts（适用则完成，不适用明确 skipped）→index 流程，记录 Document/version/parse batch/chunks、classify 状态、规范标签、实体 quote、实际模型角色/协议/模型身份。测试资料来源明确为受控 fixture，不冒充外部新闻。
3. 处理就绪后手动触发股票研究，用 `stocks.execution show --run-id ...` 和持久 Evidence Package 核对 direct/background 查询及两类材料、Fact 的同源关联、去重、覆盖缺口。注意固定窗口中其他增量资料可能入包，不宣称 scope 为精确筛选器。
4. 研究产出应同时讨论公司证据与适用的行业背景；逐条引文能回到所保存 Document/version/chunk，背景文档的 symbols=[]，不存在“行业出现所以文章提及 NVDA”的虚构标签。引用校验通过仍需对该语义要求做内容审查。
5. 一条真正外部来源→真实配置分类→真实研究模型链路须另记真实来源、时间、模型身份和引用证据；网络/配额/来源不覆盖股票时明确缺口，可保留“真实 Fed 行业背景 + 受控公司材料”两类结果，不能写成全链真实公司资料已通过。前一任务 642 远程通过/当前并行真实验证由主会话引用原报告，本调研不复跑或背书。
6. 操作文档给出 collect_enabled_sources 的 trigger、processing status、必要的显式 reprocess、研究 trigger/show、报告与证据查看路径。采集 Run 成功不代表处理已就绪；不以固定 sleep 替代 stage 状态确认。

## 边界、阻塞与交回

- **没有阻塞最小词表语义设计的外部条件**；本批产物完成，不需要新权限或再次取得创建/实施授权。
- **需要如实限定验收措辞**：现有标签仅入库存储和分类语义，Scope/query 实际依赖正文语义召回，并非完整分类标签筛选。若 PRD 要求后者，本批建议作为明确新增设计范围，而非悄悄承诺。生成式完整分类也不能声称已具备 Jev 开放实体的逐项 quote 保证。
- **已发现但不顺手修复**：无显式查询的 industry/region 角色偏差；增量路径不筛 Scope 且无 match 时默认 direct。前者可小范围纳入本任务，后者可能牵涉既有变化扫描契约，应由主会话与前一任务已知问题核对并明确归属。
- 不改变前一任务已 final-review、远程测试的未提交 Jev 源码；后续最小补丁必须在现有改动上编辑，禁止 reset/revert/覆盖工作树。未启动任何持久进程，无需清理远程资源；主会话正在运行的进程状态本批未查询。
