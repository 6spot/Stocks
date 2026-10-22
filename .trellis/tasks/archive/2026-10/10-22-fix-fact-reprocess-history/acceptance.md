# F-01 验收记录

## 当前结论

2026-10-22：修复代码、回归场景及归属文档已写入，人工静态复核已完成；**运行验证尚未执行，F-01 未通过验收**。Owner 已指示提交、归档并推送，任务因此以“代码已交付、验证未完成”归档，保持 AC 未勾选，不代表验收通过。分支为 `fix/fact-reprocess-history`。后续必须在指定测试服务器补跑下列最小范围测试；不以历史验收或本轮静态阅读替代远程测试结果。

## 修改与静态证据

- `backend/src/stocks/extraction/service.py`：文本抽取和 Source Mapper 路径均移除显式重处理时的清空；保留原 `assert_stage_owned()`、持久化、事件写入及阶段完成的事务顺序。
- `backend/src/stocks/facts.py`：删除 `clear_facts()` 及其 `delete` 导入，修正过时的替换描述。继续使用现有 `(document_id, fact_key)` 复用逻辑；已有身份返回原行，新身份追加行。
- `docs/data.md` 和 `.trellis/spec/backend/database-guidelines.md`：明确重处理保留原 Fact 身份、内容及来源；未再次产出的事实仍保留，同身份不同候选值不覆写，只有新增身份生成新增事件。
- 人工核对 Fact 详情、报告读取及比较的原有读链路，无本任务的 API 实现改动。后端文本检索未发现剩余 `clear_facts` 或 `replace_all` 引用。
- 没有修改模型、初始化建表或数据库结构，无迁移或旧库转换。

以上为静态确认，不是 lint、编译、类型检查或运行测试的通过结果。

## AC 与回归场景

以下测试名称均指已编写或已存在的覆盖，全部保持未运行状态。

| 验收项 | 覆盖位置与断言 | 运行状态 |
| --- | --- | --- |
| AC-1：同一事实保留身份与历史读取 | `test_explicit_reprocess_preserves_this_versions_facts` 的 `same` 分支比较重处理前后完整 Fact 读形状；历史报告场景核对原 Fact 详情和报告读取 | 未验证 |
| AC-2：两条处理路径与遗漏事实 | 上述文本场景的 `omitted` 分支；`test_mapper_reprocess_preserves_facts_and_only_records_new_identities` 的 `same`、`omitted` 分支 | 未验证 |
| AC-3：候选变化不覆写、新 Document 独立 | 文本 `changed`、Mapper `changed_unit`；已有 `test_revision_on_a_new_version_keeps_the_old_fact` 覆盖新 Document 版本独立性 | 未验证 |
| AC-4：追加、幂等、失败与所有权边界 | Mapper `new_field` 断言只新增一个 Fact 及对应事件、水位；`commit_failure` 在写入后、阶段完成前注入失败，断言原 Fact 不变、新 Fact/事件/水位回滚且未完成重处理；文本 `invalid` 检查失败时保留原事实；保留已有普通重试与失效所有权用例 | 未验证 |
| AC-5：历史报告引用和值稳定 | `test_report_comparison.py::test_reprocessing_keeps_a_report_fact_readable_and_its_comparison_unchanged` 保存报告后显式重处理，并对比原 Fact 详情、既有比较结果、Analysis 引用、EvidencePackage 组和原文下载 | 未验证 |

历史报告场景复用现有报告比较夹具，不扩展 F-04 的实际引用集合或冻结投影修复范围。

## 实际执行与阻塞

已执行认证探测：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 root@100.120.14.84 'uname -s'
```

实际结果：`Permission denied (publickey,password)`，exit 255。该结果证明当前免交互认证未成功，不证明服务器不可达或密码认证不可用。当前用户会话未提供可用密码，未查找其他会话、环境历史或文件中的凭据。

依据仓库 AGENTS.md 和后端测试规范，所有测试及运行检查必须在指定测试服务器的当前任务隔离目录执行。故本轮没有运行 pytest、编译、类型/格式检查、数据库或 API smoke，也未回退本机验证。没有创建远程任务目录、容器或后台进程，无本轮新增远程资源待清理。

## 恢复执行

取得本会话可用 SSH 认证后，按 `.trellis/spec/backend/testing-guidelines.md` 同步当前任务并配置任务隔离数据库与 TEST_DATABASE_URL。在远程任务目录的 `backend/` 中执行：

```bash
.venv/bin/python -m pytest -q tests/test_facts.py
.venv/bin/python -m pytest -q tests/test_report_comparison.py -k test_reprocessing_keeps_a_report_fact_readable_and_its_comparison_unchanged
```

只在实际失败或明确的相关风险需要时扩展验证范围。记录实际命令、结果与限制，修复失败后再勾选 PRD 验收项。归档时未运行的测试仍是未验证项，不得把本任务描述为验收通过；F-02 至 F-05 不在本任务实施范围。
