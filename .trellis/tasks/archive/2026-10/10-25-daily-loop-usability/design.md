# 设计：日常闭环可用性补齐

## 决策记录

| 决策点 | 选择 | 依据 |
| --- | --- | --- |
| R1 采集入口 | 方案 A：`collection` Task + 复用任务页触发 | `_run_collection` 已完整实现；新增端点会形成平行实现；且 `api.py` 被另一 Agent 占用 |
| R2 研究方向 | 美联储政策与宏观 | 一次闭环同时验证 R1（采 Fed 源）与 R2（研究产出） |
| R3 时间解析 | 放宽为「未知」+ 补日志 | 与 `_optional_time` 既有「无时区→None」先例一致；否则 AC-5 无法成立 |

## 文件边界

### 允许改动

```
config/tasks.yaml
config/sources.yaml
backend/src/stocks/worker.py
backend/src/stocks/processing/runner.py
backend/src/stocks/extraction/service.py
backend/src/stocks/settings.py（仅当需要日志级别）
backend/tests/test_processing_retry.py（或新增用例）
backend/tests/test_execution.py（collection Task 用例）
docs/quickstart.md
docs/data.md
docs/execution.md
.trellis/tasks/10-25-daily-loop-usability/
```

### 禁止改动（另一 Agent 在途）

```
backend/src/stocks/api.py
backend/tests/test_console_api.py
docs/interface.md
frontend/**（全部）
```

若实施中确认必须改 `api.py` 或 `frontend/**`，停止并交回主会话。

---

## R1：采集 Task

### 配置形态

在 `config/tasks.yaml` 新增：

```yaml
  - id: collect_enabled_sources
    name: 采集已启用来源
    type: collection
    trigger: manual
    enabled: true
    sources: [sec_press_releases, fed_press_releases]
    steps:
      - id: collect
        collect_sources:
          kind: collect_sources
```

要点：
- `type: collection` 时 `TaskConfig.validate_type` 禁止 `scope` / `objective` / `strategy`，且 `sources` 必填、去重、每项 ≤120 字符。
- `run_scope` 属性自动产出 `{"source_ids": [...]}`，`_run_collection` 读取该字段。
- 采集 Run 不解析 chat/embedding 路由，模型配置坏了也不影响采集。
- `sources` 只列 `enabled: true` 的来源，避免 AC-2 场景。

### 已禁用来源的行为（已核实，无需改代码）

`select_sources`（`collection/config.py:179`）对三类情况已明确抛 `ConfigurationError`：

| 情况 | code |
| --- | --- |
| `source_id` 不在配置中 | `SOURCE_NOT_FOUND` |
| 存在但 `enabled: false` | `SOURCE_DISABLED` |
| 未指定 id 且无任何启用来源 | `SOURCE_NOT_FOUND` |

`_run_collection`（`execution/service.py:1765`）捕获 `CollectionError` 后转成 `_StepFailure("COLLECTION_CONFIG_UNAVAILABLE", ...)`，Run 变 `failed` 并持久化该错误码。**不是 500，也不产生半个 Run**（`_StepFailure` 在 step 执行前抛出，`_persist_step` 记录 failed step）。

结论：AC-2 由现有代码满足，本任务只需在验收时实测确认。

### 不做

- 不加 `POST /api/sources/{id}/collect`
- 不改前端
- 不改 `_run_collection` 逻辑

---

## R2：真实研究 Task

### 配置形态

替换或新增一个研究 Task，`scope` 指向宏观政策：

```yaml
  - id: fed_policy_brief
    name: 美联储政策与宏观简报
    type: research
    trigger: manual
    enabled: true
    objective: >-
      基于已保存的美联储与 SEC 公开资料，总结近期货币政策取向、
      监管动向与市场情绪线索，标明每条结论的证据来源与不确定之处。
    strategy: evidence_first
    scope:
      regions: [US]
      topics: [macro, policy, market_sentiment]
    steps:
      - id: evidence
        select_evidence:
          kind: select_evidence
          queries:
            - Federal Reserve monetary policy decisions and statements
            - Federal Reserve inflation and interest rate outlook
            - US financial regulation and market structure developments
          limit_per_query: 8
          max_tokens: 30000
      - id: research
        research_agent:
          kind: research_agent
          agent: research_writer
          input: evidence
```

### 示例 Task 处置

`empty_evidence_skip` / `empty_evidence_report` 是 V2-01 的回归样例（覆盖「无证据仍成功」与「条件不满足→SKIPPED」）。删除会丢失回归覆盖。**保留，但在 `name` 中明确标注为示例**（已有 `Example` 字样，无需改动）。是否在 UI 上隐藏不在本任务范围（需改前端）。

`scheduled_market_brief` 保持 `enabled: false`：定时调度长期运行不在本任务验收范围。

### 与 R1 的衔接

先触发 `collect_enabled_sources` 采到 Fed 资料 → 等 parse/classify/facts/index 完成 → 再触发 `fed_policy_brief`。R4 验证按此顺序执行。

---

## R3：处理失败可诊断

### R3-a：日志可见

现状：`stocks/` 下无 `logging.basicConfig`/`dictConfig`，Worker 日志为空。

设计：
- 在 `worker.py` 的 `main()` 与 `processing/__main__.py` 的 `main()` 入口处调用一次 `logging.basicConfig(level=..., format=...)`，读 `STOCKS_LOG_LEVEL`（默认 `INFO`）。
- 在 `settings.py` 增加 `log_level: str = "INFO"`（不新增校验复杂度，非法值由 `basicConfig` 忽略并保持默认）。
- 只在**进程入口**配置，不在库模块配置（避免污染测试与嵌入方）。

日志内容遵守 `.trellis/spec/backend/logging-guidelines.md`：
- 记录异常类型与稳定对象标识（`document_id`、`stage`）
- **不记录** Document 正文、请求头、凭据、完整外部响应
- 异常栈只在 `PROCESSOR_FAILED` 这类「意外缺陷」路径记录，因为这类日志用于开发诊断；生产长期运行的可观测性依赖持久化 error 字段

### R3-b：错误码可区分

现状：`runner.py:264` 的 `except Exception` 只写 `PROCESSOR_FAILED`，原始异常丢失。

设计：
- 在该分支 `logger.exception(...)` 记录 `document_id` + `stage` + 异常类型。
- 持久化 error 保持 `{"code": "PROCESSOR_FAILED", "message": "Document stage processor did not complete"}` 不变（不把内部异常文本写入 DB，遵守「日志可诊断、持久化只存稳定字段」）。
- 这样 AC-4 成立：**错误码可区分**（`PROCESSOR_FAILED` / `EXTRACTION_INVALID` / `EXTRACTION_FAILED` / `PARSED_CHUNKS_UNAVAILABLE` 等已存在），**日志记录原始异常栈**。

### R3-c：放宽时间解析

`extraction/service.py:381 _optional_time()`：

现状：
```python
try:
    parsed = datetime.fromisoformat(value.strip())
except ValueError as exc:
    raise ExtractionError("EXTRACTION_INVALID", f"Extracted fact {index} has an invalid {field}") from exc
if parsed.utcoffset() is None:
    return None   # 已放宽
return parsed
```

改为：不可解析时同样 `return None`，并 `logger.debug` 记录被丢弃的原始值（截断、不写 DB）。

理由与边界：
- 与既有「无时区 → None」完全同构：都是「时间未知，但 Fact 的 statement/value/unit 仍有效」。
- 不放宽 `validate_fact`（`facts.py:113`）的时区要求：那是**入库前**的确定性检查，`None` 不触发它。
- 不放宽 `fact period_start must not be after period_end`：顺序矛盾仍应失败。
- 保留 `period_start` / `period_end` / `available_at` 三者一致性：若两者都可解析且倒序，仍抛错。

### R3-d：已失败阶段的恢复

5 个 failed 阶段用既有 CLI：
```bash
python -m stocks.processing retry --document-id <id> --stage facts
```
R3-c 生效后，2 个 `EXTRACTION_INVALID` 应能转为 completed；3 个 `PROCESSOR_FAILED` 需先看新日志确认根因，再决定是否单独处理。

**若 3 个 `PROCESSOR_FAILED` 的根因是代码缺陷**：属于 R3 范围内的「可诊断」延伸，应在预算内修复；若根因是外部依赖（模型超时等瞬时故障），记录后 retry 即可。

---

## 数据模型

**无变更。** 不新增表、不改列、不写迁移。

---

## 风险

| 风险 | 缓解 |
| --- | --- |
| 放宽时间解析导致坏数据入库 | 只影响 3 个可选时间字段，且已限定「不可解析→None」；`validate_fact` 的时区与顺序检查不变 |
| `select_sources` 对禁用来源的行为未知 | **已核实**：抛 `SOURCE_DISABLED` / `SOURCE_NOT_FOUND`，转 `COLLECTION_CONFIG_UNAVAILABLE`，非 500 |
| Fed feed 结构差异导致采集失败 | `fed_press_releases` 已配置 `connector: rss` 且 `enabled: true`；失败会被 Run 记录，不阻塞 SEC 来源 |
| 改动 `worker.py` 影响调度 | 只加日志配置，不动调度/槽位/所有权逻辑 |
| 与另一 Agent 的文件冲突 | 已在 PRD 列为硬约束；提交时逐文件 `git add` |

## 验证策略

全部在测试服务器 `100.120.14.84` 的独立任务目录执行，本机不做任何运行时验证。

1. 单元：`tests/test_processing_retry.py`、`tests/test_facts.py`（时间解析放宽的边界）
2. 配置：`load_task_configuration("config/tasks.yaml")` 能通过校验（含新 collection Task 与 research Task）
3. 集成（R4）：真实触发 collection Run → 观察 Document 入库 → 触发 research Run → 读取报告
