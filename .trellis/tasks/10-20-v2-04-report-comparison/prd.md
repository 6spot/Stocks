# V2-04 基于原始证据的历史报告比较

## 目标与用户价值

Owner 可比较前后报告及其真实依据，理解结论、修订和不确定性变化。

## 背景与依据

阶段：V2。归属契约：`docs/research.md`, `docs/interface.md`, `docs/validation.md`。代码现状见[基线](../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V2-03](../10-20-v2-03-source-conflicts/prd.md)

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- 按 Task/范围和时间读取历史成功报告，允许 previous_report_ref 与已有 Analysis 作为背景。
- 比较回到原始 Document/Fact/版本变化；历史模型结论不能替代新事实依据。
- 统一报告界面支持前后对照，保留当时 coverage/as_of/生成时间与实际输入，不因当前数据更新改写旧报告。
- 市场、行业和个股复用比较流程，不建立 Finding 主数据或长期关系图。

## 验收条件

- [x] AC-1: `GET /api/reports/{report_id}/comparison?other_report_id=...` 与 Web 报告页「历史报告对照」卡片可选择两份成功报告，逐项看到结论新增/消失/持续（含不确定性与引用变化）、Document 新增/消失/同一来源新版本、同指标取值修订、覆盖缺口增减与晚到资料；`documents.persisting[].earlier/later` 分别给出两侧可打开的 Document 版本（测试断言两次实际原件入口）。
- [x] AC-2: 同指标 Fact 按 `(subject_name, symbol, metric_id)` 比较，标出 `value_changed`/`basis_changed`/`period_changed` 并给出两侧真实取值（示例 2.4 → 2.1，不出现中间值）；晚于较早报告 `as_of` 发布或采集的 Document 列在 `late_material`，说明为真实来源变化；`unexamined_count` 只从 Evidence Package 读取，未知保持 `null` 不补 0，两侧 coverage 与缺口增减分开显示，Task/Scope 不同或 `as_of` 未推进时给出 `warnings[]`。
- [x] AC-3: 失败或未完成 Run 的报告返回 409，不能充当成功历史报告；同一报告自比返回 400、未知报告返回 404；比较只读两份报告各自保存的 Analysis/Evidence Package/引用版本，不重新研究、不改写旧报告，测试断言较早报告在新版本入库后仍引用当时版本。

## 不在范围内

逐条 Finding 关系图、严格历史知识回放、虚构价格/收益对比。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。本任务已在 Owner「按任务列表逐个实施」的授权下完成：无数据库结构变更，契约写入 `docs/research.md`、`docs/interface.md`、`docs/validation.md`，运行说明写入 `docs/quickstart.md`；验证只在测试服务器 `100.120.14.84` 隔离目录执行。真实连续 Run 报告的人工对照复核留给最终验收，不阻塞后续任务。
