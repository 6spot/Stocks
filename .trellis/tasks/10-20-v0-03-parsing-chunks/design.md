# V0-03 技术方案

## 边界与数据流

- 解析与分块是后端普通步骤；V0 手动调用，V1 再由持久 Worker 驱动。
- 输入为不可变 Document 及其 S3 原件；处理只读取 Document 的
  `content_type`、`content_hash`、`snapshot_key`，不下载或改写正文。
- 纯解析器把 HTML/XHTML/纯文本转换为有序语义块，再按字符预算和重叠合并为
  Chunk。每个 Chunk 保存可见文本、顺序、MIME/处理器版本和原文位置。
- Repository 负责持久化批次、状态、Chunk 与事务；CLI 只解析参数并汇总结果。
- 失败、不支持与空文本只写处理批次/最新状态，不创建成功 Chunk；原件和此前成功
  批次保持不变。

## 持久化结构

- `document_parse_batches`：每次实际处理的不可变记录，保存 `document_id`、
  `processor_version`、配置指纹、内容哈希、状态
  `completed | failed | unsupported`、错误、Chunk 数和时间。
- `document_parse_states`：每份 Document 一行，`latest_batch_id` 指向最近尝试，
  `current_batch_id` 指向最近成功批次；最新状态、错误、处理器版本和尝试时间独立保存。
- `document_chunks`：增加 `parse_batch_id` 和 `locator` JSONB，唯一约束改为
  `(parse_batch_id, position)`。Chunk 文本、位置与批次创建后不原位更新。
- `locator` 对纯文本保存解码文本中的元素级字符起止范围；对 HTML 保存语义块
  类型、元素路径和原始 HTML 字符范围。`segments[].locator.source_ranges` 保存
  切片实际可见文本的有序原文范围；普通连续文本可合并范围，无法从原文推导的
  合成空白和表格分隔符不伪造来源范围。HTML 实体覆盖完整实体原文，表格单元格
  文本保留自己的原文范围。表格行级 `locator.separators` 标记系统生成的
  ` | `。跨块 Chunk 保存有序 `segments[]`，足以回到具体段落、列表项或表格行复核。

## MIME、解析与分块

- 只支持 `text/html`、`application/xhtml+xml`、`text/plain`。按 Media Type
  选择解析器，配置中的 charset 只决定严格解码，切换采集渠道不改变流程。
- HTML 优先使用 `article` / `main` 内语义块，排除 `script`、`style`、`noscript`、
  `template`、`svg`、`nav` 和表单控件；保留标题、段落、列表项、引用、预格式文本
  及表格行。表格转换为保持行/列关系的文本。
- 文本按非空段落切块；HTML 语义块先形成有序片段，再在不超过字符上限的情况下
  合并。句/段落边界优先，只有超长块才按字符切分；重叠只复制相邻上下文，同一
  原件位置可被多个 Chunk 引用。
- 默认 Chunk 字符上限 1,200、重叠 150，通过 `PARSE_CHUNK_SIZE` 和
  `PARSE_CHUNK_OVERLAP` 配置；要求 `0 <= overlap < size`。处理器版本
  `semantic-text-v1` 与规范化配置共同构成结果指纹。
- 解码失败、空可见正文、解析器无法形成有效块记为 `failed`；不支持的 MIME 记为
  `unsupported`。错误只含稳定 code、安全 message、MIME 或字符数等诊断字段。

## 幂等与重处理

- 普通调用先查同一 Document 下相同处理器版本和配置指纹的最近批次：
  `completed` 直接复用；已记录的 `failed` / `unsupported` 也复用，避免普通重复
  运行制造无穷批次。
- `force_reprocess=true` 必须创建新批次。成功批次成为 `current_batch_id` 并新增
  Chunk；失败/不支持只更新 `latest_batch_id` 与最新状态，`current_batch_id` 及旧
  Chunk 不变。
- 不提供自动清理、级联替换或按 Document 删除全部 Chunk 的入口。历史报告引用
  Chunk ID 后，新增批次不会改变该 ID 的 text、locator 或批次。
- 数据库写操作在一个事务中提交批次、Chunk 和状态指针；异常回滚。重复点击发生
  并发竞争时，最终读取仍以状态指针和不可变批次为准，不在 V0 预建分布式锁。

## 运行入口与读取

- `python -m stocks.processing [--document-id ID ...] [--limit N] [--reprocess] [--include-text]`
  处理显式 Document 或按 `collected_at` 倒序处理有界数量；默认 `--limit 20`，
  上限 200。
- CLI 默认输出每份文档的 `document_id`、版本、状态、是否复用、批次 ID、Chunk 数
  和脱敏错误，不输出 Chunk 正文；只有运维显式传入 `--include-text` 才输出供人工
  核对的本批次正文与定位，不属于前端读取接口。
- Repository 提供按 Document/当前批次读取 Chunk、读取最新状态和累计批次的能力，
  后续 V0-04 直接消费 Chunk，不重新解析 S3。

## 建议代码落点

- `backend/src/stocks/models.py`
- `backend/alembic/versions/0001_initial.py`
- `backend/src/stocks/repository.py`
- `backend/src/stocks/settings.py`
- `backend/src/stocks/processing/`：安全解析、分块、持久化编排与手动 CLI
- `backend/tests/test_processing.py`
- `backend/tests/test_repository.py`：仅补充批次持久化和引用保护边界
- `config` / `docs/quickstart.md`：实际运行参数与命令

## 输入输出与契约

`docs/data.md`, `docs/research.md`, `docs/validation.md` 是唯一业务契约来源。
本任务只实施 V0 手动解析/分块，不把 `current_batch_id` 当作“原件版本”或研究
已消费状态。分类、Fact、Embedding 和检索状态留给各自阶段。

## 兼容、风险与回退

不保留旧版兼容、不新增增量迁移。数据结构变化直接改当前模型及初始化，验证隔离
的新开发库；不自动清理已有原始证据。代码回退不得原位修改已保存报告引用。

主要风险是宽松 HTML 产生导航噪声、错误的原始字符位置、超长表格破坏关系，以及
重处理覆盖历史引用。对应边界是语义容器优先与噪声标签排除、每块保留元素路径和
源范围、表格行作为不可拆分表格语义块、批次只追加。V0 不承诺对任意坏 HTML 得到
可用正文；无有效正文必须明确失败。

真实来源验收需要原 V0-02 SEC 原件或临时可用的 PostgreSQL/S3 环境。环境缺失时
保留该限制，不把固定响应测试写成真实来源验收。
