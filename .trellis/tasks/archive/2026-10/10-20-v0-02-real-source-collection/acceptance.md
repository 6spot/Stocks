# V0-02 脱敏验收记录

验收日期：2026-10-20

## 环境与范围

- 使用提交前的最新仓库工作区，在远程隔离 Docker 环境执行。
- Python 3.12；全新 PostgreSQL 17 + pgvector 数据库；独立 S3 兼容测试桶。
- 远程目录、容器、数据库用户与对象存储测试凭据均为临时验证资源，未写入仓库。
- 真实联网只访问配置中的 SEC 官方来源；其余网络边界由固定响应测试覆盖。
- SEC 要求自动客户端声明联系身份。配置 `user_agent` 使用
  `StocksIntelligence/0.1 (owner@example.com)`；未带联系邮箱的请求实测返回
  HTTP 403，带声明后取得正文。提交前应由 Owner 将示例邮箱替换为实际联系邮箱。

## AC-1：真实文章正文、来源与时间

结果：通过。

- 真实 SEC Feed 发现当前顺序内 20 条条目；20 条文章请求均取得 `text/html` 正文。
- 20 条均保存为 `completeness = complete`、`version = 1`，没有使用 Feed 响应冒充文章原件。
- 首条记录标题为 `SEC Announces Roundtable on Machine-Understandable Data in the Age of Super Intelligence`，原文地址为 SEC 官方 `newsroom/press-releases/2026-106-...` 页面。
- 首条记录 `published_at = 2026-10-19T17:19:43+00:00`，`collected_at =
  2026-10-20T04:02:39.210577+00:00`，来源记录 publisher 为
  `U.S. Securities and Exchange Commission`，trust tier 为 `T1`。
- 数据库记录与对象存储对象核对：正文 63,766 字节，数据库
  `content_size = 63766`；对象实际读取长度相同。正文 SHA-256 为
  `4298bc0193e973e96353291b06847bcac7d48e5cc7cffd80e580e9dbeb6d7685`，数据库与
  对象存储重算结果相同。

## AC-2：重复与部分内容

结果：通过。

- 在同一数据库和对象存储上再次运行相同 SEC 采集，结果为 20 条 `skipped`、0 条新增、0 条复用、0 条失败；没有重复创建 Document 版本。
- 固定响应测试验证：相同部分内容字节重复取得时复用不可变版本并更新独立采集状态中的最近获取错误；后续正文成功时因原始字节不同形成下一版本。
- 固定响应测试验证：文章请求失败时保存 Feed 实际提供的标题与摘要，标记 `partial`，同时保存脱敏错误；错误中的 URL 查询参数不会进入持久化结果。

## AC-3：配置范围与安全边界

结果：通过。

- 20 条 SEC 文章与当前研究关注股票无对应关系，仍全部入库，证明没有 Watchlist 或投资相关性过滤。
- 固定响应测试拒绝 HTTP、URL userinfo、非默认端口、私网/回环/链路本地地址及解析结果中混入的非公网地址。
- 重定向目标重新校验；响应类型、声明大小、压缩前/后实际大小、超时与重定向次数均受配置上限约束。
- XML 使用安全解析器；Feed 中不安全的文章 URL 作为单项失败保留，不中断其余条目。
- 配置未知字段、越界限制、重复/缺失/禁用来源在发起请求前失败；`User-Agent`
  必须同时声明客户端/版本和联系邮箱。错误输出不含凭据或响应正文。

## 实际执行

```text
远程 Python 3.12 + 全新隔离 PostgreSQL/pgvector（不可变版本与采集状态拆分后复验）:
TEST_DATABASE_URL=<temporary-test-db> \
  .venv312/bin/python -m pytest -q tests/test_collection.py tests/test_repository.py
39 passed

全新空库:
.venv312/bin/alembic upgrade head
exit 0

.venv312/bin/alembic check
No new upgrade operations detected.

真实 SEC 首次采集:
python -m stocks.collection --config ../config/sources.yaml --source sec_press_releases
exit 0
discovered=20, ingested=20, reused=0, skipped=0, failed=0

真实 SEC 重复采集:
同命令再次执行
exit 0
discovered=20, ingested=0, reused=0, skipped=20, failed=0

对象核对:
read_snapshot(<temporary-object-key>)
63766 bytes
SHA-256 4298bc0193e973e96353291b06847bcac7d48e5cc7cffd80e580e9dbeb6d7685
```

## 未验证与限制

- 本次真实来源仅覆盖 SEC 官方 RSS 与静态 HTML 页面；没有验证 JavaScript 渲染、PDF、API 或登录内容。
- 未验证定时调度、采集游标、Task/Run、后台解析、分类、Fact、Embedding/检索及证据阅读 API，这些不属于 V0-02。
- 真实 S3 验收使用临时兼容服务；生产存储的容量、持久化策略、访问控制和跨主机网络未在本任务验证。
- 示例联系邮箱只用于开发配置和验收，生产运行前必须替换为 Owner 实际可联系的邮箱。
