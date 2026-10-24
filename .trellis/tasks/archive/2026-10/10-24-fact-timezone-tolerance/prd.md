# Fact 抽取对无时区 available_at 容错并强化提示

## 问题

真实运行中发现 3 份文档的 facts 阶段以 `EXTRACTION_INVALID` 失败：

```text
Extracted fact 0 available_at must include a timezone
```

模型（`deepseek-v4.1-flash`）返回了形如 `2026-10-24` 的无时区日期。当前
`_optional_time()` 对无时区值直接抛错，导致**整条 Fact 连同 statement、数值、单位
一并丢弃**，且该 Document 的 facts 阶段失败。

`available_at` 是「该信息成为公开可知的时间」，属附加时间语义；它的缺失或格式不完整
不应使一条内容本身有效的 Fact 完全不可用。系统既有原则是「不能把资料缺失写成事实不存在」，
这里同理：时间不可确定时应记为未知，而不是让整条 Fact 消失。

## 要求

- `available_at`、`period_start`、`period_end` 等可选时间字段在**无法确定时区**时置为
  `None`（未知），不因此拒绝整条 Fact。
- 仍是硬错误的输入保持不变：非字符串类型、无法解析的日期字符串仍按
  `EXTRACTION_INVALID` 拒绝，避免把明显的模型错误静默吞掉。
- 抽取提示中明确要求时间必须带时区（UTC 偏移或 `Z`），减少这类输出。
- 不改变 Fact 的必填字段、引用校验、数值/单位规则与现有错误码。

## 验收

- 无时区时间 → 该字段为 `None`，Fact 其余字段照常入库（回归用例）。
- 非字符串时间、无法解析的时间 → 仍抛 `EXTRACTION_INVALID`（保留现有用例）。
- 有时区时间 → 原样保留（保留现有用例）。
- 提示文本包含明确的时区要求。
- 远程执行 `tests/test_facts.py`，仅跑受影响用例即可。

## 背景证据

生产部署 `100.120.14.84:/opt/stocks` 首次真实运行：20 份 SEC 文档，parse 20/20、
index 13 完成（32 向量），facts 阶段 3 份因该错误失败。
