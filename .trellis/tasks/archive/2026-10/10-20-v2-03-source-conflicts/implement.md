# V2-03 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务 [V1-09](../10-20-v1-09-arm-deployment-acceptance/prd.md) 已归档完成，V2-01/V2-02 已交付。
- [x] 重新检查现有代码及测试：V1-06 已做同 Document 合并与 conflict 标记；`Document.extra` 为每版本 JSONB；覆盖统计已有 `selected_count`/`evidence_group_count`；报告由 `render_report()` 确定性生成。
- [x] 用户授权按 V2 任务列表逐个实施；真实转载/冲突样例与第三方来源确认按会话约定延后记录，不阻塞实现。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线。

## 执行顺序

- [x] 补充源头关联的输入、证据组和冲突表示：新增 `stocks.origins`（canonical 链接解析 + URL 归一化 + `document_origin()`/`origin_key()`），采集时写入 `Document.extra.origin`，feed `<source>`/`atom:source` 记为 `declared_sources`（只作转载出处，不作合并键）。
- [x] 证据组与覆盖：`EvidenceGroup.origin`、`group_origins()`（原件组、`independent_source_count`、未知记为 `single_document`）、`metric_comparisons()`（数值/口径/观测期三类标记）、`document_origins()` 读回；`selected_chunks[]`/`evidence_groups[]` 带 `origin_id`（组内含 `origin_group_size`）；覆盖新增 `origin_group_count`/`merged_origin_group_count`/`origin_groups`/`metric_comparisons`。
- [x] 调整取材/研究提示与结果呈现，保留原始引用：`config/tasks.yaml` research_writer 提示词；`render_report()` 的确定性 `## Sources and Conflicts` 段落；报告详情接口新增 `origin_id`/`origin`/`metric_comparisons`；Web 报告页新增来源独立性卡片、同指标对比卡片、「同一原件（N 份）」标记与 Fact 冲突标记。
- [x] 验证转载、独立反证、冲突单位与未知源头（详见下方最小必要验证）。

## 最小必要验证

- [x] 测试服务器 `100.120.14.84` 隔离目录 `/tmp/stocks-v2-03-source-conflicts`（独立 PostgreSQL `stocks-test-v2-03-postgres` 127.0.0.1:55440、自有 seaweedfs `stocks-test-v2-03-s3` 与 bucket）：
      `tests/test_evidence_conflicts.py` 13 passed（新增；覆盖转载合并、标题相似不误合并、未知来源、
      数值冲突不平均、口径/观测期差异、报告段落、来源校验与 `extra.origin` 落库）；
      `tests/test_collection.py tests/test_incremental_evidence.py` 56 passed；
      `tests/test_report_api.py tests/test_research.py tests/test_execution.py` 78 passed（1 个第三方 Starlette 弃用 warning）。
- [x] 全新空库 `alembic upgrade head`（0001）后 `alembic check` 报告 `No new upgrade operations detected`。
- [x] 前端（同一远程目录）：`npm run typecheck` 通过；`npm run test` 6 个文件 12 tests passed（含新增的报告页来源独立性/冲突用例）。
- [x] 真实来源核对（只读取公开页面、不写库）：Fed 与 SEC 公开 RSS 各 20 条真实条目分别得到 20 个互不相同的结果原件键，未误合并；Fed 真实文章页（85,178 字节）无 canonical → `article_url`，SEC 真实新闻页（64,615 字节）声明 canonical 且与文章地址一致 → `canonical_url`。
- 未验收（不阻塞后续任务，留给最终验收）：真实「多来源转载同一原文」成对样例与真实同指标冲突值；事件关系库、来源真值评分与独立核查平台不在范围内。

新增测试名为计划，文件存在后才执行；不得把本文件命令写成已通过。按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制（见 `prd.md` 验收条件与 `docs/validation.md` 的 V2-03 段落）。
- [x] 契约变更只更新唯一归属文档：`docs/research.md`（原件组与同指标比较契约）、`docs/data.md`（来源声明与合并键）、`docs/interface.md`（报告页独立原件/冲突呈现）、`docs/quickstart.md`（取材与报告字段）、`docs/validation.md`（验收边界）。
- [x] 后续依赖检查：V2-04 报告比较、V2-05 覆盖与费用验收都只读既有 Analysis/Report/覆盖统计；`metric_comparisons` 是增量列，旧 Report 引用不被改写。
- [x] 完成实现与质量检查后按 Trellis 工作流交付：代码、文档、任务记录同一次提交，随后归档任务并记录交付提交号。

## 上下文读取注意

`docs/data.md` 当前为 34,267 字节，超过默认单文件注入上限 32,768 字节。自动注入可能截断末尾；派发/开工时须完整读取原文件，尤其末尾检索与来源安全契约。不要把已注入前缀当成全文；无需为此修改领域文档或 Trellis 全局配置。
