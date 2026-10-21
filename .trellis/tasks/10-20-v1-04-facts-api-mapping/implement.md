# V1-04 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务 V1-02、V1-03 已完成并归档。
- [x] 重新检查现有代码及测试，落实实际路径与 Schema（见 acceptance.md）。
- [x] 用户确认实施范围后运行 task.py start。
- [x] implement.jsonl / check.jsonl 已按实际改动复核。

## 执行顺序

- [x] 扩充模型/初始化及 Fact 来源位置和幂等约束（`facts` 表 + `(document_id, fact_key)`）。
- [x] 实现 HTTP API 采集（`connector: http_api`）与字段 Mapper（`stocks/source_mapper.py`）、按需文本抽取（`stocks/extraction/`）和 Fact 查询（`/api/facts`、`stocks.processing facts`）。
- [x] 接入 Worker（惰性 `facts_factory`），验证修订/单位/失败/陈旧所有权/重复处理边界。

## 最小必要验证

实际执行（测试服务器 `100.120.14.84`，`/tmp/stocks-v1-04-facts-c1`）：

- `pytest -q tests/` → 333 passed
- 全新空库 `alembic upgrade head` + `alembic check` → 结构一致
- `python tests/v1_02_processing_smoke.py` → 36/36 PASS
- 真实 Fact 闭环（真实采集服务路径 + 真实 CLI 子进程 + 真实 PG + S3）→ 23/23 PASS

没有真实第三方 API 或 Generation Model 凭据，不声称任何具体外部服务已接通。详见 [acceptance.md](acceptance.md)。

新增测试名为计划，文件存在后才执行；不得把本文件命令写成已通过。按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制 → [acceptance.md](acceptance.md)。
- [x] 契约变更只更新唯一归属文档（data/execution/validation），运行能力更新 quickstart 与 sources 示例。
- [x] 检查后续依赖：V1-05 变化水位需要 Fact 写入可追溯，V1-06 需要 Fact 参与统一取材，均已可用。
- [ ] 完成实现/质量检查后按 Trellis 工作流交付。

## 上下文读取注意

`docs/data.md` 当前为 34,267 字节，超过默认单文件注入上限 32,768 字节。自动注入可能截断末尾；派发/开工时须完整读取原文件，尤其末尾检索与来源安全契约。不要把已注入前缀当成全文；无需为此修改领域文档或 Trellis 全局配置。
