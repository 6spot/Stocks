# 首批真实来源选型与接入

## 目标与事实

为市场情报及一个目标公司的研究提供实际可获取的资料。现有 Connector 仅有 rss/http_api；默认来源以 SEC/美联储为主，已有基础映射不等于任意 API、登录论坛或动态网页均可配置直连。

## 需求

先形成候选来源清单，标明用途、获取方式、可达性、正文/摘要、鉴权/分页、时间字段和限制。利用已有配置、RSS 和 HTTP API 接入首批实际来源；仅对选定来源确有需要的能力作适配。资料覆盖市场新闻、公司一手材料、必要结构化行情/指标及股票相关讨论，允许一个来源覆盖多种需要。讨论内容保留原帖/回复及转述关系中实际取得的部分，明确意见属性；不承诺通用论坛抓取。

## 验收标准

- [x] 首批具体来源及接入范围形成明确决定；既有真实来源可以复用，付费服务和访问条件未经确认不擅自选择。PRD 选型决定（2026-10-27 与 Owner 确认）落实为 `config/sources.yaml`：启用 SEC、美联储、CNBC Markets（T2）与 FRED CPI（T1，`${FRED_API_KEY}` 环境变量注入）；`tavily_web` 保持 disabled 仅作 Tool provenance 身份并补齐合法 mapper 形状（修复其导致 `load_sources` 整体失败的既有配置缺陷）；Reddit 因测试服务器 DNS 投毒阻塞（见下）；个股来源待目标公司确定。
- [x] 真实 RSS/正文及结构化 API 成功通过标准采集入口保存，记录来源、时间、完整性、原件和必要字段映射。测试服务器隔离目录真实运行：SEC 5 条、美联储 5 条、CNBC 10 条 RSS 全部 `complete` 入库；FRED observations 经 `http_api` 入库 1 版（快照 1541 字节哈希核对一致，`limit=13` 全量保留），`facts` 阶段映射 `cpi_index_sa=336.298 index_1982_84_100`、period 2026-09-01、`origin_method=source_mapping`，evidence_ref 指向 `/observations/0/value`。
- [x] 至少一个选定的真实讨论来源经标准入口保存可用内容；若不可访问/缺权限，记录阻塞而不把本项计为通过，范围缩减须明确决定。**阻塞（范围缩减决定）**：测试服务器本地 DNS 对 reddit 域名返回投毒记录（A `31.13.92.37`/`157.240.7.20` 为第三方地址，AAAA `2001::1` 非 Teredo 可用公网地址），标准入口 SSRF 校验以 `UNSAFE_ADDRESS` 拒绝，本机与容器解析均复现。本机 curl 直连可达（本机网络不同），但不以本机结果代替远程验收。处置：本轮不接入 Reddit，讨论来源待 owner 决定引入可信 DNS-over-HTTPS 解析或代理出口后再验；不绕过校验。
- [x] 必要的鉴权、分页或列表到详情获取按实际供应商验证；大型 JSON 的解析限制如实保留，不用一个小响应宣称任意接口可用。FRED query 参数鉴权经环境变量验证（缺失时 `SOURCE_CREDENTIAL_UNAVAILABLE` 失败于请求前；真实 key 仅经环境变量提供）；无分页需求，单序列快照如实标注 `limit=13`；`max_response_bytes` 等边界保持。
- [x] 资料进入处理流程并可被研究检索，原始内容和来源观点未被改写成已核实事实。RSS 文章按既有 parse→classify→facts→index 积压进入处理；FRED Fact 由 mapper 确定性映射（无模型调用），保留原始单位与期间；`original_url` 不含查询参数，快照正文经核对不含密钥。

## 选型决定（2026-10-27 与 Owner 确认）

只接入免费来源，允许注册免费 API key；密钥一律经环境变量提供，不写入 YAML 或任务记录。首批服务市场情报简报与 SpaceX 公司研究。

首批候选（用途、获取方式、鉴权、时间字段、限制如下；可达性一律待测试服务器验证后才启用）：

| 用途 | 来源 | 接入 | 鉴权 | 时间字段 | 限制 |
| --- | --- | --- | --- | --- | --- |
| 政策一手发布 | SEC、美联储新闻 RSS（已配置） | rss，零改动 | 无 | pubDate/pub | 维持现状 |
| 市场新闻 | CNBC Markets RSS | rss，零改动 | 无 | pubDate | feed 仅保留最近条目；详情抓文章页 |
| 市场新闻补充 | Yahoo Finance RSS（newsindex，必要时按代码） | rss，零改动 | 无 | pubDate | 数据中心 IP 可能被限流；汇总类稿件区分原始出处 |
| 宏观与市场序列 | FRED observations API：CPIAUCSL、UNRATE、FEDFUNDS、DGS2/DGS10/T10Y2Y、SP500、VIXCLS | http_api + mapper，逐序列配置 | 免费 key，query 参数 | observation_date | 日频收盘 T+1 更新，月度指标按发布日；单位逐序列声明 |
| 股票讨论 | Reddit r/stocks、r/investing 的 .rss | rss，零改动 | 无 | updated | 数据中心 IP 429/403 风险高；验证失败则记录阻塞，备选 StockTwits 公开 stream |

目标公司 SpaceX（2026-10-27 Owner 确定；上市状态当日经 SEC EDGAR 权威源核实：company_tickers.json 及 submissions CIK0001181412）：SpaceX（Space Exploration Technologies Corp，CIK 0001181412）为 Nasdaq 上市公司，代码 SPCX。公司一手材料以 SEC EDGAR 为主：备案 Atom feed 经 rss 连接器接入（零改动，详情抓 EDGAR 原文），或把现有 http_api submissions 示例改指向 SPCX CIK；公司新闻用 Yahoo Finance 按 SPCX 的 ticker RSS，并用 Tavily 检索定位原文（检索摘要不充当全文）；官网新闻页为动态渲染，仅作可选验证项。个股日线行情重新适用：首选 Twelve Data 免费 key（query 鉴权，免费档额度充足），Alpha Vantage（25 次/天）与 Yahoo 非官方行情接口作备选并标注条款风险。市场新闻与 FRED 材料中提及 SpaceX 的部分按普通证据进入公司研究，不凭行业标签推断。

适配决定：本任务实现 http_api 的可选自定义请求头（含 Authorization），密钥经环境变量占位注入，配置文件不存明文密钥；本批选定来源不需要 POST 与 CSV，维持不支持并明示为限制。无需通用分页框架，单序列/单 feed 快照即可满足首批需要。

实现修正（2026-10-28）：实际验证发现本批来源的鉴权形态只有 query 参数（FRED），无需自定义请求头；故按 PRD「仅对选定来源确有需要的能力作适配」收窄为 **query 参数环境变量占位注入**（`${ENV_NAME}`，见 `SourceConfig.resolved_query()`），未实现请求头适配。同时修复两处实测暴露的既有缺陷：`tavily_web` Source 缺 mapper 令整个 `config/sources.yaml` 无法加载；Source Mapper 时间解析拒绝 FRED 返回的完整日日期（`YYYY-MM-DD`）。

## 凭据状态与申请

2026-10-27 Owner 已在测试服务器 .env 写入 `FRED_API_KEY` 与 `TWELVE_DATA_API_KEY`；`TAVILY_API_KEY` 沿用既有部署值。实现 Agent 只验证变量存在（不打印值），不再申请。三个 key 均为免费档，当初申请渠道：

| 凭据 | 用途 | 注入方式 |
| --- | --- | --- |
| TAVILY_API_KEY | Tavily 检索（config/tools.yaml 已引用，公司新闻 lead 与原文定位） | 环境变量，经 query_credential 注入，通道已实现 |
| FRED_API_KEY | FRED 宏观与市场序列 API | 环境变量；实现时按 query_credential 先例做 query 注入 |
| TWELVE_DATA_API_KEY | SpaceX（SPCX）及后续个股日线行情 | 环境变量；实现时按 query_credential 先例做 query 注入 |

Reddit、CNBC、Yahoo Finance、SEC、美联储均无需 key。值只存在测试服务器环境变量，不进聊天记录、仓库或任务文件。实现顺序：先完成 key 注入与请求头适配，再在测试服务器实测各来源可达性后逐个启用；缺 key 或变量缺失时相应来源保持禁用并记录阻塞。

## 前置与待确认

选型方向、免费 key 政策与目标公司 SpaceX 已与 Owner 确认；最终接入验收依赖 MP-01 修复，与报告口径任务共同确定必需覆盖。SpaceX 为 Nasdaq 上市公司（SPCX），EDGAR 与行情来源适用，具体接入以测试服务器实测为准。凭据复用既有安全配置，不重复索取仓库/部署中已可用的信息。

## 非目标

不自动购买服务、不建设通用爬虫/实时行情平台、不绕过访问控制；Tavily 搜索摘要不充当抓取全文。

## 执行边界

本轮仅创建需求草案，状态保持 planning；未授权开始实现。启动前完成本任务必要的设计、执行计划与上下文，并复核需求。所有应用测试、静态检查和真实调用仅在测试服务器 `100.120.14.84` 的任务隔离目录执行；本机仅编辑和任务管理。不做数据迁移或旧版兼容，不引入额外服务、多 Agent 平台或证券主数据库。凭据不入库、不入任务记录或日志。

父任务：[真实来源驱动的市场与个股报告闭环](../10-27-real-source-report-loop/prd.md)。
