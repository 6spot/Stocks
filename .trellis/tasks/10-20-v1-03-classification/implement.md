# V1-03 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务 V1-02 已完成并归档。
- [x] 重新检查现有代码及测试，落实实际路径与 Schema（见 acceptance.md）。
- [x] 用户确认实施范围后运行 task.py start。
- [x] implement.jsonl / check.jsonl 已按实际改动复核。

## 执行顺序

- [x] 定义词表（`config/taxonomy.yaml` + `stocks/taxonomy.py`）、业务 Schema 与 `openai_compatible` Decision adapter（`stocks/decision.py`）。
- [x] 实现预算输入、单次分类、确定性校验和当前结果保存（`stocks/classification/service.py`）。
- [x] 接入 Worker（惰性 `classify_factory`）并验证无标签/失败/陈旧所有权/新批次作废边界。

## 最小必要验证

实际执行（测试服务器 `100.120.14.84`，`/tmp/stocks-v1-03-classification-c1`）：

- `pytest -q tests/test_classification.py` → 30 passed
- `pytest -q tests/test_classification.py tests/test_worker_processing.py tests/test_processing_retry.py tests/test_providers.py tests/test_report_api.py` → 101 passed
- `pytest -q tests/test_execution.py tests/test_api.py tests/test_processing.py tests/test_embeddings.py` → 98 passed
- `pytest -q tests/test_scheduler.py tests/test_collection.py tests/test_repository.py tests/test_research.py tests/test_s3.py tests/test_search_cli.py` → 101 passed
- 全新空库 `alembic upgrade head` + `alembic check` → 结构一致
- `python tests/v1_02_processing_smoke.py` → 36/36 PASS
- 真实 HTTP 分类闭环（本地 OpenAI 兼容 stub + 真实 CLI 子进程）→ 26/26 PASS

协议验证只覆盖 `openai_compatible` Adapter 与本项目词表/存储链路；**没有真实第三方
Decision Provider 凭据**，不声称任何具体厂商服务已接通。详见 [acceptance.md](acceptance.md)。

新增测试名为计划，文件存在后才执行；不得把本文件命令写成已通过。按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制 → [acceptance.md](acceptance.md)。
- [x] 契约变更只更新唯一归属文档（data/execution/validation），运行能力更新 quickstart。
- [x] 检查后续依赖：V1-04 依赖本任务的 `should_extract` 与分类标签，已可用。
- [ ] 完成实现/质量检查后按 Trellis 工作流交付。

## 上下文读取注意

`docs/data.md` 当前为 34,267 字节，超过默认单文件注入上限 32,768 字节。自动注入可能截断末尾；派发/开工时须完整读取原文件，尤其末尾检索与来源安全契约。不要把已注入前缀当成全文；无需为此修改领域文档或 Trellis 全局配置。
