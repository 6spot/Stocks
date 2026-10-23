# V2 集成验收记录（2026-10-23）

## 范围与结论

沿用现有 V2 阶段任务，按 Owner「可处理的继续验收，真机及外部条件最后统一处理」的授权执行。
本轮没有改动产品代码，也没有启动新的实施任务。所有运行验证均在测试服务器
`100.120.14.84` 执行，本机只读取、编辑和传输文件。

可执行的相关测试、真实保存报告对照、SEC JSON 采集与字段映射、Fed XML 解析已取得证据。
真实远程 MCP、真实转载/冲突及跨报告修订成对样例、返回货币费用的端点与长期运行仍未完成；
V2 的端到端退出条件保持未勾选，父任务保持 `planning`。

## 环境与相关测试

- 当前源码基于 `6428d73`，传至 `/tmp/stocks-v2-acceptance-20261023`。
- 复用既有 PostgreSQL 容器 `stocks-test-v0-03-postgres` 与持久 Python 环境，另建数据库
  `stocks_v2_acceptance`；真实报告所在的 `stocks_both` 不用于会清表的测试。
- 测试通过显式 `PYTHONPATH=/tmp/stocks-v2-acceptance-20261023/backend/src` 读取本轮源码，
  未修改公共虚拟环境的 editable 指针。数据库连接和对象存储凭据仅从远程受限环境文件载入。
- 首次测试因新库未建表出现 **101 passed、32 setup errors**（缺少 `reports` 等表）；
  修正验收脚本，先执行 `alembic upgrade head`，再执行 `alembic check`，结果为
  `No new upgrade operations detected`。这是验收环境初始化问题，未修改业务代码。

在隔离目录的 `backend` 下执行持久环境 Python 的以下命令，最终退出码均为 0：

```bash
python -m alembic upgrade head
python -m alembic check
python -m pytest -q --tb=short \
  tests/test_tools.py tests/test_research_supplement.py \
  tests/test_parsing_formats.py tests/test_evidence_conflicts.py \
  tests/test_report_comparison.py tests/test_coverage_usage_api.py \
  tests/test_providers.py tests/test_research.py tests/test_collection.py
python -m pytest -q --tb=short tests/test_facts.py
```

- 九文件批次：**133 passed，1 warning，4.60s**。
- Fact / Source Mapper 文件：**40 passed，1 warning，3.05s**。
- 共 **173 passed**；两个批次均报告同一 Starlette/httpx 弃用警告。
- 这些测试覆盖授权拒绝与预算、先保存后引用、解析定位与超限、同源合并和数值/口径/期间冲突、
  已存报告快照对照、未知覆盖与未知费用、Provider 返回费用及 Fact 映射。
  测试中的 MCP、冲突、版本变化与货币费用使用受控响应，不等于真实第三方场景已验收。
- 未在本轮重跑全量后端、前端类型检查或生产构建；本轮没有产品或前端变更。
- 文档收尾检查同样在服务器隔离目录执行：9 个变更文档、33 个相对链接、2 个任务 JSON
  均通过；记录与远程结果一致，V1/V2 阶段退出条件保持未勾选，差异空白检查通过。
  结果保存在 `doc-review-summary.json`。

## 两份真实模型报告

两次真实成功研究来自同一 Task `saved_evidence_brief`，经既有 magpie 网关：

| 一侧 | Report | 创建时间（UTC） |
| --- | --- | --- |
| 较早 A | `f123a0bf-0003-4f99-a264-8527200148b2` | 2026-10-23 13:31:23.657434 |
| 较晚 B | `9e9d97b2-07f3-4751-ad4a-2cce67aee741` | 2026-10-23 13:31:54.933025 |

认证读取两个报告与
`GET /api/reports/{id}/comparison?other_report_id={other_id}` 的正反方向均成功，
排序按 `created_at`，请求交换时 `swapped` 从 false 变为 true，较早/较晚两侧保持一致。

| 差异 | 新增 | 消失 | 持续 |
| --- | ---: | ---: | ---: |
| Finding | 3 | 3 | 0 |
| Open Question | 3 | 3 | 0 |
| Document | 0 | 0 | 1 |
| Fact | 0 | 0 | 不适用 |

Fact 指标变化 0，晚到 Document 0，warnings 为空。不能把差异对象的三个键误计为三个持续项。
自比返回 400，未知报告返回 404；失败 Run 没有报告时也是 404。临时把已有报告对应 Run
标为 failed 后比较返回 409，检查后已恢复为 succeeded。

逐条阅读两份实际报告，均围绕同一份 SEC–AMF MOU 公告，三条 Finding 分别描述协议范围、
SEC 声明将处理注册申请、两位官员的观点；报告区分事实、官方观点和未证实的效果。
两份表述改写，因此按现有精确 statement 契约计为新增/消失，不能解读为现实发生了六项变化。
两份均说明宏观/市场情绪资料不足。A 无已知索引缺口，B 明确显示 `unindexed_chunks`。
此处完成的是 Agent 对真实输出及接口的核对，不代表 Owner 已做投资内容签认。

在读取两份报告与双向对照前后，对以下表的全部记录转成 JSON 文本、按该文本排序并计算 SHA-256，
行数和完整内容哈希均相同：

| 表 | 行数 |
| --- | ---: |
| reports | 2 |
| analyses | 2 |
| task_runs | 6 |
| task_step_results | 9 |
| model_calls | 2 |
| documents | 22 |
| facts | 3 |
| document_chunks | 4 |
| evidence_packages | 3 |

这证明本次请求没有改写这些已存快照或追加 ModelCall。未捕获网络流量，不能据此单独证明
完全没有外部网络请求；只读实现与受控测试另提供证据。完整行哈希保存在远程
`real-report-check.json`，不把数据库内容提交到仓库。

两份报告的调用记录分别为 prompt/completion **20701/865**、**20204/834**，
耗时 **11430ms**、**9930ms**，Provider 为 `chat`、请求模型为 `group/openai`。
费用均为 null，各自 `cost_known_calls=0`、`cost_unknown_calls=1`，未伪填为零。
真实样本没有持续 Finding、Fact 修订、Document 版本更替、晚到资料或失败/回退计费调用；
相应行为的本轮证据限于上述受控测试。

## SEC EDGAR submissions：真实采集与字段映射

仅在隔离副本的 `config/v2-acceptance-sources.yaml` 中启用
`sec_edgar_submissions`，请求公开的 `https://data.sec.gov/submissions/CIK0000320193.json`
（Apple）。使用已授权的联系邮箱；仓库示例仍为 `enabled: false`。
对象存储使用既有 SeaweedFS 的独立 bucket `stocks-v2-acceptance`。

- 初次采集：discovered 1、ingested 1、failed 0。
- Document `572a1c03-cd72-44b3-8596-50cef908b736`，version 1，保存原始 JSON（163,917 字节）。
- 原件 SHA-256：`47e4383b0133a4146cc631725f9aa28bac6d5679d504f735a78f1a1923c4b622`。
- 字段映射完成，四条 `origin_method=source_mapping` 的 Fact：

| metric_id | 实际取值 | 原文 JSON Pointer |
| --- | --- | --- |
| registrant_name | Apple Inc. | `/name` |
| latest_accession_number | 0001140361-26-040540 | `/filings/recent/accessionNumber/0` |
| latest_filing_date | 2026-10-22 | `/filings/recent/filingDate/0` |
| latest_filing_form | 4 | `/filings/recent/form/0` |

四条引用均保存 document_version=1 和与 Document 一致的 content_hash；另从 S3 读回完整原件，
重算 SHA-256 一致，并逐一解析 JSON Pointer，确认四条 Fact 的值与原文对应值一致。
重复采集 ingested 0、reused 1、failed 0，仍为同一份 version 1。
只验证了配置的四个文本字段，不代表财务数值指标或所有 CIK 均已覆盖。

**全文解析未通过**：真实 Apple JSON 超过解析器 5,000 值上限，parse 阶段记录
`STRUCTURED_CONTENT_TOO_LARGE`，completed 0、failed 1；没有把截断内容冒充完整正文。
字段映射仍可独立完成。该边界已有超限测试；本轮未放宽上限、修改 parser 或声称已完成
该 JSON 的 Chunk/Embedding/报告引用链路。

验收汇总脚本曾两次使用错误的统计列名/表名；核对当前模型后修正为 `statement` 和
`document_parse_batches`，重新只读汇总成功，未因此重跑清表测试或改动原始证据。

## Fed XML 与本轮 PDF 探测

通过应用 `SafeFetcher` 获取 `https://www.federalreserve.gov/feeds/press_all.xml`，
真实 XML 14,813 字节，SHA-256 为
`529e889a916423a3979c1bebdd94c2b8d402889700d8c85cc4d125b7f538d327`。
当前 parser 生成 125 个 segment、12 个 Chunk，定位类型均为 `xml_path`；
首段定位为 `/rss` 的 `version` 属性。原始响应保存为远程 `fed-feed.raw`。
此项为真实字节获取与 parser 调用，不是 XML Mapper 或完整研究链路验收。

本轮另请求 Fed 的 `monetary20240918a1.pdf`，在 25 秒预算内超时，未拿到 PDF 字节，
不记为本轮 PDF 通过，不重复无依据请求。此前 V2-02 已记录的真实 8 页 PDF 采集与页内定位
证据仍保留，两轮证据不能互相冒充。

## 构造数据与保留资源

对真实报告库先创建权限为 0600 的备份 `/tmp/stocks-both-before-v2-cleanup.dump`，再以
subject/symbol/metric/processor/fingerprint/fact-key 联合条件删除本轮四条手工 Acme 冲突 Fact。
这些 Fact 曾使用不存在的 Chunk 引用，只用于函数/渲染辅助检查，不算真实有效证据；
完整冲突行为以隔离库的相关测试为准。

远程证据目录保留 `verification.log`、`verification-initial.log`、`facts-tests.log`、
`real-report-check.json`、`real-sec-summary.json`、`real-sec-pointers-check.json`、
`real-formats-summary.json` 及来源原件，
供后续继续验收与排查 SEC 全文解析上限；不提交凭据、数据库转储或数据库行内容。
本轮不新建容器，验收库和独立 bucket 暂保留。真实报告 API、已有 PostgreSQL/SeaweedFS、
magpie 反向隧道和其运行目录继续保留，避免破坏后续真机/外部联合验收现场。

## 对旧证据表述的校正

- V0 重处理证明的是同一 Document 版本的旧批次与引用仍可读；当时只比较了引用文本前
  40 个字符和报告正文，不能写成全文 Chunk 零变化，也不是上游字节变化后新版本入库的实测。
- V1 双连接晚提交检查证明 SQL 事务可见性；当时未证明该条晚提交资料经应用变化流进入
  后续 Evidence Package。真实失败/成功 Run 的游标保持 23 / 推进至 24 是另一项观测。
- V1 的三条引用来自两 Chunk/一 Document，只能说明引用分布，不能单独证明 Fact 与 Document
  的取材合并；该契约由相关集成测试覆盖。cron/timezone 只做配置/实现核对。
- 当前 S3 证据来自自建 SeaweedFS，不代表外部 S3 供应商已接通；magpie 网关成功也不能外推为
  官方 OpenAI/DeepSeek 端点或全部模型的兼容性。

统一剩余项见 [pending-acceptance.md](../../10-20-stocks-roadmap/pending-acceptance.md)。
