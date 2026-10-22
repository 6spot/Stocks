# 当前实现与设计一致性审查

审查日期：2026-10-22。审查基线：`main`，HEAD `1f69907`，包含本次任务记录修正；未修改业务代码。

## 范围与结论口径

Owner 要求修正此前发现的任务记录问题，并重点回答代码实现是否符合设计。本次沿用路线图保存审查结果，不创建或启动新的实施任务。

对照 `docs/data.md`、`docs/execution.md`、`docs/research.md`、`docs/interface.md`，阅读数据处理、Run/研究、报告 API 与前端调用链。下列五项是静态代码可确认的实现偏差；给出的复现步骤尚未执行，不代表运行验证通过，也不宣称覆盖了全部潜在问题。

真实 Provider、真实 MCP、ARM 部署及此前由 Owner 批准延期的验收仍按 `../pending-acceptance.md` 管理，不作为本次代码缺陷。PA-2 既有修复不重新列为待修问题。

## F-01 / P1：显式重处理同一 Document 的 Fact 会删除历史证据身份

**设计依据：** `docs/data.md` 的“Fact：结构化事实与数值”要求同版本/同位置保持幂等，旧 Fact 不被覆盖，已保存报告实际使用的证据保持可复查。

**代码证据：**

- `backend/src/stocks/extraction/service.py` 的文本抽取路径约 558 行、Source Mapper 路径约 877 行，在 `claim.reprocess_requested` 时调用 `clear_facts()`。
- `backend/src/stocks/facts.py:198` 的 `clear_facts()` 对该 Document 的 Fact 执行物理 DELETE；后续 `persist_facts()` 新建行并生成新 UUID，相同 `fact_key` 也不能保留原 ID。
- `backend/src/stocks/api.py:1265` 附近的 Fact 详情读取现存行；`_report_fact_snapshots()` 约 1815 行也按旧 Fact ID 查询当前表，查不到的行直接消失。

**触发与影响：** 报告已使用 Fact F，Owner 对同一 Document 发起显式 facts 重处理。即使再次抽出完全相同的事实，F 的 ID 也失效，旧 Fact 详情无法读取，历史报告比较中的事实可能减少。原始 Document 和包内部分 Fact 投影仍可能存在，不能据此说全部原文丢失，但稳定的 Fact 证据链已经断开。

**后续验证场景：** 先生成并保存引用 F 的报告，再排队和执行该 Document 的显式 facts 重处理；核对旧 Fact ID、报告证据入口及比较结果是否仍能读取原值。当前 `test_explicit_reprocess_replaces_this_versions_facts` 预期替换，未覆盖历史报告引用保留。

**修复边界：** 保留已引用的 Fact 身份和内容；直接调整当前模型/初始化或处理策略，不引入历史数据库迁移。

## F-02 / P1：重新解析会让旧 Run 保存的证据包无法重试

**设计依据：** 历史解析批次/Chunk 保留，重试复用已保存的证据选择和输入，历史证据可追溯；“当前批次”限制应用于新取材，而不应使已冻结引用失效。参见 `docs/data.md` 的解析批次契约和 `docs/research.md` 的证据包、重试规则。

**代码证据：**

- `backend/src/stocks/execution/service.py:444` 的 `retry_run()` 复制原 Run 的证据包及研究输入。
- `_agent_evidence()`（约 1996 行）将这些保存的引用传给 `validate_selected_versions()`。
- `backend/src/stocks/research/service.py:955` 的验证查询不仅核对 Document、版本、Chunk 与 parse_batch_id，还在约 979 行强制 `DocumentParseState.current_batch_id == parse_batch_id`。

**触发与影响：** Run 使用解析批次 A 并在后续步骤失败；同一 Document 成功重新解析成 B；Owner 重试旧 Run。A 及其 Chunk 即使仍完整存在，也会因不再是 current 被拒绝，最终报 `EVIDENCE_PACKAGE_INVALID`。这是保存引用的校验规则与保留历史输入的契约不一致。

**后续验证场景：** 失败 Run 绑定 A，重新解析成 B，重试 Run；应继续使用 A 的原片段，并继续拒绝伪造 ID、错误 Document 或错误版本引用。

## F-03 / P1：补证得到的结构化 Fact 没有完整进入 Agent 与冲突展示

**设计依据：** `docs/research.md` 要求初始资料与授权补充资料进入同一研究输入视图；`docs/data.md` 要求补充资料产生的 Fact 遵循相同契约；`docs/interface.md` 要求同指标冲突值并列呈现，不能掩盖。

**代码证据：**

- `backend/src/stocks/research/supplement.py` 的补证入库过程保存 Fact 投影到追加台账的 `fact_refs`（约 845 行），因此问题并非“没有抽取/保存 Fact”。
- `backend/src/stocks/research/service.py` 的 `merged_research_refs()` 合并补证 Chunk 和 Fact ID；`validate_selected_versions()` 返回的可读引用没有包含补证台账的完整 `fact_refs`。
- `backend/src/stocks/execution/service.py:1996` 的 `_agent_evidence()` 只从初始 `package.evidence_groups` 构建 `groups_by_document`，约 2038 行仅为这些初始分组附加完整 `fact_refs`。只在补证中新出现的 Document 得不到这些数值、单位和期间字段。
- 同文件约 2693 行的报告渲染继续使用初始 `package.metric_comparisons`，没有将追加 Fact 纳入该确定性比较视图。

**触发与影响：** 初始包已有某指标值 A，授权补证取得另一 Document 并成功抽取同指标值 B。追加台账与数据库有 B，但 Agent 的结构化证据和报告的来源/冲突比较可能仍只有 A。原文 Chunk 可能允许模型读到 B，不能将其等同于结构化比较链路已接通。

**后续验证场景：** 使用成功执行 parse 和 facts 的补证 stage registry，构造同主体/指标/单位/期间的 A、B 两个值，检查 Agent 实际请求、最终研究输入和报告冲突段同时包含两者。现有补证测试中的 parse-only registry 无法证明这个场景通过。

## F-04 / P2：历史比较把入选但未实际引用的材料算作引用变化

**设计依据：** `docs/interface.md` 的“查看历史（V2-04）”要求比较引用 Document 的新增/消失、引用变化与同指标修订，并以两份保存报告的快照为准。

**代码证据：**

- `backend/src/stocks/api.py:1727` 的 `_report_side()` 调用 `_report_research_refs()` 获取初始选择与采用的补证输入全集，随后将其直接交给 `_report_document_refs()` 和 `_report_fact_snapshots()`。
- `analysis.evidence_refs` 虽单独放进返回结构，却未用于过滤上述 Document/Fact 集合。
- `_report_fact_snapshots()` 还遍历所有初始 evidence group 的 Fact，并读取现存 Fact 行，而不是只使用报告实际引用的事实快照。

**触发与影响：** 两份报告都只引用 A，但第二次取材额外选入未被报告引用的 B。对比仍会把 B 计入 Document/Fact 差异，容易把取材范围变化呈现成报告引用变化；F-01 还会使这类 Fact 比较结果受后续重处理影响。

**后续验证场景：** 两份报告的实际引用完全相同，第二份包额外含一个未引用 Document/Fact；引用新增应为空。若产品希望同时比较输入范围，应另外明确展示该口径，不能复用“引用变化”的含义。

## F-05 / P2：处理积压面板默认 stage 非法，首次请求被拒绝

**设计依据：** `docs/interface.md` 要求 Runs 提供积压/失败处理入口，来源、阶段、状态可查看并重试。

**代码证据：**

- `frontend/src/features/runs/processing-panel.tsx:28` 将 `stage` 初始化为 `"failed"`，`status` 则为空字符串。
- 该状态经 `dashboardApi.processing` 作为 stage 查询参数发出。
- `backend/src/stocks/api.py:774` 附近接收 stage，约 782 行校验它属于 `PROCESSING_STAGES`；合法值是 parse/classify/facts/index，failed 属于 status。

**触发与影响：** 首次打开面板即发送非法 stage，初始积压列表不能正常加载；用户更改筛选后才可能恢复。

**后续验证场景：** 从初始状态渲染面板，检查首次请求参数和实际服务端响应。修复时将默认失败条件放入 status，并使用合法或不指定的 stage。

## 已处理的任务记录问题

- 批量更新归档后的 Markdown 链接、`meta.depends_on` 与 JSONL 上下文引用，包括此前发现的非业务 Trellis 归档任务上下文路径；不修改这些任务已交付业务内容。
- V0-03、V1-05、V2-01 的收尾项按既有实现、验收和归档记录补齐；真实 SEC/MCP 等未执行验收保持未通过。
- V1-06 的 notes 改为已实施/归档及现存限制；`planning_review` 补记本次材料复核，并明确不追认历史审查时间。
- V2-01 `implement.md` 中仍称补证台账存于 Evidence Package 的旧决策改为实际最终设计：TaskRun.research_inputs，完成时冻结到 Analysis.research_inputs。
- 不沿用上次报告中有歧义的受影响任务数、依赖条数；本次没有把未运行的自动检查写成通过。

## 修复任务交接（2026-10-22）

Owner 已要求将发现整理为任务并交由其他 Agent 开发。已创建[修复父任务](../../10-22-implementation-design-fixes/prd.md)，负责五项范围和最终集成验收；本轮只规划，未启动实施。

| 发现 | 子任务 | 依赖 |
| --- | --- | --- |
| F-01 | [保留 Fact 历史身份](../../10-22-fix-fact-reprocess-history/prd.md) | 无 |
| F-02 | [历史解析批次重试](../../10-22-fix-retry-historical-parse/prd.md) | 无；与 F-03 共享文件需协调 |
| F-03 | [补证 Fact 输入与冲突](../../10-22-fix-supplement-fact-inputs/prd.md) | 无；建议先于 F-04 集成 |
| F-04 | [实际引用比较](../../10-22-fix-report-cited-comparison/prd.md) | F-01 |
| F-05 | [积压默认筛选](../../10-22-fix-processing-default-filter/prd.md) | 无，可独立交付 |

各任务均有 PRD、设计、实施计划及 spec/research 上下文清单。状态保持 planning；最终验收条件未勾选通过，真实服务延期清单保持原状态。

## 本次验证状态

已完成：静态对照领域设计、阅读实际调用链和相关既有测试、审阅任务材料及修正 diff。既有验收记录中的测试结果属于历史结果，本次未重跑。

未执行：上述五项的运行复现、业务测试、前端构建，以及任务链接/JSONL 的远程自动复核。本次没有修改业务代码，业务问题仍待修复。

尝试 `ssh -o BatchMode=yes -o ConnectTimeout=8 root@100.120.14.84` 得到 `Permission denied (publickey,password)`。它只能证明免交互认证未成功，不能证明服务器或密码 SSH 不可用。本会话没有可用密码，故未建立远程隔离验证环境；依项目规则未回退到本机执行测试或检查。后续验证应使用测试服务器的本次任务隔离目录。
