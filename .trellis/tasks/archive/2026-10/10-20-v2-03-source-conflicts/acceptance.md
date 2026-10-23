# V2-03 来源独立性与冲突验收记录

- 验收日期：2026-10-22
- 提交：`8a8bcc3`（`feat(research): group declared originals and list conflicting values`）
- 环境：测试服务器 `100.120.14.84` 隔离目录 `/tmp/stocks-v2-03-source-conflicts`，
  独立 PostgreSQL 端口 `55440` 与任务自有对象存储 bucket `18337`。

## 结论

AC-1 至 AC-3 通过。真实「多来源转载同一原文」成对样例与真实同指标冲突值未取得，登记为
待验收项而非已通过。

## AC-1：已确认转载合成一组且所有原件可打开；相似主题不误合并

结果：通过。

- 采集时把来源自己声明的原件记在 Document 内容版本上：HTML canonical 链接、文章绝对
  地址、feed 绝对 `http(s)` id。取材阶段只用这个声明合并。
- 声明相同 → 同一个 `OriginGroup`，`document_count` 增加，但每份 Document 仍保留自己的
  Chunk、Fact、引用与原件入口。
- 标题、摘要、向量相似与 feed `<source>` 都不参与合并。
- 集成测试覆盖「确认转载合并为一个原件组、两份文档都可引用」与「标题相同但地址不同 →
  两个独立原件组」。
- 真实来源核对：Fed 与 SEC 公开 RSS 各 20 条真实条目分别得到 20 个互不相同的结果原件键，
  无误合并；SEC 真实新闻页声明 canonical → `canonical_url`，Fed 页面无 canonical →
  `article_url`。

## AC-2：同指标冲突值及不同口径可同时追溯，不自动标为已核实

结果：通过。

`metric_comparisons[]` 对同一 `(subject_name, symbol, metric_id)` 的多条 Fact 分别标记
`value_conflict` / `basis_mismatch` / `period_mismatch`，逐个列出取值、单位、币种、观测期、
可用时间、陈述与 Document。`render_report()` 确定性生成 `## Sources and Conflicts` 段落，
把各来源值并列写出并声明不平均、不覆盖、不标为已核实，不依赖模型复述。

测试断言 2.4 与 3.1 同时存在、包内不出现平均值，不同观测期只标记期间差异。

## AC-3：同源增多不被描述成更多独立确认，未知如实呈现

结果：通过。

覆盖统计区分 `selected_count` / `evidence_group_count` / `origin_group_count`，
`independent_source_count` 固定为 1。没有来源声明的资料记为 `single_document` 并保留
`document:<id>` 键，报告明确写「独立原件数未知，不等于资料条数」。研究提示词要求按独立
原件计数并把反证与未解决关系如实写出。

## 实际执行的测试

测试服务器隔离目录：

- `test_evidence_conflicts`：13 passed
- `test_collection` + `test_incremental_evidence`：56 passed
- `test_report_api` + `test_research` + `test_execution`：78 passed
- 全新空库 `alembic upgrade head` + `alembic check`：通过，无差异
- 前端 `npm run typecheck` 通过；`vitest` 6 文件 12 tests passed

数据库结构变更只改 `0001` 初始化定义（`evidence_packages` 增加 `metric_comparisons` JSONB），
未写增量迁移。

## 未执行与限制

- **真实同源转载成对样例未取得**：公开来源通常不声明 canonical，本轮没有取到成对的真实
  转载样例。
- **真实同指标冲突值未取得**：公开来源很少给出互相矛盾的同一指标取值。
- 这两项需 Owner 用实际来源复核；本轮只核对了原件键不误合并，不等于验证了真实转载合并
  与真实冲突呈现。
- 事件关系库、来源真值评分与独立核查平台明确不在范围。
