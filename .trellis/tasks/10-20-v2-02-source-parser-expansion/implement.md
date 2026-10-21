# V2-02 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务 [V2-01](../archive/2026-10/10-20-v2-01-authorized-tools-mcp/prd.md) 已归档完成。
- [x] 重新检查现有代码及测试：parse 仅支持 HTML/XHTML/纯文本，`http_api` 已能存原件并映射 JSON Fact，文章获取只允许 HTML/纯文本。
- [x] 用户授权按 V2 任务列表逐个实施；来源名单确认与真实来源验收按会话约定延后记录，不阻塞实现。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线。

## 执行顺序

- [x] 定界：JSON/XML/PDF 解析适配 + PDF 文章获取 + 公开来源配置样例；XML 字段映射（XPath mapper）与真实来源接通明确留给后续/待验收。
- [x] 新增 `stocks.processing.structured`（JSON Pointer / XML 元素路径定位）与 `stocks.processing.pdf`（页码定位），在 `parse_content()` 中按 MIME 分发并升级处理器版本。
- [x] `collection/http.py` 文章获取允许 `application/pdf`，复用同一入库与阶段链路。
- [x] `config/sources.yaml` 增加公开 RSS 来源与需 Owner 确认的 API 来源样例（后者保持 disabled）。
- [x] 单元测试覆盖三种格式的定位保真、数值/单位保留、失败与超限码，以及 PDF 文章链接的入库路径。
- [x] 更新 `docs/data.md`、`docs/quickstart.md`、`docs/validation.md` 的支持范围与待验收边界。

## 最小必要验证

- [x] 单元/集成测试（测试服务器 `100.120.14.84` 隔离目录 `/tmp/stocks-v2-02-source-parser-expansion`，独立 PostgreSQL `stocks-test-v2-02-parser-postgres`）：
      `tests/test_parsing_formats.py` 14 passed；`tests/test_processing.py` + `tests/test_processing_retry.py` 67 passed；
      `tests/test_collection.py` 48 passed；`tests/test_worker_processing.py tests/test_facts.py tests/test_classification.py tests/test_api.py tests/test_report_api.py` 98 passed（1 个第三方 Starlette 弃用 warning）。
      过程中修复：`httpx.MockTransport` 的 `Response(content=...)` 已被消费（`StreamConsumed`），成功正文的固定响应用 `stream=httpx.ByteStream(payload)`；XML 元素路径重复父级，改为由父级把完整路径压栈。
- [x] 本轮不改数据模型；全新空库 `alembic upgrade head`（0001）后 `alembic check` 报告 `No new upgrade operations detected`。
- [x] 真实来源验收（隔离数据库 + 自有 seaweedfs bucket，`v2_02_real_drill.py`，全部 10 项 pass）：
      公开 Fed 新闻 RSS 采集 20 条真实条目入库；S3 原件字节哈希/大小与数据库一致（20/20）；
      真实 HTML 解析出 `semantic-text-v2` 批次（2 Chunk），42/42 片段范围可回到解码原文、21 个不同元素路径，
      数字逐字保留且无新增数字；普通重跑复用批次，显式重处理生成 attempt 2 且 attempt 1 的 Chunk 仍可读；
      真实 Fed 执行行动 PDF（189,823 字节）经 `ARTICLE_CONTENT_TYPES` 文章获取入库并解析为 8 页 / 15 Chunk，
      22 个页内片段按去空白字符逐一映射回该页文本，`coordinate_unit=pdf_extracted_text_chars`。
      未验收：`user_agent` 仍是占位联系邮箱（首次持续采集需 Owner 换真实邮箱）、SEC EDGAR API 真实映射、调度触发的长期运行与第三方限流行为。

新增测试名为计划，文件存在后才执行；不得把本文件命令写成已通过。按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制（见 `prd.md` 验收条件与 `docs/validation.md` 的 V2-02 段落）。
- [x] 契约变更只更新唯一归属文档：`docs/data.md`（来源、格式、批次与处理器版本）、`docs/quickstart.md`（解析与 PDF 获取）、`docs/validation.md`（验收边界）、`docs/architecture.md`（仅技术栈一行）。
- [x] 后续依赖检查：V2-03 来源冲突与 V2-04 报告比较都只读现有 Document/Chunk/Fact/Report，不依赖本任务的解析内部；`semantic-text-v2` 使旧文档下一次解析生成新批次，不迁移旧库。
- [x] 完成实现与质量检查后按 Trellis 工作流交付：代码、文档、任务记录同一次提交，随后归档任务并记录交付提交号。
- 外部限制（不阻塞后续任务，留给最终验收）：Fed/SEC 来源的真实联系邮箱与访问政策确认；SEC EDGAR submissions API 的真实响应映射未运行（示例保持 disabled）；OCR、PDF 表格结构重建、XML XPath 字段映射未实现。

## 上下文读取注意

`docs/data.md` 当前为 34,267 字节，超过默认单文件注入上限 32,768 字节。自动注入可能截断末尾；派发/开工时须完整读取原文件，尤其末尾检索与来源安全契约。不要把已注入前缀当成全文；无需为此修改领域文档或 Trellis 全局配置。
