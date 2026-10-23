# V2-02 来源与解析扩展验收记录

- 验收日期：2026-10-22
- 提交：`bc2220c`（`feat(parsing): locate JSON, XML and PDF originals as citable chunks`）
- 环境：测试服务器 `100.120.14.84` 隔离目录 `/tmp/stocks-v2-02-source-parser-expansion`，
  独立 PostgreSQL 端口 `55439` 与任务自有对象存储 bucket。

## 结论

AC-1 至 AC-3 通过，且新增格式在**真实公开来源**上做过核对。外部限制（联系邮箱、SEC
EDGAR 映射）登记在 `docs/validation.md`「待最终验收」。

## AC-1：新来源有可运行配置与真实样例，链路可追溯

结果：通过。

- 真实 Fed 新闻 RSS 在测试服务器用真实链路采集 20 条条目入库；20/20 的 S3 原件
  SHA-256 与数据库 `content_hash`、字节数一致。
- 真实 HTML 可解析成带定位的片段并可回查原件。
- 真实 Fed PDF（189,823 字节）经文章获取通道入库后解析为 8 页、15 Chunk，页内字符
  一一映射。
- **待 Owner 验收（不阻塞 AC-1 的代码部分）**：`user_agent` 仍是占位联系邮箱
  `owner@example.com`，首次持续采集前需换成真实邮箱；SEC EDGAR submissions 示例的
  CIK 与访问政策未确认，真实响应映射未验收，样例保持 `enabled: false`。

## AC-2：同数据经不同渠道走统一入口；失败保留原件可重试

结果：通过。

PDF 与 HTML 一样经 `SafeFetcher` 文章获取 → `ingest_document()` → 同一 parse 阶段。
真实重跑复用同一处理器版本批次（attempt 1 未新增）；显式重处理生成 attempt 2 并保留
attempt 1 的 Chunk ID、文本与定位。损坏、加密、无文字层与超限分别记为稳定错误码，
原件不变。

## AC-3：新增格式保持数值、单位与定位

结果：通过。

- JSON 用 JSON Pointer、XML 用元素路径、PDF 用页码加页内字符范围定位。
- 数值与单位按原文文本保留；真实 PDF 片段中的数字全部来自原件，无新增数字。
- HTML 表格行与单元格定位行为不变。
- **明确不在范围**：PDF 表格结构重建与 OCR。无文字层的 PDF 记 `PDF_TEXT_UNAVAILABLE`
  失败并保留原件供重试，缺口写在 `docs/data.md` 而非承诺为已支持。

## 实际执行的测试

测试服务器隔离目录：

- `test_parsing_formats`：14 passed
- `test_processing` + `test_processing_retry`：67 passed
- `test_collection`：48 passed
- `worker_processing` / `facts` / `classification` / `api` / `report_api`：98 passed
- 全新空库 `alembic upgrade head` + `alembic check`：通过，无差异
- 真实来源 drill：10/10 pass（含上述真实 RSS、真实 HTML、真实 PDF 三项）

## 未执行与限制

- 真实联系邮箱替换、SEC EDGAR 真实映射、调度长期运行与第三方限流均未验证。
- XML XPath 映射、OCR 与 PDF 表格重建不在本任务范围。
