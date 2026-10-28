# MP-02 修复执行计划

> 执行说明（2026-10-28 补记）：以下步骤在 2026-10-27 已执行完成，证据见 `prd.md` 验收项、提交 `be6764c` 与远程定向回归；归档时未同步步骤勾选状态。

## 前置

- [x] MP-01 已归档（配置一致性修复完成）。
- [x] 已读审查记录 MP-02 节、`research/service.py`、`research/evidence.py`、`facts.py`。
- [x] design.md 完成。

## 步骤

1. [ ] 修改 `backend/src/stocks/research/service.py`：语义查询循环内为新增入选 Document 批量加载 Facts，经 `_unique_facts_by_signature` + `mark_conflicting_facts` 投影后随 `add_hit` 传入。
2. [ ] `backend/tests/test_incremental_evidence.py` 增加回归：语义命中携带 Facts / 双路径不重复 / 无 Fact 正常入选 / token 淘汰不带 Fact。
3. [ ] 本机静态检查语法（ruff/mypy 如可用仅本机编辑校验，不作为验证结论）。
4. [ ] 远程测试服务器 100.120.14.84 隔离目录：同步代码 → 建隔离 venv/库 → 运行步骤 2 的测试与探针 `test_historical_semantic_document_keeps_saved_facts`。
5. [ ] 定向回归：`test_incremental_evidence.py`、`test_evidence_conflicts.py`、`test_research.py` 中受影响用例。
6. [ ] 更新 `docs/research.md` V1-06 一句说明。
7. [ ] 更新任务 prd.md 验收项勾选状态、写验证记录；commit；archive。

## 验证命令（远程）

```bash
cd /tmp/stocks-<run>-mp02-facts/backend
ln -s /opt/stocks-test-runtime/tool-venvs/stock-tools .venv  # 若可用
TEST_DATABASE_URL=... .venv/bin/python -m pytest -q tests/test_incremental_evidence.py -k historical
TEST_DATABASE_URL=... .venv/bin/python -m pytest -q tests/test_incremental_evidence.py tests/test_evidence_conflicts.py
```

探针复核：把 `audit_probes.py::test_historical_semantic_document_keeps_saved_facts` 复制进远程隔离目录的 tests 下运行，预期转通过。

## 回滚点

单文件功能修复 + 测试，git revert 单 commit 即可回滚。

## 风险

- `facts_for_documents` 默认 limit=200：单个历史 Document 的 Fact 数超过限制时截断——与增量路径同参数，行为一致，不做额外处理；测试用例保持小规模。
- 语义查询每轮一次批量查询，查询数最多 MAX_QUERIES=8，无 N+1 放大。
