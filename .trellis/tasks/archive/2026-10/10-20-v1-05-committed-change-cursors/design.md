# V1-05 技术方案

## 边界与数据流

- 使用 PostgreSQL 内共享计数记录行锁：`record_change()` 在业务写入同一事务内递增计数并插入变化记录，持锁至提交；`current_watermark()` 只读取已提交计数，因此窗口终点之前不存在未提交空洞。
- 本任务统一接入入库、解析完成、索引完成、分类完成、Fact 映射与 Fact 抽取六个变化写入点。失败、取消和回滚事务不留下变化记录或水位空洞。
- 研究 Run 在执行开始时固定 `(change_watermark_start, change_watermark_end]`。每个 Run 保存原窗口、显示时间和扫描快照；未完成窗口优先于新窗口恢复，避免范围跳号。
- 同一 Task + Scope 的 `research_cursors.watermark` 只表示“完整窗口已扫描，且 Analysis/Report 已成功提交”的消费边界。分页下界 `after_watermark` 只写 Run 的 `change_scan_progress`，绝不提前写入成功游标。
- 运行按有界页读取 `ChangeRecord`，每个已完成页单独提交检查点。进程中断后，后续 Run 或 retry 从最后检查点继续；若窗口已扫描但报告失败，则复用扫描结果和原窗口，不从新窗口重新开始。
- V1-05 提供 `repository.search_changes()`、持久扫描进度、游标推进和原子收尾。把增量候选与历史证据选择合并、处理 Fact/Document 去重和覆盖统计属于 V1-06，不在本任务提前实现。

## 建议代码落点

- `backend/src/stocks/models.py`
- `backend/alembic/versions/0001_initial.py`
- `backend/src/stocks/changes.py`
- `backend/src/stocks/repository.py`
- `backend/src/stocks/execution/service.py`
- `backend/src/stocks/execution/__main__.py`
- `backend/tests/test_change_watermarks.py`

标注拟新增或随前置任务确定的路径是建议，不代表代码已存在；后续以实际模块组织为准。

## 输入输出与契约

`docs/execution.md`, `docs/data.md`, `docs/research.md`, `docs/validation.md` 是唯一业务契约来源。具体实现契约如下：

- `ChangeWindow` 表示固定的 `(start,end]` 窗口；`search_changes()` 按 watermark 稳定排序，先应用查询条件再 `LIMIT`，返回本页记录、原始页续点和 `completed`。
- `TaskRun.change_scan_progress` 保存版本化 JSON：扫描状态、`after_watermark`、页数/记录数、窗口展示时间以及证据查询条件快照。它不包含正文副本，也不替代 Evidence Package。
- `TaskRun.change_scan_completed` 只在原窗口记录全部扫描完成后置为 true；它不表示报告已经保存或游标已经推进。
- 新的研究 Run 若发现同一 Task + Scope 有 `end > cursor.watermark` 的未消费 Run，优先复制其固定窗口与扫描进度。retry 显式复制来源 Run 的窗口与进度。
- 最终 Research Agent Step、Analysis、Report、成功游标和 `TaskRun.status='succeeded'` 在同一事务提交。最终 Step 为 skipped、报告缺失、扫描未完成所有权失效或任一写入失败时，游标不推进且整笔成功收尾回滚。
- 游标只允许单调前进；不同 Task、不同 Scope 使用独立记录。范围变更由 fingerprint 区分并从该范围的初始起点开始。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。外部服务凭据/目标环境缺失时保留验证限制，不标记实际接通。后续任务不得把本任务未完成能力当已实现。

共享计数行会把研究可见写入的最终提交串行化，因此变化记录必须在慢速外部 I/O 结束之后写入，锁内只保留数据库收尾。若扫描超长，以页检查点支持恢复；不得为追求吞吐改用普通序列、时间列或未提交计数。
