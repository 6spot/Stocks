# 实施计划：日常闭环可用性补齐

所有验证在测试服务器 `100.120.14.84` 的独立任务目录执行。本机只编辑。

## 执行顺序

R3 → R1 → R2 → R4。先修诊断能力，再让数据流动起来，最后做端到端验证——否则 R4 失败时无法定位原因。

---

## 步骤 1：R3-a 日志配置

- [ ] 1.1 `backend/src/stocks/settings.py` 增加 `log_level: str = "INFO"`
- [ ] 1.2 `backend/src/stocks/worker.py` 的 `main()` 开头调用 `logging.basicConfig(level=settings().log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")`
- [ ] 1.3 `backend/src/stocks/processing/__main__.py` 的 `main()` 同样配置
- [ ] 1.4 确认只在进程入口配置，未在库模块加 `basicConfig`

**验证**：远程启动 `python -m stocks.worker --once`，确认 stderr 有日志行。

---

## 步骤 2：R3-b 记录原始异常

- [ ] 2.1 `backend/src/stocks/processing/runner.py` 新增 `logger = logging.getLogger(__name__)`（当前无 logger）
- [ ] 2.2 该文件的 `except Exception` 分支加 `logger.exception("Document stage processor failed: document=%s stage=%s", claim.document_id, claim.stage)`
- [ ] 2.3 持久化 error 保持 `PROCESSOR_FAILED` 不变（不写异常文本进 DB）

**验证**：远程跑 `tests/test_processing_retry.py`；构造一个抛异常的处理阶段，确认日志含 traceback 且 DB error 仍为 `PROCESSOR_FAILED`。

---

## 步骤 3：R3-c 放宽时间解析

- [ ] 3.1 `backend/src/stocks/extraction/service.py` 新增 `logger = logging.getLogger(__name__)`（当前无 logger）
- [ ] 3.2 `_optional_time()`（约 381 行）：`except ValueError` 改为 `logger.debug(...)` + `return None`
- [ ] 3.3 保持 `parsed.utcoffset() is None → return None` 分支不变
- [ ] 3.4 确认 `facts.py:113 validate_fact` 的时区检查与 `period_start > period_end` 检查**未改动**
- [ ] 3.5 在 `_optional_time` 的 docstring 说明「不可解析与无时区同等处理：时间未知，Fact 保留」

**验证**：远程跑 `tests/test_facts.py`，并补一个用例覆盖 `"2024-Q3"` 这类不可解析输入 → Fact 成功、`period_start is None`。

---

## 步骤 4：R3-d 恢复已失败阶段

- [ ] 4.1 远程查看 3 个 `PROCESSOR_FAILED` 的新日志，确认根因
- [ ] 4.2 对 2 个 `EXTRACTION_INVALID` 执行 `python -m stocks.processing retry --document-id <id> --stage facts`
- [ ] 4.3 等 Worker 处理，确认状态转为 `completed`
- [ ] 4.4 根据 4.1 的根因决定 3 个 `PROCESSOR_FAILED` 是否在本任务内修复；若属外部瞬时故障则记录后 retry

**验证**：`document_processing_states` 中 `facts` 的 failed 计数下降。

---

## 步骤 5：R1 collection Task

- [ ] 5.1 `config/tasks.yaml` 新增 `collect_enabled_sources`（`type: collection`，`sources: [sec_press_releases, fed_press_releases]`）
- [ ] 5.2 确认 `load_task_configuration` 能通过（`type: collection` 不允许 `scope`/`objective`/`strategy`）
- [ ] 5.3 确认没有改动 `scheduled_collection`（保持 `enabled: false`）

**验证**：远程 `python -c "from stocks.execution.config import load_task_configuration; load_task_configuration('config/tasks.yaml')"`；跑 `tests/test_execution.py`。

---

## 步骤 6：R2 研究 Task

- [ ] 6.1 `config/tasks.yaml` 新增 `fed_policy_brief`（`type: research`，`scope.regions: [US]`，`topics: [macro, policy, market_sentiment]`）
- [ ] 6.2 保留 `empty_evidence_skip` / `empty_evidence_report` 作为回归样例，不改其内容
- [ ] 6.3 确认 `scheduled_market_brief` 保持 `enabled: false`

**验证**：同上配置加载检查。

---

## 步骤 7：R4 端到端验证（测试服务器）

- [ ] 7.1 触发 `collect_enabled_sources`，确认 Run 排队并被 Worker 执行
- [ ] 7.2 确认 `fed_press_releases` 出现在 `sources` 表，新 Document 入库并生成 4 个阶段待办
- [ ] 7.3 等待 parse/classify/facts/index 完成（观察 `document_processing_states`）
- [ ] 7.4 触发 `fed_policy_brief`，确认 Run 成功
- [ ] 7.5 读取 `/api/reports`，确认新报告存在且可读
- [ ] 7.6 记录脱敏证据到 `acceptance.md`：run_id、来源计数、阶段分布、报告标题与引用数

**AC-2 专项**：临时把 collection Task 的 `sources` 改成一个不存在的 id，触发后确认 Run 变 `failed` 且 error 为 `COLLECTION_CONFIG_UNAVAILABLE`，不是 500；验证后改回。

---

## 步骤 8：文档同步

- [ ] 8.1 `docs/quickstart.md`：补充「用任务页触发采集」的说明（与现有 CLI 并存）
- [ ] 8.2 `docs/data.md`：若 R3-c 改变 Fact 时间字段语义，更新对应段落
- [ ] 8.3 `docs/execution.md`：若 collection Task 用法有新增约定，更新对应段落
- [ ] 8.4 只改与本次契约变化相关的段落，不做无关重排

---

## 步骤 9：提交

- [ ] 9.1 `git status` 确认没有意外改动另一 Agent 的文件
- [ ] 9.2 逐文件 `git add`，禁止 `git add -A` / `git commit -a`
- [ ] 9.3 提交信息聚焦本任务四项要求

---

## 验证命令（远程格式）

```bash
ssh root@100.120.14.84
cd /tmp/stocks-v0-XX-daily-loop-usability-<run>/backend
ln -s /opt/stocks-test-runtime/tool-venvs/stock-tools .venv
.venv/bin/python -m pytest -q tests/test_processing_retry.py tests/test_facts.py tests/test_execution.py
```

只在出现相关失败或影响面扩大时才追加其他测试文件。

---

## 回滚点

| 步骤 | 回滚方式 |
| --- | --- |
| 1–3（代码） | `git checkout` 对应文件（**仅限本任务文件**） |
| 5–6（YAML） | 删除新增 Task 段落 |
| 7（数据） | 采集产生的新 Document 保留（真实证据不删除） |

---

## 停止条件（交回主会话）

- 必须修改 `backend/src/stocks/api.py` 或 `frontend/**`
- 3 个 `PROCESSOR_FAILED` 根因需要超出 R3 范围的架构改动
- Fed feed 采集持续失败且根因在外部（网络/站点变更）
- 远程测试环境不可用
