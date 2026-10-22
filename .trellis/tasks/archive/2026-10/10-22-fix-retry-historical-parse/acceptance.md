# F-02 验收记录

## 当前结论

2026-10-22：`validate_selected_versions()` 已从“必须等于当前批次”改为“必须是该 Document 已成功完成的不可变批次，且 Chunk、Document 版本、批次归属一致”，回归场景与归属文档已更新，人工静态复核完成。**运行验证尚未执行，F-02 未通过验收，AC 全部保持未勾选。** 任务因 Owner 指示沿用 F-01 的提交/归档/推送流程而归档，归档只表示代码已交付，不表示测试通过。

## 修改与静态证据

- `backend/src/stocks/research/service.py`：`validate_selected_versions()` 改按 `DocumentChunk → Document → DocumentParseBatch` 的真实归属校验，要求批次属于该 Document、`status = completed`、内容哈希与 Document 版本一致；不再要求 `DocumentParseState.current_batch_id == parse_batch_id`。`document_version`、Chunk 身份、`parse_batch_id` 一致性校验保持不变。
- 新 Run 的正常取材未改动：`semantic_search`、`current_document_hits` 等 Repository 入口仍只读当前成功批次，因此历史批次不会因此回到普通召回。
- 允许集仍只由该 Run 的 Evidence Package 与 `research_inputs` 台账构成（`_agent_evidence()`），未放宽伪造引用、跨 Document 或越权补证校验。
- 未修改数据库结构、初始化建表或迁移；没有新增任务状态或独立历史证据服务。

以上为静态确认，不是 lint、编译、类型检查或运行测试的通过结果。

## AC 与回归场景

全部用例保持未运行状态。

| 验收项 | 覆盖位置与断言 | 运行状态 |
| --- | --- | --- |
| AC-1：批次 A 后重新解析 B，重试仍读 A | `test_execution.py::test_retry_reads_a_superseded_parse_batch_after_the_document_is_reparsed`：失败 Run 冻结批次 A，插入成功批次 B 并切换 `current_batch_id`，重试包的 `selected_chunks` 仍为 A，执行成功后 Analysis 引用仍为 A 的 Chunk 与批次 | 未验证 |
| AC-2：追加台账中的历史引用同样可读 | `test_retry_keeps_the_ledger_and_appended_evidence_stays_citable` 已扩展：初始选择与追加输入都属于批次 A，重试前插入批次 B，重试仍以追加 Chunk 完成引用 | 未验证 |
| AC-3：非法引用仍拒绝 | `test_research.py` 覆盖错误 `document_version`、未成功完成的批次、Chunk/批次属于另一 Document；允许集仍来自本 Run 包与台账 | 未验证 |
| AC-4：新 Run 仍只选当前批次 | 上述执行用例断言 `current_document_hits()` 在切换后只返回批次 B 文本，重试结束后仍只返回 B；`test_embeddings.py::test_reparse_makes_old_batch_vectors_unsearchable_until_reindexed` 保留旧向量不可检索的既有覆盖 | 未验证 |
| AC-5：旧批次缺失/损坏显式失败 | 批次行缺失时 join 不成立、`status != completed` 时被过滤，均抛 `ValueError` 并由 `_agent_evidence()` 映射为 `EVIDENCE_PACKAGE_INVALID`；不回退当前批次 | 未验证 |

## 实际执行与阻塞

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 root@100.120.14.84 'uname -s'
# root@100.120.14.84: Permission denied (publickey,password)  exit 255
```

当前会话仍无可用测试服务器认证。依据仓库 AGENTS.md 与后端测试规范，测试、编译、类型与格式检查只能在指定服务器的任务隔离目录执行，因此本轮未运行 pytest 或任何项目检查，也未回退本机。编辑过程中仅做过本地语法解析以确认编辑未破坏文件结构，**该解析不属于项目验证，不计为通过证据**。未创建远程目录、容器或后台进程，无新增远程资源待清理。

## 恢复执行

取得本会话可用 SSH 认证后，在远程任务隔离目录的 `backend/` 中执行：

```bash
.venv/bin/python -m pytest -q tests/test_research.py -k validate_selected_versions
.venv/bin/python -m pytest -q tests/test_execution.py -k "superseded_parse_batch or retry_keeps_the_ledger"
```

出现相关失败或需要核对新 Run 召回边界时，再追加 `tests/test_embeddings.py -k reparse`。只在实际失败或明确风险需要时扩大范围；记录实际命令与结果后再勾选 AC。F-03、F-04 的共享文件修改尚未进行，合并后需复核输入允许集与冻结字段投影。
