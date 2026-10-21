# 数据库开发规范

## 模型与查询

使用 SQLAlchemy 2 的 `DeclarativeBase`、`Mapped[...]` 和 `mapped_column(...)`；参照 `backend/src/stocks/models.py`。Python 可空类型与列的 `nullable=True` 一致，时间列使用带时区的类型。表、列和索引采用 `snake_case`；显式约束使用 `uq_<表>_<含义>`、索引使用 `ix_<表>_<含义>` 这类可读名称。

`backend/src/stocks/db.py` 提供缓存的 `session_factory()`。调用方用上下文管理会话，并为列表查询设置排序和上限。`backend/src/stocks/api.py` 的查询写法可作参考：

```python
with session_factory()() as session:
    rows = session.scalars(
        select(Document).order_by(Document.collected_at.desc()).limit(limit)
    ).all()
```

写操作要明确事务提交位置，失败时让事务回滚；不要在多个函数里隐式提交同一操作。批量或复杂查询先依据实际访问模式设计，不为假定的性能问题提前加索引。

当前 `stocks.repository.ingest_document()` 是第一个写事务入口：调用方传入现有的
`Session`，Repository 在数据库提交处负责 `commit` / `rollback`。版本身份由
`(source_id, dedupe_key, version)` 与 `(source_id, dedupe_key, content_hash)` 唯一约束
共同保护，调用方不得绕过入口直接拼接 Document 版本。跨 S3 与 PostgreSQL 的写入
不是分布式事务：S3 原件必须先写入，数据库失败后保留不可达对象但不产生可用
Document；读取端仍以数据库引用的 `snapshot_key` 为准。

Document 版本保存当次实际取得的不可变证据元数据；相同原始字节必须复用内容版本，不得原位改写标题、URL、时间、完整性或获取错误。只有原始字节哈希变化才新增 Document 版本。最新采集尝试的完整性和错误保存在可更新的 `document_collection_states`，以 `(source_id, dedupe_key)` 为主键并指向最近 Document；该状态不替代任一历史版本自己的完整性及错误。Document 与采集状态分别约束 `completeness` 为 `complete | partial`，部分内容必须带获取错误、完整内容不得带获取错误，Repository 也执行同一校验。

## 结构变更

`backend/alembic/env.py` 使用 `Base.metadata`；`backend/alembic/versions/0001_initial.py` 是新数据库的初始化结构。开发阶段修改模型时同步修改这份初始化定义，必要时重建开发数据库。不新增历史版本升级迁移、回填、双写或旧结构兼容代码。

结构变更后，必须在**测试服务器**的任务隔离环境中对全新数据库运行
`alembic upgrade head` 与 `alembic check`，核对初始化结果和模型；禁止使用本机
数据库或把本机结果当作验证。命令与环境清理见
[测试环境](testing-guidelines.md)。这项检查只在结构变更时需要，不能替代相关行为测试。

`alembic check` 只比较命名约束与索引，不比较列级 `unique=True` 生成的匿名唯一索引。
因此模型侧写在 `__table_args__` 里的具名 `UniqueConstraint` 必须在
`0001_initial.py` 用同名 `UniqueConstraint` 表达，不能改成列级 `unique=True`，
否则全新库结构与当前模型出现漂移。唯一性属于表级契约时使用具名约束；新增或调整
约束后立即在新库跑 `upgrade head` 与 `check`，两者都通过才算结构一致。

## 派生事实与幂等键

派生事实（如 `facts`）用一张通用表 + `attributes` JSONB 表达不同种类，不为
每种指标建专表。每行必须引用它来自的不可变来源身份（Document 版本 + Chunk 或
JSON 字段路径），并且必须有一条**由来源位置推导**的确定性幂等键，在该版本内唯一：

```python
# Correct：身份来自证据位置，同一位置重复处理只会命中同一行
payload = {"origin": fact.origin_method, "type": fact.fact_type,
           "evidence": fact.evidence_ref, "metric": fact.metric_id, ...}
fact_key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
```

- 修订值必须落在**新的 Document 版本**（新 `document_id`），不覆盖旧行；这是旧报告引用
  仍然有效的前提。
- 语义上不可比较的值要有表级约束兜底，例如 `value IS NULL OR unit IS NOT NULL`：未知
  单位宁可不存数值，也不能存成看起来可比较的数字。
- 只有显式重处理（`reprocess_requested`）才在同一版本内先删除再重写；普通重试依赖唯一
  约束复用，不删除已有结果。

## pgvector 查询

向量召回从 `document_chunk_embeddings` 出发，按 `chunk_id`、`document_id` 和当前
解析批次逐级连接，并在同一查询中过滤当前模型、维度和来源/时间条件。HNSW 索引可能
包含保留的旧模型向量，过滤条件也会排除候选；执行带过滤的 `ORDER BY distance
LIMIT` 查询时，先 `LOAD 'vector'` 注册扩展参数，再在事务内设置
`hnsw.iterative_scan = strict_order`，避免首批图候选被过滤后少于请求数量。测试
应使用真实 pgvector 覆盖当前批次、模型/维度隔离和过滤后的召回，不能用纯 Mock
代替。
