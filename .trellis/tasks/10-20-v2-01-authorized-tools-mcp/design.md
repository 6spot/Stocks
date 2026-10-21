# V2-01 技术方案

## 边界与数据流

仍然只跑现有 `research_agent` Step，不新增 Agent 编排、无独立调度服务：

1. `select_evidence` 照常产生初始 Evidence Package（不变）。
2. Research Agent 先读初始包 + 已追加输入（Repository First）。
3. 只有该 Agent 配置了授权 Tool 时，才发一次**结构化补证计划调用**；计划为空则不发起任何外部调用。
4. 计划中的每次调用先过授权与参数 Schema，再由远程 MCP Streamable HTTP 客户端执行。
5. 取得完整原文/响应后，走统一 Source→Repository 入库（`ingest_document()`），只对该 Document 跑 `parse → classify → facts`，然后**追加** `research_inputs`；摘要或无原文的结果只记录缺口。
6. 最终研究调用一次性拿到初始证据 + 成功追加的输入与缺口，引用校验对两者一致。

## 代码落点

- `backend/src/stocks/tools/`（新增）：Tool 目录配置、授权、错误与远程 MCP Streamable HTTP 客户端。
- `backend/src/stocks/research/supplement.py`（新增）：补证计划 Schema、执行、入库、追加与缺口记录。
- `backend/src/stocks/research/service.py`：初始包快照哈希与初始+追加的合并只读视图。
- `backend/src/stocks/execution/service.py`：Run/Agent 集成、快照校验、重试拷贝。
- `config/tools.yaml`（新增）、`config/tasks.yaml`（Agent 授权与输入 Schema）。

## 授权与快照

- `AgentConfig.tools` 是**有界、去重**的可信 Tool id 列表；Tool 定义只在 `config/tools.yaml`，默认空/停用。模型只能提出结构化请求，不能新增 id。
- Tool 目录条目声明 `endpoint`、`remote_name`、`bearer_token_env`、`source_id`、输入/输出 Schema、`result_mapping`、超时与预算。凭据只从环境变量读取，不进入配置、日志或快照。
- Run 创建时把授权与配置身份写入 `TaskRun.model_snapshot["tools"]`（工具 id、远端名、endpoint hash、Schema hash、预算、超时），不写凭据；执行时重新读取线上配置并逐项比对，不一致则本 Step 失败。

## 结果与证据契约

Tool 结果经 `result_mapping` 归一化为固定字段：`title`、`url`、`content`（完整原文时才可入库）、`content_type`、`publisher`、`published_at`、`source_updated_at`、`summary`。要求：

- 只有取得完整 `content` 且 URL 为公开 HTTPS 才登记 Document；`dedupe_key` 取规范化 URL，重复取回同一内容按现有幂等规则复用版本。
- 无 `content` 的搜索/摘要结果只记录 `tool_content_unavailable` 缺口，不生成 Document/Chunk，不可被引用。
- 每份新 Document 只按位置取有界数量的当前 Chunk，并把该 Document 的 Fact 通过 `fact_refs` 关联，保持“Document 与其 Fact 不重复计数”。
- `published_at`/`collected_at` 保存真实来源时间与采集时间；运行中取得或晚于 `as_of` 的资料照常可引用，但标注 `published_after_as_of` / `collected_after_as_of`。

## 持久化与不可改写

- 追加台账落在 `TaskRun.research_inputs`（追加型 JSONB 列表，`docs/execution.md` 归属），Analysis 保存同一列表的冻结副本；`EvidencePackage` 不新增字段，仍是只读的选择快照。
- 首次追加写入 `selection_snapshot` 边界条目（`evidence_package_id` + 初始快照 SHA-256）；之后每次追加前重算并比对，不一致即拒绝追加（`EVIDENCE_PACKAGE_REWRITTEN`），保证初始 `selected_chunks`/`evidence_groups`/`coverage` 不被改写。
- 合并只读视图 = 初始 `selected_chunks`（标 `origin=initial`）+ 追加输入内的 Chunk 引用（标 `origin=research_input`）。Agent 输入、引用白名单、报告详情、单条证据接口与缺口渲染共用同一视图。
- 每个成功追加在 Agent 最终调用前提交，因此重试沿用来源 Run 的初始包与已追加输入；`_copy_research_inputs()` 深拷贝台账并把边界条目指向重试自己的新包。

## 有界与失败语义

- 计划调用只在有授权 Tool 时发生；轮次上限、同一 Tool 的 `max_calls`、请求/响应字节、JSON 深度、SSE 事件、总超时全部有界。
- 同一 `(tool_id, 规范化参数)` 不重复调用；一轮既无成功追加也无新缺口即无进展终止。
- 超时/传输/RPC/映射失败分别记录 `tool_timeout`、`tool_transport_failed`、`tool_call_failed`、`tool_result_invalid`，不新增瞬时输入，也不放弃已有证据。
- 处理失败（无法 claim parse/classify/facts 或执行失败）按阶段记录 `supplement_parse_unavailable` / `supplement_parse_failed`、`supplement_classify_*`、`supplement_facts_*`：原件已保存但本次不作为可引用输入。

## 无证据语义

`config/tasks.yaml` 的 Agent `input_schema.evidence` 允许空数组：初始与追加输入都为空时，报告仍然成功，但只能表达缺口与不确定性；Finding 仍必须引用证据，因此无证据时模型只能给出 `open_questions` 与覆盖说明。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。外部服务凭据/目标环境缺失时保留验证限制，不标记实际接通。后续任务不得把本任务未完成能力当已实现。
