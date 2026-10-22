# F-04 验收记录

## 当前结论

2026-10-22：报告比较已改为按保存的 Analysis/Findings 实际引用确定集合，Fact 原值取自冻结投影；回归场景与归属文档已更新，人工静态复核完成。**运行验证尚未执行，F-04 未通过验收，AC 全部保持未勾选。** 归档只表示代码已交付，不表示测试通过。

## 修改与静态证据

- `backend/src/stocks/api.py`：
  - `_report_side()` 先用 `_cited_research_refs()` 把 Analysis 顶层与各 Finding 的引用规范化为去重集合，并与该 Run 的冻结输入（初始选择 + 已采用补证）相交；不在冻结输入内的保存引用按 409 拒绝，不静默补齐。
  - `_report_document_refs()` 只投影实际引用的 Document；同一引用重复出现只计一次。
  - `_cited_fact_snapshots()` 只取被引用 Chunk 的冻结 `fact_ids`，值、单位、期间与 `available_at` 来自 `merged_research_fact_refs()` 的冻结投影，不再查询实时 Fact 行，也不再遍历整个 Evidence Package。冻结投影缺失某 ID 时保留身份并显式标记 `unavailable: true`、值为 `null`，不读当前行、不填 0。
  - 删除旧的 `_report_fact_snapshots()`（它遍历全部组并读取当前 Fact 表）。
- `docs/interface.md`、`docs/research.md`：记录比较集合来自实际引用、未引用材料不参与、Fact 值来自冻结投影。
- 报告详情的「完整输入」展示语义（`evidence`、`research_inputs`）未改动；未修改排序、告警、成功 Run 校验或原件入口。
- 未修改数据库结构或初始化建表，无迁移。

以上为静态确认，不是 lint、类型检查或运行测试的通过结果。

## AC 与回归场景

全部用例保持未运行状态。

| 验收项 | 覆盖位置与断言 | 运行状态 |
| --- | --- | --- |
| AC-1：未引用 B 不产生新增/修订 | `test_report_comparison.py::test_comparison_ignores_material_a_report_did_not_cite` 在后续包的 `selected_chunks` 追加未引用 Document，断言 Document/Fact 的 `appeared` 与 `disappeared` 均为 0，仅保留两侧实际引用的 Document | 未验证 |
| AC-2：引用新增/消失、版本变化与值修订正确，覆盖补证 | `test_comparison_counts_an_adopted_supplement_only_when_cited` 断言被引用补证作为 Document 与 Fact 新增出现、值为 2.1，且同一初始 Fact 不双算；既有 `test_comparison_explains_a_metric_revision_as_a_source_change` 保留版本与值修订覆盖 | 未验证 |
| AC-3：集合来自保存引用、不遗漏 Findings、不双算 | `_cited_research_refs()` 合并 Analysis 顶层与所有 Finding 的引用；同一用例让两份报告引用同一初始 Fact 并断言 `disappeared` 为 0 | 未验证 |
| AC-4：来源后续变化不影响旧侧 | `test_comparison_reads_frozen_fact_values_instead_of_current_rows` 修改当前 Fact 值后重新比较，断言响应与修改前完全一致、旧侧仍为 2.4；F-01 的真实重处理场景 `test_reprocessing_keeps_a_report_fact_readable_and_its_comparison_unchanged` 保留 | 未验证 |
| AC-5：失败 Run、错配/越权引用拒绝，缺失值如实标未知 | 既有 `test_comparison_rejects_self_unknown_and_failed_reports` 保留；保存引用不在冻结输入内返回 409；冻结投影缺失的 Fact 以 `unavailable: true` 与 `null` 值呈现 | 未验证 |

## 实际执行与阻塞

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 root@100.120.14.84 'uname -s'
# root@100.120.14.84: Permission denied (publickey,password)  exit 255
```

当前会话仍无可用测试服务器认证。依据仓库 AGENTS.md 与后端测试规范，测试与运行检查只能在指定服务器的任务隔离目录执行，因此本轮未运行 pytest 或任何项目检查，也未回退本机。编辑过程中只做过本地语法解析，**不计为通过证据**。未创建远程目录、容器或后台进程。

## 恢复执行

取得本会话可用 SSH 认证后，在远程任务隔离目录的 `backend/` 中执行：

```bash
.venv/bin/python -m pytest -q tests/test_report_comparison.py
.venv/bin/python -m pytest -q tests/test_report_api.py -k "evidence or snapshot or merged"
```

`test_report_comparison.py` 覆盖本次全部新增用例及 F-01 的联合重处理场景；若出现相关失败再缩小到单个用例排查。记录实际命令与结果后再勾选 AC。
