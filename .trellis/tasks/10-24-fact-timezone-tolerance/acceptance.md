# 验收记录

## 改动

- `backend/src/stocks/extraction/service.py`
  - `_optional_time()`：无时区值返回 `None`（未知），不再抛 `EXTRACTION_INVALID`。
  - 非字符串、无法解析的时间仍是硬错误。
  - 抽取提示新增明确要求：时间必须带 `Z` 或 `+HH:MM` 偏移，裸日期/本地时间视为未知。
- `backend/tests/test_facts.py`：新增 3 个用例（无时区→None、带时区原样保留、畸形仍报错）。
- `docs/data.md`：记录该容错语义。

## 验证

| 检查 | 结果 |
| --- | --- |
| `tests/test_facts.py` | **43 passed, 1 warning in 3.00s** |
| 回归有效性 | 用改前 `service.py` 运行，`test_naive_time_is_recorded_as_unknown_instead_of_failing_the_fact` 失败（抛 `must include a timezone`） |

环境：`100.120.14.84:/tmp/stocks-review-fixes-20261023`，专用库 `stocks_review_fixes`。日志 `tz-tests.log`。

## 背景

生产部署首次真实运行中，3 份 SEC 文档的 facts 阶段因该错误失败；Fact 的 statement/数值/单位本身有效，仅时间缺时区。

## 限制

未用真实模型重新跑那 3 份文档验证（需重新部署后再验证）。
