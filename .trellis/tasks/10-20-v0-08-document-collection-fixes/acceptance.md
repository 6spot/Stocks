# V0-08 脱敏验收记录

验收日期：2026-10-20

## 环境与范围

- 所有验证在测试服务器 `100.120.14.84` 的任务隔离目录
  `/tmp/stocks-v0-08-document-collection-fixes-<run>` 执行；本机只编辑与打包，
  未在本机运行 pytest、Alembic、编译或数据库命令。
- 使用服务器持久 Python 3.12.15、SQLAlchemy 2.1.5、httpx 0.28.1；数据库为
  PostgreSQL 17.11 + pgvector 0.8.7。
- 先检查现有容器：没有创建新容器，沿用 `stocks-v0-03-accept-postgres`，只为本任务
  新建独立数据库与临时超级用户角色（pgvector 测试角色需要 `LOAD 'vector'` 权限）。
  其他任务的容器、数据库与角色未被修改。
- R1 用真实 PostgreSQL 验证；R2/R3/R4 使用固定 HTTP 响应与 Repository 行为验证，
  未重复执行 V0-02 当时的真实 SEC/S3 采集。

## AC-1（R1）：最近内容与版本读取一致

结果：通过。

- `get_latest_document()` 改为读取 `document_collection_states.latest_document_id`，
  不再按最大版本号猜测最近取得内容；采集跳过判断与最近内容读取共用同一指针。
- 真实 PostgreSQL 覆盖 A → B → A：恢复正文 A 时 `created=False` 且指针指向 A 的
  不可变版本，`latest.version == 1`，版本 2 仍完整保留。
- 覆盖“完整 → 部分内容 → 恢复同一完整正文”：指针回到完整版本，
  `completeness = complete`、`fetch_error` 为空，历史版本的标题、时间与原文不变。
- 相同字节重复取得只更新采集状态，不新增版本；原件字节哈希与内容版本复用规则不变。

## AC-2（R2）：部分内容重试不依赖时间字段

结果：通过。

- `_should_recheck()` 先判断 `partial`，再比较来源更新时间/发布时间；
  来源未提供时间信号时部分内容仍会在下一次手动采集重新请求正文。
- 固定响应覆盖“无 pubDate → 文章 503 → 部分内容 → 再次采集成功取得正文 →
  第三次采集跳过”，文章请求次数为 2，验证失败恢复后不会重复请求。
- 未知时间保持 `NULL`，不伪造；完整且无变化信号的条目继续跳过。

## AC-3（R3）：畸形文章链接按单项失败

结果：通过。

- Feed 条目的 `link` 在 `urljoin` 解析阶段抛出的 `ValueError` 收敛为
  `INVALID_ARTICLE_URL`，只记录位置与 `malformed_link` 原因，同批合法条目继续入库。
- 混合畸形/合法条目的固定 Feed 断言：合法条目正常返回，失败只有一条且
  `details == {"position": 1, "reason": "malformed_link"}`，错误 URL 为 Feed 入口。
- 文章重定向的畸形 `Location` 实测由 httpx 自身拒绝，落入既有
  `REQUEST_FAILED` 有界错误；新增断言确认脱敏后不含凭据与主机片段。
  未新增不可达的“重定向 URL 解析”分支。
- 既有 HTTPS、默认端口、userinfo、私网地址与体积上限拒绝行为保持不变。

## AC-4（R4）：保留正文声明编码

结果：通过。

- 内容类型白名单只比较媒体类型，`FetchedResponse.content_type` 保留声明的
  `charset`，写入 Document 与快照的仍是原始字节。
- 固定响应声明 `text/html; charset=windows-1252` 时，采集入库的 `content_type`
  为完整声明值，`parse_content()` 严格解码得到 `encoding == "windows-1252"`、
  Chunk 文本正确且不含替换字符，定位器回到 `p` 元素。
- UTF-8 正常路径与无效编码失败路径的既有用例保持通过。

## AC-5（R5）：修正归档记录

结果：通过。

- V0-01/V0-02 的 `prd.md`、`implement.md` 收尾项与 `task.json` notes 改为
  “已实现并归档”，并显式指向本修复任务及缺陷清单；原验收记录保留当时的实际边界，
  未补写当时没有执行的检查。

## 实际执行

最终复核在本任务独立的空数据库（容器 `stocks-v0-03-accept-postgres` 内的
`v0-08-final`，配套临时超级用户角色）一次性完成，验收后连同角色删除。

```text
Python 3.12.15 + PostgreSQL 17.11/pgvector 0.8.7（全新任务数据库）:
全新空库结构检查:
.venv/bin/alembic upgrade head
exit 0
.venv/bin/alembic check
No new upgrade operations detected.

TEST_DATABASE_URL=<isolated-test-db> \
  .venv/bin/python -m pytest -q \
  tests/test_repository.py tests/test_collection.py tests/test_processing.py \
  tests/test_api.py tests/test_s3.py tests/test_embeddings.py \
  tests/test_search_cli.py tests/test_research.py tests/test_execution.py \
  tests/test_providers.py
exit 0
168 passed

.venv/bin/python -m compileall -q src tests
exit 0
```

本任务直接改动覆盖 `46 passed`（repository + collection），其余为共享采集读取语义与
研究调用链的受影响套件；开发阶段规则不要求全量回归，此处范围只到实际影响面。过程中 3 个新增用例先失败，原因分别是：测试用的
`httpx.Response(content=...)` 在 `stream()` 下已被消费（改为 `stream=httpx.ByteStream(...)`）、
重定向畸形 `Location` 由 httpx 拒绝而不会进入本仓库代码（删除不可达分支并改为断言
既有 `REQUEST_FAILED` 脱敏结果）、把纯文本正文声明为 `text/html` 触到解析器的既有
分块行为（改用真实 HTML 结构）。均修正测试写法后复跑通过，未把失败中间结果计为通过。

## 未验证与限制

- 本任务没有再次执行真实 SEC 采集与真实 S3 对象核对；R2/R3/R4 以固定响应验证边界，
  真实来源链路由 V0-07 端到端验收时统一复跑。
- 新发现一个 **V0-03 解析器既有缺陷**（本次审查中发现，未在本任务范围内修改）：
  正文文本直接位于 `article` / `main` / `body` 而没有内层块元素时，
  `parse_content()` 产出的 Chunk 文本被逐字符换行拆成
  `"L\ne\nt\na\nu\nx..."`；`<p>`、`<div>` 等内层块元素正常。真实页面通常带内层
  块元素，但这是可复现的解析正确性问题，需要单独任务修复并补回归。
- `get_latest_document()` 语义变更影响“最新内容”读取，当前唯一调用方
  （采集跳过判断）与新增测试已覆盖；V0-07 的证据阅读 API 必须按采集状态指针读取
  最近内容，不能回到最大版本号。
- `ruff` 与 `mypy` 未安装在项目约定持久环境，未运行；已执行 `compileall`。
- 开发阶段不维护增量迁移；本任务未改数据模型，验收只使用全新数据库。
- 清理：删除本任务创建的测试数据库、临时角色、远程代码目录与归档包；
  沿用的 `stocks-v0-03-accept-postgres` 容器属早期任务，保持运行不删除。
