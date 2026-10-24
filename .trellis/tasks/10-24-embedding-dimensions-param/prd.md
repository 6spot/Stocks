# Embedding 请求按配置携带 dimensions 参数

## 问题

`ModelClient.embed()` 的请求体只有 `{"model", "input"}`。`config/models.yaml` 里的
`dimensions: 1024` 目前**只用于校验响应**，没有发给服务端。

火山方舟 `doubao-embedding-text-240715` 默认返回 2048 维，支持 1024/2048（及 512）降维，
但降维必须通过请求体 `dimensions` 参数指定。不发送该参数时服务端按默认 2048 维返回，
与系统固定的 1024 维向量空间不匹配，索引会以 `EMBEDDING_DIMENSION_MISMATCH` 失败。

## 要求

- embedding 请求体始终携带配置的 `dimensions`。

  > 实施中修正：原计划「未配置时不携带」不可达。`load_embedding_targets()` 与
  > `ModelClient.embed()` 都在发出请求前要求维度非空，因为该值同时用于校验返回向量
  > 长度；因此不存在「无维度仍能发请求」的配置。该分支为死代码，已改为无条件携带。

- 不改变向量空间维度、建表定义、配置校验规则与现有错误语义。
- 不新增依赖、不新增配置字段（`dimensions` 字段已存在）。

## 验收

- 请求体始终含 `dimensions`，且不夹带其他字段（完整 body 断言）。
- 维度缺失时在发出请求前失败，不发送任何请求，也不会序列化出 `null`。
- 现有 embedding 响应校验（模型名、向量数、索引、维度、数值有限性）行为不变。
- 现有 `test_providers.py` 全通过；在测试服务器隔离环境执行。
- `docs/data.md` 与 `docs/quickstart.md` 中「维度固定为 1024」的表述补充说明：
  维度通过请求参数向服务端声明，不再是纯本地校验。
