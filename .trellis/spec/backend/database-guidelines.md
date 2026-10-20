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

## 结构变更

`backend/alembic/env.py` 使用 `Base.metadata`；`backend/alembic/versions/0001_initial.py` 是新数据库的初始化结构。开发阶段修改模型时同步修改这份初始化定义，必要时重建开发数据库。不新增历史版本升级迁移、回填、双写或旧结构兼容代码。

结构变更后，在全新数据库运行 `alembic upgrade head` 与 `alembic check`，核对初始化结果和模型；命令见 `docs/quickstart.md`。这项检查只在结构变更时需要，不能替代相关行为测试。
