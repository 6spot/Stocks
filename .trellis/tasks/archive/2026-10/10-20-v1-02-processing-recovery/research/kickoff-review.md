# V1-02 开工复核（2026-10-21）

## 前置交付与授权

- Owner 要求“提交并推送，然后开始下一个任务”。V1-01 工作提交 `8f67efe`、归档提交 `8b1d5a5`、日志提交 `b0c4ef7` 已推送到 `origin/feat/v0-07-evidence-api-acceptance`。
- 前置任务已归档到 `.trellis/tasks/archive/2026-10/10-20-v1-01-worker-scheduling/`；验收包含远程 142 项 pytest、新库初始化、容器 smoke 与真实进程中断恢复。PA-1/PA-2 不因此关闭。
- 本次进入现有 V1-02 规划，不创建重复任务。最终实施方案尚待本次规划评审，未执行 `task.py start`，未编辑产品代码、启动 SSH 或测试。
- 无关 Pi/Trellis/Magpie 工作继续留在工作区，不属于本任务。

## 当前代码证据

| 落点 | 已有能力 | 本任务缺口 |
| --- | --- | --- |
| `stocks.repository.ingest_document` | S3 原件先存、不可变 Document 版本、采集状态一起提交 | 入库事务没有创建统一后处理待办 |
| `stocks.processing.service.process_document` | 哈希验证、解析、不可变批次及 Chunk | 普通路径复用最近批次，包括失败批次；没有自动有限重试/统一领取 |
| `stocks.repository.persist_parse_result` | 在 Document 行锁内创建批次、切换成功指针、提交 | 没有与统一阶段完成状态同事务提交的入口 |
| `stocks.indexing.service.index_document` | 按批次生成向量、复用已存向量、保存错误和覆盖 | 没有统一的待办、持久尝试次数及手动重试入口 |
| `stocks.repository.persist_embedding_result` | 向量批量提交，完成必须覆盖当前批次全部 Chunk | 需要同步统一阶段进度与所有权校验 |
| `stocks.worker` | 独立调度循环、常驻 Run 槽位、独立 Session、有界空闲等待 | 只领取 TaskRun，没有文档处理执行槽位 |
| `stocks.models` | ParseState/Batch、EmbeddingState、Chunk/Embedding | 无分类/Fact 处理器、无统一四阶段运行状态 |
| `stocks.processing.__main__` / `stocks.indexing.__main__` | 手动解析/重处理、索引/重建及安全 JSON 输出 | 没有按阶段查询积压/入队/恢复入口 |

## 本轮确定的工程边界

- 复用已有解析、索引及 Repository 持久化函数；新增最小 Document 阶段状态记录，不建积压服务、DAG、动态插件平台或分类历史表。
- 四阶段为 parse/classify/facts/index。V1-02 实装 parse/index，定义另两阶段的普通 Python 接入边界；生产环境缺少处理器时保留 pending 和明确等待原因，不伪造 completed/skipped。
- 数值资料不需要向量的判断来自处理器显式产出的适用性信息，不按 MIME、空 Chunk 或缺少实现自动认定 skipped。当前 JSON/XML/PDF 解析仍未实现，保留真实 unsupported/待接入信息；本任务仅以固定处理器验证数值跳过协议，真实 Source Mapper 由 V1-04 验收。
- 文档运行与 TaskRun 仍分别保存状态；不让每个文档触发研究。Worker 为文档处理提供独立有界槽位，避免 Run 队列和文档积压互相饿死。
- 失败批次重试必须真正重新执行；成功批次和已提交向量继续复用。持久派生结果与统一状态的提交需要统一事务边界，防止“结果已提交、阶段未完成”造成重复解析。
- 运行中状态不自动接管。Owner 确认旧进程停止后才解除其所有权并恢复该阶段，延续 V1-01 的恢复边界。
- 默认不重处理成功资料。显式重索引读取已有 Chunk，模型切换只影响待处理资料；显式重分类不重开已完成 Fact。

## 验证落点与限制

实际已有测试文件是 `test_processing.py`、`test_embeddings.py`、`test_repository.py`、`test_scheduler.py`、`test_report_api.py`，不是 `test_indexing.py`。新增 `test_processing_retry.py` 验证统一流程。按改动选择最小用例，只在测试服务器本任务目录运行；本轮规划不运行验证。

复用远程 `/opt/stocks-test-runtime/`，将 editable 包重新指向本任务目录后再验证；不重装整套环境。不得把已清理 V1-01 目录造成的 import 失败当作环境损坏。所有密码只通过会话临时传递，不写任务记录。
