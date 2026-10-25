# 验收记录

## 结论：A/B 均已实现并部署；真实重跑后 **20/20 文档 facts 全部完成**，Fact 71 → 130

## A. PROCESSOR_FAILED 保留异常类型（缺陷修复）

- `runner.py` 兜底 `except Exception` 现在记录 `detail.exception`（异常类名），
  `logger.exception` 写进程日志一次（含堆栈）；DB 不存异常原文/堆栈。
- 效果立现：剩余 2 份失败的新错误格式直接暴露根因
  `ValueError: duplicate fact identity within one extraction result`（此前不可见），
  并区分出 `MODEL_TIMEOUT`（真·偶发）与确定性失败。

## B. 期间表达式容错（Owner 确认的语义）

- 新增 `_period_range()`：年份/FY/年 → 全年；Q1–Q4 → 季度区间；H1/H2/1H/2H → 半年区间；
  均按 UTC 边界。无法识别 → 字段置空 + `attributes.period_raw` 记原始文本。
- 无时区裸日期仍置空（不推断时刻）；`available_at` 保持严格（期间表达式不适用）。
- 非字符串/空值仍是 `EXTRACTION_INVALID` 硬错误。
- `docs/data.md` 记录规则表与 `period_raw` 用途。
- 真实验证：`b6c9a5bb` 的 `violation_period` Fact 存下
  `period_raw = {"period_start": "August 2016", "period_end": "March 2025"}`，区间置空。

## C. 追加修复：同一响应内重复 Fact 身份（Owner 批准）

实测模型重复输出同一 `(origin, type, evidence, metric, subject, symbol)` 导致
`persist_facts` 抛 `ValueError` 使整份文档失败。语义本就是「同一身份只存一条」，
因此改为**保留第一条、丢弃重复并计数**（`PersistFactsResult.duplicates_dropped`）。

## 验证

| 检查 | 结果 |
| --- | --- |
| 远程 vitest/pytest：facts + processing_retry | **86 passed**（含新增重复身份、retryable/parked 等用例） |
| 回归有效性 | 用旧 `facts.py` 运行，重复身份用例失败（抛 ValueError） |
| 生产部署 | md5 核对 → 构建（PyPI 超时重试一次）→ 四服务 healthy；容器内确认 dedup 代码 |
| **真实重跑** | 两份重复身份失败文档全部 **completed**；**20/20 文档 facts 无 failed**；**Fact 总数 71 → 130** |

## 最终状态

- facts：completed 14 / skipped 6 / failed **0**（skipped 均为分类判定无需抽取）。
- 5 份原失败文档全部修复：80cda9e4（8）、b6c9a5bb（8）、ff85d7d7（12）、
  c85768ab、11cbe32a（重复身份去重后成功）。

## 限制

真实浏览器视觉验收属 PA-3 待办；`duplicates_dropped` 目前不单独展示，仅在
`to_dict()` 与处理摘要中可见。
