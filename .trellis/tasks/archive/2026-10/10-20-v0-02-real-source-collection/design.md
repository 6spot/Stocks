# V0-02 技术方案

## 边界与数据流

- 发现、正文获取、统一入库分清职责；Connector 不做正文分类、Fact 或研究。
- 来源与访问参数通过 `config/sources.yaml` 提供；测试以固定 HTTP 响应验证边界，真实联网用于首个 SEC 来源的验收。
- RSS/Atom 条目只作为发现结果。每个新增条目或带 `updated` 信号的已有条目通过受限 HTTP 请求取得文章页；实际取得的 HTML 原字节作为 Document 快照，Feed 标题和摘要仅作元数据。
- 手动入口读取 Source 配置、建立/同步 Source 记录，按配置的 `max_items` 处理本次 Feed 顺序内的条目，并逐条记录结果。V0 不建设 Task/Run、定时调度、采集游标或后台 Worker。
- 首次运行只取 Feed 当前顺序中的有限条目；后续运行对已有 `source_updated_at` 未变化且已有 Document 的条目跳过下载。RSS/Atom 的 `updated` 或 `pubDate` 只表示值得复查，原始字节哈希仍决定是否新建内容版本。

## 建议代码落点

- `config/sources.yaml`：Source 身份、RSS/Atom endpoint、publisher、trust tier、HTTP 限制及首个 SEC 来源。
- `backend/src/stocks/collection/`：Source 配置模型、RSS/Atom 发现、受限 HTTP 正文获取、统一采集编排与手动 CLI。
- `backend/src/stocks/models.py`、`backend/alembic/versions/0001_initial.py`：Document 保存某一原件的不可变完整性/错误，`document_collection_states` 保存同资料最近一次采集尝试。
- `backend/src/stocks/repository.py`：V0-01 的统一入库入口；保留 S3 先写、数据库后提交的语义。
- `backend/tests/test_collection.py`、`backend/tests/test_repository.py`：采集边界、幂等/版本、部分内容与配置行为。

`collection/` 按输入输出边界拆成配置、Feed 发现、HTTP 获取和采集编排，避免单文件同时承担配置校验、网络策略与持久化。只增加当前 RSS/Atom + HTTP 正文路径需要的代码。

## 输入输出与契约

`docs/data.md`, `docs/execution.md`, `docs/validation.md` 是唯一业务契约来源。实现遵守以下本次决策：

- Source 配置只允许 `rss` connector；`source_id`、Feed URL、publisher、trust tier、超时、最大响应字节、最大重定向次数和 `max_items` 必须显式配置并有界校验。自动请求的 `User-Agent` 必须声明客户端版本与联系邮箱；SEC 会拒绝未声明联系身份的自动化请求。
- Feed 和文章请求仅允许 HTTPS；拒绝 userinfo、非默认端口、私网/回环/链路本地/保留地址及重定向到不允许目标的响应。DNS 解析和每次重定向都执行地址校验。
- 请求设置总超时、`User-Agent`、Accept 和响应字节上限。只接受 RSS/Atom 的 XML 类型和文章页的 HTML/文本类型；压缩后与解压后的实际读取字节都受限。错误保留稳定错误码和脱敏 URL，不记录完整响应或凭据。
- 发现结果统一包含 source、外部 ID/URL、标题、摘要、发布时间、来源更新时间。正文获取优先保存完整 HTML；若文章请求失败，则保存已取得的 Feed 标题与摘要，标记 `partial` 并记录获取错误，绝不为它伪造文章原件。
- Document 版本增加不可变的 `completeness`（`complete | partial`）与 `fetch_error`（JSONB，或 null），描述该次实际取得的原件；`document_collection_states` 保存同一资料最近一次采集尝试的当前完整性和错误。相同原始字节仍复用 Document 版本，只更新采集状态；原始字节变化时才增加版本。重试成功后保存真实正文，形成新版本。
- 统一采集结果汇总发现、入库、复用、失败数量及逐条结果，供 CLI 输出和测试断言。单条失败不中止其余条目；配置错误在请求前失败。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。外部服务凭据/目标环境缺失时保留验证限制，不标记实际接通。后续任务不得把本任务未完成能力当已实现。

主要风险是 SSRF、无界响应、Feed 恶意 XML、重定向绕过及把摘要误当全文。对应处理是 URL/地址校验、请求与重定向上限、受限解析、明确完整性状态和真实来源验收。真实 S3 若未配置，代码级测试仍可使用注入的存储函数；真实 SEC 验收需同时提供可用 S3 endpoint。
