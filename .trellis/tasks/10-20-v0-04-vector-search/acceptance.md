# V0-04 脱敏验收记录

验收日期：2026-10-20

## 环境与范围

- 使用当前工作区代码、Python 3.12.15、PostgreSQL 17.11 和 pgvector 0.8.7。
- 数据库结构从全新空库执行 `alembic upgrade head`，随后执行 `alembic check`；
  HNSW 索引确认使用 `vector_cosine_ops`。
- Embedding Provider 的请求和响应边界使用 `httpx.MockTransport` 验证，没有调用
  真实外部 Embedding Provider，也没有使用外部模型凭据。
- 数据库测试使用固定 HTML 和确定性向量，不包含真实正文、凭据或数据库密码。

## AC-1：语义与精确检索

结果：通过真实 PostgreSQL/pgvector 行为测试。

- 当前解析批次中的 Chunk 写入真实 pgvector 表后，可通过余弦距离语义命中，
  返回持久 Chunk、Document 版本、来源、时间和相似度。
- 来源条件以及发布时间、采集时间的半开区间条件均加入语义、精确和覆盖查询；
  无匹配的过滤条件返回空结果。
- 股票代码或指标编号形式使用 64 字符以内的词边界匹配，不调用 Embedding
  Provider，并返回同一批次的持久 Chunk。
- 语义查询从向量表开始逐级连接当前批次，并在事务内启用
  `hnsw.iterative_scan = strict_order`，过滤后的候选不会因首批图结果不足而被
  静默截短。小规模验证库可能由查询规划器选择顺序扫描；这不改变索引定义或结果
  正确性，生产规模下的规划器选择和召回率仍是验证限制。

## AC-2：无标签资料、失败缺口与重索引

结果：通过。

- 索引与检索只依赖当前解析批次，不读取分类或股票标签；没有分类字段的固定文档
  仍可语义召回。
- 索引失败不删除 Document、Chunk 或已有向量；状态记录稳定错误、原件仍可读取，
  覆盖结果显示失败文档、未索引 Chunk 和文档缺口。
- `/api/documents` 在当前解析批次尚无本次向量时报告 `pending`，不沿用旧批次的
  `completed` 状态和计数。
- 重处理切换当前批次后，旧批次向量不进入语义查询，覆盖显示新批次尚未索引；
  重新索引后只检索新批次 Chunk。相邻上下文读取只接受当前成功批次。
- 普通重复索引复用完成结果；`--reindex` 从已存 Chunk 重建，不读取 S3 原件或重新
  解析 Document。

## AC-3：向量空间与查询副作用

结果：通过。

- 配置层拒绝与固定 1,536 维不一致的目标，以及模型名或维度不同的回退目标。
- Provider 响应拒绝不同模型、错误数量、重复/越界索引、错误维度和非有限数值。
- 相同模型和维度的回退按配置顺序执行；不可重试的 HTTP 失败不静默切换。
- 语义查询只为本次查询文本生成一个向量，然后读取持久向量和 Chunk；测试确认
  查询没有读取 S3、解析原文或生成文档向量。查询字符上限在 Provider 调用前生效。
- `--chunk-id` 模式拒绝来源/时间过滤和 `--limit`；语义/精确模式拒绝
  `--radius`，避免显示或接收未被该模式应用的参数。
- 精确查询为向量无关路径，`coverage` 保持 `null`；语义查询返回真实当前批次覆盖。

## 实际执行

```text
Python 3.12.15 + PostgreSQL 17.11/pgvector 0.8.7:
TEST_DATABASE_URL=<isolated-test-db> \
  mise x python@3.12.15 -- python -m pytest -q \
  backend/tests/test_api.py backend/tests/test_providers.py \
  backend/tests/test_embeddings.py backend/tests/test_processing.py \
  backend/tests/test_search_cli.py backend/tests/test_repository.py
exit 0
59 passed in 0.69s

全新空库结构检查:
python -m alembic upgrade head
exit 0
python -m alembic check
No new upgrade operations detected.

python -m compileall -q src tests
exit 0

git diff --check
exit 0
```

## 未验证与限制

- 没有调用真实外部 Embedding Provider，因此实际供应商兼容性、凭据和网络连通性
  尚未完成阶段验收；固定 `httpx.MockTransport` 结果不能替代一次真实调用。
- 验证数据规模不足以让 PostgreSQL 规划器必然选择 HNSW；仅确认索引定义、查询路径、
  当前批次/模型/维度/过滤语义和真实 pgvector 执行正确。生产规模性能与召回率未测。
- 本次未新增公开检索 API、`search_changes`、Evidence Selection、Fact 或前端；
  这些不属于 V0-04 验收范围。
