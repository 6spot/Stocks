# 修复 Fact 阶段两类失败：吞异常 + 期间表达式容错

## 背景（真实数据）

生产库 5 份 SEC 文档 facts 阶段失败，实测分类：

| 文档 | 错误 | 重跑结果 |
| --- | --- | --- |
| 80cda9e4 | PROCESSOR_FAILED | **成功**（8 条 Fact）→ 偶发 |
| c85768ab | PROCESSOR_FAILED | **成功**（22 条 Fact）→ 偶发 |
| 11cbe32a | PROCESSOR_FAILED | 仍失败：`fact 7 invalid period_start` |
| ff85d7d7 | EXTRACTION_INVALID | `fact 2 invalid period_start` |
| b6c9a5bb | EXTRACTION_INVALID | `fact 1 invalid period_start` |

## A. PROCESSOR_FAILED 吞掉真实异常（缺陷修复）

`backend/src/stocks/processing/runner.py` 的兜底 `except Exception` 只记录
`{"code": "PROCESSOR_FAILED", "message": "Document stage processor did not complete"}`，
原始异常类型、安全错误码与堆栈全部丢失，导致无法判断失败性质（偶发 vs 确定性）。

修复：
- 保留异常类型名到安全错误：`{"code": "PROCESSOR_FAILED", "message": "...", "detail": {"exception": "<ClassName>"}}`。
- 不记录异常原文/堆栈到数据库（可能含外部内容），但 `logger.exception` 写进程日志一次。
- 已有 `ProcessingError` / `ExtractionError` 分支不受影响。
- 补测试：processor 抛任意异常时 stage error 含 exception 类名。

## B. 期间表达式容错（业务语义，Owner 已确认）

现状：`_optional_time()` 对无法 `fromisoformat` 的值抛 `EXTRACTION_INVALID`，
导致整条 Fact（含 statement/数值/单位）丢弃，进而整份文档失败。

目标规则（Owner 确认）：
- ISO 完整时间（带时区）→ 原样保留。
- 纯年份 `2026` / `FY2026` / `2026年` → 全年区间 `[2026-01-01, 2026-12-31]`。
- 季度 `2026 Q3` / `Q3 2026` / `2026Q3` → `[2026-07-01, 2026-09-30]`。
- 半年 `H1 2026` / `2026 H1` / `1H 2026` → 上半年 `[2026-01-01, 2026-06-30]`；
  `H2`/`2H` → `[2026-07-01, 2026-12-31]`。
- 纯日期 `2026-10-24`（无时区）→ 保持既有语义：视为未知（置空），不推断时刻。
- 无法识别的表达式 → 该字段置空，原始文本写入 Fact `attributes.period_raw`（不改 schema）。
- `period_start` / `period_end` / `available_at` 共用同一解析器；`available_at` 只接受
  精确时间与完整日期+时区，期间表达式不适用于它（保持置空）。
- 规范化后的区间按 UTC 边界，与 Source Mapper 的 `YYYY-MM`/`YYYY` 期间锚定语义一致。

## 契约

- `docs/data.md`：记录期间表达式规范化规则与 `attributes.period_raw` 的用途。
- 不变：Fact 必填字段、引用校验、数值/单位规则、`EXTRACTION_INVALID` 对结构性错误的语义
  （非字符串、明显畸形仍硬错误）。

## 验收

- A：runner 单测覆盖未知异常 → stage error 含 exception 类名。
- B：`tests/test_facts.py` 覆盖 年份/季度/H1/H2/无法识别/无时区日期 六类输入。
- 真实回归：重试 5 份文档，期望至少 4 份成功（3 份 period 类 + 2 份偶发），
  记录实际结果；若仍有失败，如实记录原因。
- 远程 pytest（facts + processing）+ 部署 + 容器内特征核验。
