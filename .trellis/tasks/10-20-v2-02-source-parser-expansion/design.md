# V2-02 技术方案

## 边界与数据流

- 这是按缺口执行的适配任务，不把“更多来源”解释为全网采集或厂商插件平台。
- 缺口依据：现有 parse 只支持 `text/html`、`application/xhtml+xml`、`text/plain`，而真实研究需要的 SEC 申报/公司文件是 PDF、监管 API 是 JSON，`http_api` 已能存原件但缺可检索片段与定位。
- Owner 未在开工前指定来源名单。按“真人验收/第三方 API 可延后”的会话约定，本任务先实现与来源无关的格式适配并给出可运行配置样例，真实来源接通与真实样例核对记为待验收。

## 数据流与契约落点

~~~text
RSS 条目链接 → SafeFetcher（新增允许 application/pdf）
                    → ingest_document()（字节、哈希、来源、时间、版本不变）
                    → parse 阶段按 MIME 选择解析器
                        ├─ HTML/XHTML/文本（既有）
                        ├─ application/json（+json） → JSON Pointer 定位片段
                        ├─ application/xml|text/xml（+xml） → 元素路径定位片段
                        └─ application/pdf → 页码 + 页内字符范围定位片段
                    → 后续 classify / facts / index 不变
~~~

解析只做格式与定位，不总结、不改写；数值与单位以原文文本保留，PDF 不承诺表格结构识别，
扫描件无文字层时记为缺口而不是空正文。所有分支继续走同一 `parse_content()` →
`persist_parse_result()` → 阶段状态机，不新增并行存储或阶段。

## 建议代码落点

- `backend/src/stocks/processing/structured.py`（新增）：JSON/XML → 带 pointer/path 定位的 `SourceSegment`。
- `backend/src/stocks/processing/pdf.py`（新增）：PDF 逐页文本与页码定位；依赖 `pypdf`。
- `backend/src/stocks/processing/parser.py`：`SUPPORTED_CONTENT_TYPES` 与 MIME 分发；版本号随解析能力升级。
- `backend/src/stocks/collection/http.py`：文章获取允许 `application/pdf`。
- `config/sources.yaml`：新增公开、无需密钥的来源配置；需要 Owner 确认联系邮箱/口径的 API 源保持 `enabled: false`。

标注拟新增的路径以实际模块组织为准。

## 输入输出与契约

`docs/data.md`, `docs/research.md`, `docs/validation.md` 是唯一业务契约来源。输入引用前置任务的持久记录，输出满足 PRD 各 AC；不把本任务文档变成另一套字段规范。跨层 Schema、事务及失败语义在开工复核中结合已落地代码确定。

- 片段定位沿用现有 `locator` 契约：JSON 用 `kind="json_pointer"` + `pointer`；XML 用 `kind="xml_path"` + `path`；PDF 用 `kind="pdf_page"` + `page`/`page_count`，并像纯文本一样带 `source_ranges`（相对该页提取文本）。
- 失败码沿用 `ParseFailure`：格式非法/损坏、无可用文本、超出上限都产生显式错误，`unsupported` 只保留给未支持的 MIME。
- 有界性：JSON 叶子数、XML 元素数、PDF 页数与单页字符数都有上限；超限失败而不是静默截断，保证「缺口」可被看到。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。外部服务凭据/目标环境缺失时保留验证限制，不标记实际接通。后续任务不得把本任务未完成能力当已实现。

解析器版本升级会改变配置指纹：同一 Document 在新版本下重新解析生成新的不可变批次，
旧批次与已引用片段保留；不需要结构迁移。
