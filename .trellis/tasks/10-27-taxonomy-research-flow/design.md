# 技术设计

## 边界与选择

采用全局词表 + 独立研究配置。`config/taxonomy.yaml` 定义标签候选；`config/tasks.yaml` 的 objective/scope/select_evidence.queries 定义研究关注什么。分类不读取或聚合全部 Task，不按 Watchlist 决定收录，不把原文没有点名的公司/股票补进实体结果。

现有检索使用正文语义查询，Scope 不是已实现的全标签精确过滤器。本任务明确补齐分类候选语义及可操作流程，不新增 Repository 标签过滤或改增量扫描契约；不把 direct/background 查询角色说成已经证实原文明示公司。股票示例使用显式查询分别请求公司材料和行业背景。

## 标签配置契约

五类候选统一使用 mapping：`id`、`name`、`description` 必填非空；`aliases` 可省略，为非空字符串组成的数组。删除旧的裸字符串和只有 ID 的配置输入，直接更新当前 YAML 及相关 fixture，不加兼容解析或迁移。

- id：保存与输出的唯一规范值，保留现有 ID 规则及枚举约束。
- name：可读名称，可为中文。
- description：标签适用范围及必要的排除条件。
- aliases：同义词、常用表达及相关词，仅供模型理解；不是出现即命中的规则，不增加另一个 keywords 字段。不要求跨标签全局唯一。

例如 semiconductors / 半导体 / 芯片设计、制造、封装、设备及产业需求 / [semiconductor, chips, 芯片, 集成电路]。模型仍须结合上下文，chips 不能机械等于半导体。

严格校验未知字段、空白、类型、重复别名及合理文本边界。name/alias 不能作为输出 ID。现有公司/代码开放抽取及原文 quote 校验不变，不向实体生成角色注入股票候选名单。

## 提示与留痕

在 taxonomy.py 共享候选语义序列化/渲染，包含 id/name/description/aliases。Jev choice 的 key 仍为 ID，value 带完整语义；逐标签 noul instructions 带相同语义。生成式完整分类的可信提示中增加同一词表结构，使两条协议都知道标签含义；原文仍是独立的不可信输入。

taxonomy fingerprint 覆盖 version、全局 instructions 及各标签全部语义字段，使用稳定 JSON；候选顺序影响请求，保留顺序。仅改名称/说明/别名也改变 fingerprint。不自动重跑成功文档，Owner 通过已有显式 reprocess 更新分类。既有 JSONB 分类结构、数据库模型、Provider 协议和概率校验保持当前设计。

## 可运行配置与流程

补齐当前 YAML 的名称和语义，特别是行业/地域。提供一项手动股票研究示例，使用 NVIDIA/NVDA、semiconductors、相关主题和公司/行业分开的显式语义查询；沿用 research_writer 与 evidence_first。示例不承诺已配置的来源必有该公司文章，报告必须如实表达缺失。

操作文档连起：配置来源与模型 → 手动采集 → 逐文档检查 parse/index/classify/facts → 研究 → 阅读总结 → 打开保存的引用与原件。行业资料没有公司/代码时也能作为背景材料，不能变成“文章提及 NVDA”。

## 验证与回滚

只在 `100.120.14.84` 本任务隔离目录执行。优先 classification、classification_jev 和配置加载相关测试，检查实际请求包含候选语义、输出仅接受 ID、指纹对语义变化敏感，保留实体证据与失败边界。

同批流程至少覆盖公司+代码、公司无代码、纯行业背景三种输入，确认标签、实体、选证与报告引用关系。来源/模型受控和真实调用分别记录。前一任务已通过真实 Magpie Jev；本任务改变候选语义后应做一次有界真实样例验证。真实来源与所有模型联合运行依赖实际可用配置，不把固定来源或受控总结冒充真实全部外部验收。

无数据库结构变更，无迁移。必要时恢复本次精确补丁；不 reset 前一任务仍未提交的 Jev 接入和审查产物。
