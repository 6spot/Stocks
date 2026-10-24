# 验收记录

## 结论：已实现并验证；计划中的「可选携带」经审查修正为「始终携带」

## 实际改动

`backend/src/stocks/providers.py` `ModelClient.embed()`：请求体加入 `dimensions`，
取自已校验的目标维度。

**与原计划的偏差（重要）**：PRD 原本要求「配置了才带、没配置就不带」。实施后经审查确认
该分支不可达——`load_embedding_targets()` 与 `embed()` 都在发请求前要求维度非空，因为
同一数值还用于校验返回向量长度。因此不存在「无维度仍能发出请求」的配置，`if ... is not None`
是死代码，且其注释宣称的兼容性并不存在。已改为无条件携带，并同步修正 PRD 与两份文档中
「可用无维度配置规避」的错误表述。

## 验证

| 检查 | 结果 |
| --- | --- |
| `tests/test_providers.py` + `tests/test_embeddings.py` | **26 passed in 1.24s**，退出码 0 |
| 新增 `test_embedding_requests_the_configured_dimensions` | 通过；断言完整请求体 `{model, input, dimensions}` |
| 回归有效性 | 用改前 `providers.py` 运行，该用例失败（`{'model','input'}` vs 期望含 `dimensions`） |
| `test_embedding_rejects_an_unset_dimension_before_any_request` | 通过；确认维度缺失时零请求发出 |

环境：`100.120.14.84:/tmp/stocks-review-fixes-20261023`，专用库 `stocks_review_fixes`，
持久 Python 3.12。日志 `dims-tests.log`。

## 未改变

响应校验（模型名匹配、向量数量、索引/位置、维度、数值有限性）、重试与回退顺序、
全部 `EmbeddingError` 错误码、向量空间 1024 维与建表定义均未改动。

## 限制

- 未用真实火山方舟凭据发起调用；`dimensions` 参数的存在性与取值范围依据官方文档
  （默认 2048，支持 1024/2048）与本次完整请求体断言，不是真实供应商验收。
- 若某端点拒绝 `dimensions` 字段，当前实现会以 `EMBEDDING_REQUEST_FAILED`（HTTP 400，
  不在可重试集合）失败且无配置绕过路径；这是有意取舍，已写入 `docs/quickstart.md`。
