# V0-03 脱敏验收记录

验收日期：2026-10-20

## 环境与范围

- 使用当前工作区代码，在远程 Python 3.12.15 环境执行；数据库为任务独立的
  PostgreSQL 17 + pgvector 空库。S3 调用使用固定 Mock；本次没有使用真实对象存储
  bucket 或外部凭据。
- AC-1 至 AC-3 的解析行为、失败路径、分块边界和事务行为由固定 HTML/文本输入及
  远程 PostgreSQL 测试覆盖。真实 SEC 来源尚未完成验收，原因见 AC-4。
- 数据库密码和对象存储密钥只存在于远程系统临时文件或进程环境，未写入仓库、
  验收记录或测试输出。
- 验收目录和容器名称使用 V0-03 独立标识；未删除其他用途不明的测试容器。

## AC-1：正文片段、数字单位与定位

结果：通过。

固定 HTML 输入从保存前的原始字节生成 Chunk，保留标题、段落、列表、表格及
数字/单位。段落、表格行和单元格在 `segments[].locator` 中保存元素类型、
原始 HTML 元素范围、元素路径和 `coordinate_unit = decoded_html_chars`；
`source_ranges` 保存可见文本的有序原文范围，普通连续文本可以合并范围。
HTML 实体的来源范围覆盖完整的 `&amp;` 原文，解码后的可见文本为 `&`；嵌套
行内元素和前导空白均可回查，合成空白不能冒充原文。

表格的 ` | ` 和空白在 `locator.separators` 标记为系统生成的位置，不能当作
原文单元格；表格单元格保留自己的原始来源范围，合成空白不伪造范围。跨语义块
及跨 Chunk 的正文使用有序 `segments[]`。纯文本使用严格解码文本的元素级字符
范围，切片以 `source_ranges` 核验实际字符，而不是只依赖元素级范围。固定输入还核对相邻 Chunk 的
重叠、字符预算和超长段落切片。未知或不完整的 `&name` 序列按原文保留；1,000 组
固定随机种子组合覆盖 HTML 实体、语义分段和从 8 到 80 字符的预算/重叠配置。

## AC-2：畸形与不支持内容

结果：固定响应测试通过；真实外部畸形网页未验收。

- 固定响应中的 `application/pdf` 记录为 `unsupported`，没有写入成功 Chunk。
- 固定响应中的无效编码和无可见正文记录为 `failed`，返回稳定错误码
  `INVALID_ENCODING`、`NO_EXTRACTABLE_TEXT`；没有写成空的成功正文。
- 固定响应与远程 PostgreSQL 测试拒绝完成状态无 Chunk 的批次形态。解析失败
  不会删除或改写 Document 所引用的原件。
- 在远程全新空库执行 `alembic upgrade head` 和 `alembic check`，当前模型与
  初始化结构一致。

## AC-3：幂等与历史 Chunk 引用保护

结果：固定响应与远程 PostgreSQL 测试通过。

- 同一 Document、处理器版本和配置指纹再次处理返回原批次，`reused = true`，
  不新增批次或 Chunk。
- `--reprocess` 创建新批次并切换到新的当前批次；旧批次与旧 Chunk 仍可按批次读取。
- 模拟重处理失败时只更新最近尝试状态，当前成功批次和 Chunk 保持可读。
- 历史 Chunk 与新批次 Chunk 使用不同主键，而文本和定位可保持一致，证明新处理没有
  原位改写旧引用。
- 同一 Document 与处理器版本、配置指纹的失败/不支持结果也由批次状态复用，
  普通重复调用不增生无意义批次。

## AC-4：真实 SEC Document 关联验收

结果：未验收，PRD 的 AC-4 保持未完成。

- 当前测试配置中的 SEC `user_agent` 仍是示例邮箱 `owner@example.com`，无法证明
  所声明的真人联系身份；不将示例邮箱或已出现的反爬响应冒充真实来源成功。
- 远程 S3 兼容测试服务的对象权限在安全清理后不再允许读取此前的临时 bucket，
  V0-02 当时的临时数据库也已撤除。只有既有的分散临时 HTML 文件，不能证明它们
  属于当前 Document 版本、当前对象快照及当前解析批次，因此不将其拼成新的
  “真实 Document → 批次 → Chunk”证据链。
- 取得可信 SEC 联系身份和可复核的当前原件后，仍需执行以下验收：保存完整 HTML，
  重读对象并核对字节哈希；执行当前解析器，核对 Document、批次、状态指针和
  Chunk 的关联；人工核对至少一个数字/单位、元素路径和原始字符范围；再验证相同
  调用复用及显式重处理保留旧 Chunk。

## 实际执行

```text
远程 Python 3.12.15 + 全新 PostgreSQL 17/pgvector:
TEST_DATABASE_URL=<temporary-test-db> \
  .venv/bin/python -m pytest -q \
  tests/test_processing.py tests/test_repository.py tests/test_s3.py
exit 0
47 passed in 0.87s

全新空库结构检查:
.venv/bin/alembic upgrade head
exit 0
.venv/bin/alembic check
No new upgrade operations detected.

后端相关文件静态检查:
ruff check backend/src/stocks/processing \
  backend/tests/test_processing.py backend/src/stocks/api.py \
  backend/src/stocks/repository.py backend/src/stocks/models.py \
  backend/src/stocks/settings.py backend/src/stocks/s3.py \
  backend/tests/test_s3.py backend/alembic/versions/0001_initial.py
All checks passed!

当前 CLI 的默认输出是每份 Document 的 JSON 摘要；显式 --include-text
额外包含本批次正文和定位，供人工复核，不输出凭据。
```

上述测试在本地和远程均执行过；本地同一相关测试子集为 `28 passed,
3 deselected`（排除依赖真实 PostgreSQL 的用例），没有把本地结果替代远程结果。
远程测试使用当前工作区传输代码，持久运行环境的 `.venv` 指向验证代码目录。

## 未验证与限制

- 真实来源只覆盖 SEC RSS 与静态 HTML；没有验证 JavaScript 渲染页面、PDF、JSON、
  XML 或 OCR，这些格式当前明确不在 V0-03 范围。
- 真实 SEC 的正文获取、Document/批次/Chunk 关联、人工定位和重处理尚未完成，
  固定输入测试不能替代这次阶段验收。
- 未实现 Embedding、检索、Fact、Task/Run、报告读取或前端解析状态页面。
- 当前验证使用的一次性 PostgreSQL 和对象存储仅用于测试；测试完成后清理临时
  凭据，持久 Python 3.12 环境保留给后续测试复用。
