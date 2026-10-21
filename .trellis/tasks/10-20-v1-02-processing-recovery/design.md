# V1-02 技术方案

## 最小行为缺口与边界

开工时 `process_document`/`index_document` 只能被手动调用，`worker.py` 只领取 TaskRun。本轮增加 Document 的统一阶段待办和执行入口，复用已有算法、不可变结果与 Worker 进程。调查依据见[开工复核](research/kickoff-review.md)，最终实现及验证见[验收记录](acceptance.md)。

业务契约只在 `docs/data.md`、`docs/execution.md` 定义；本文件记录实施选择，不另立业务规范。不添加研究 Task 类型、DAG、外部队列或动态插件发现。

## 持久状态

新增最小 `document_processing_states`，每个 `(document_id, stage)` 一条当前记录；stage 为 parse/classify/facts/index。它负责排队/尝试/所有权，已有 ParseBatch/ParseState/EmbeddingState 继续负责不可变解析结果、当前成功指针与索引覆盖，各字段只有一个明确的写入入口，不各自推测运行状态。

状态字段包括 status（pending/running/completed/failed/skipped）、等待/跳过原因、尝试次数和自动重试预算、下次可尝试时间、最近开始/完成时间、安全错误、处理器版本与模型/配置指纹、输入批次引用、所有权 token。已成功结果与最近尝试状态分开，重处理失败不能撤销此前可用的 Chunk/向量。必要约束与待办索引写入 ORM 和 `0001_initial.py`。

- 入库新版本时，在创建 Document 的同一事务内创建四阶段记录；相同字节复用不会重开已完成阶段。
- pending 可因依赖未满足、实现未接入而等待；这些等待不消耗尝试次数，不轮询调用缺失处理器，也不记为 failed/skipped。
- completed 默认保持完成，不因为最新模型配置不同而自动重开。只在待处理阶段领取时解析并记录该次使用的配置身份，不保存密钥或 endpoint 明文。
- failed 可按预算与 `next_attempt_at` 有限自动重试，耗尽后只能由 Owner 重试。skipped 必须有明确适用性依据；不以 MIME/空结果/缺少配置推断没有可处理内容。

## 数据流与阶段接入

```text
原件保存 → Document + 四阶段待办提交
                  ↓ parse
            已存 Chunk/解析输出
             ├─ classify → facts（按需）
             └─ index（独立）
```

编排留在 `stocks.processing`：新增小型阶段服务模块，调用现有 parse/index 函数；classify/facts 使用普通 Python callable/result 契约，不建设运行时插件系统。处理结果包含阶段状态、持久结果引用、安全错误、处理器身份，以及下游适用性信息；分类输出的 should_extract 以后由 V1-03 校验、Fact 保存由 V1-04 实现，本轮不复制算法。

生产阶段可用性与依赖分开：当前注册 parse/index；文本 classify 与后续 facts 等待其实现接入。结构化处理器以后可明确说明无可检索文本、无需分类/索引并走字段映射；当前尚不支持的 JSON/XML/PDF 不会直接写成“数值无需向量”。本轮用固定处理器和真实持久化验证跳过及失败隔离协议，不将其写成真实模型/Source Mapper 已接通。

解析失败不释放依赖它的新处理；已有成功批次仍可供查询。分类/Fact 失败不改变索引资格。重新解析成功后，新当前批次需要索引；重分类及待处理的分类输入据实际批次更新，但已经完成的 Fact 不自动重开。Fact 重处理始终需要明确请求或新 Document 版本。

## 领取、事务与中断

1. 以短事务原子领取符合依赖、实现可用、未超预算且已到期的阶段，使用 `FOR UPDATE SKIP LOCKED` 与状态条件更新，分配 token 后提交。网络/模型调用不持数据库行锁。
2. 每次执行使用独立 Session。完成阶段前校验 token；与结果有关的持久化入口把阶段状态、结果引用及下一步待办写入同一结果事务。已有 Repository 会自行提交，实施时必须调整其可复用的事务入口，使最终成功状态不落在另一次提交之后。
3. 解析普通调用只复用成功结果；失败批次的重试产生真实新尝试，不再直接返回旧失败。显式重解析才强制建立新的成功批次。保留全部旧批次与 Chunk。
4. 索引继续分批提交向量，每批核对当前所有权与输入；最后一批覆盖核对与阶段 completed 同事务提交。普通索引中断后保留已提交向量、只生成缺失部分，不能把部分完成写成成功。显式重建请求在失败或中断后保留，继续执行重建，不能以原有成功结果抵消该请求。
5. running 不自动接管。Owner 的阶段恢复入口强制确认旧进程停止，清理该阶段 token，再判断已有持久结果是完成还是需要继续。旧 token 写入必须被拒绝；不承诺撤销已经提交的真实派生资料。解析成功提交新当前批次时，会同事务作废旧批次对应的运行中 classify/index，并将它们指向新批次重新排队；这属于输入变化，不是死进程接管。所有权和批次判断在数据库锁内以最新行为准，`expire_on_commit=False` 下的缓存实体不能作为授权依据。
6. 不增加处理历史平台；当前记录保存最近尝试，解析既有批次保留其历史。恢复不能通过删除原件/旧 Chunk/报告来实现。

## Worker 与 CLI

- 在同一 Worker 中增加独立文档处理槽位，默认 1、可配置有界并发；Run 槽位仍保持 V1-01 的常驻行为。线程池容量覆盖 Run 槽位、文档槽位及调度，避免处理积压饿死采集/研究或调度。
- 每个文档槽位一次领取一个可执行阶段，完成即尝试下一项；无可执行待办时有界等待，连续运行失败退避。阶段失败只影响本阶段，不让槽位退出；停止时不无限追赶积压。
- `worker --once` 在原一轮调度/至多一个 Run 外处理至多一个文档阶段，输出增加清晰的 processing 摘要；更新 CLI 帮助、退出状态及现有测试。
- `stocks.processing` 统一提供有界的 status/retry/reprocess 命令：按 Document、阶段、状态筛选；retry 只重开失败/未完成，reprocess 可显式选择成功阶段；处理 running 时必须 `--confirm-stopped`。列表有稳定排序及 1–200 上限，输出安全 JSON。
- 旧手动 parse/index CLI 调整为同一阶段服务的调用方，保留必要的显式处理能力，但不保留绕过所有权/统一状态的平行执行路径。命令形状变化同步 quickstart，不添加旧版兼容分支或未认证写 API。

## 预期文件与必要性

| 文件/模块 | 原因 |
| --- | --- |
| `models.py`、`alembic/versions/0001_initial.py` | 统一阶段记录、约束与待办索引 |
| `repository.py` | Document 入库时登记待办，结果与阶段完成同事务及 token 校验 |
| `processing/` | 阶段依赖、接入结果、领取/重试/恢复、解析失败真实重试及统一 CLI |
| `indexing/service.py`、`indexing/__main__.py` | 索引结果复用、批量提交与统一状态/CLI 接入 |
| `worker.py`、`settings.py`、`.env.example`、`compose.yaml`（需要传入设置时） | 文档槽位、线程池容量、运行设置与可观察输出 |
| 相关 tests、`docs/data.md`、`docs/execution.md`、`docs/quickstart.md`、`docs/validation.md` | 行为验证、唯一契约与实际可用命令 |

具体模块拆分以现有职责为准，不为表格预建空模块。不涉及前端、来源扩展、Fact 表、Decision Adapter 或增量水位。

## 风险与回退

最大风险是既有 helper 自行提交导致阶段状态与结果分离，其次是两个入口并发绕过所有权；先落实事务入口与领取验证，再接 Worker/CLI。处理未接入阶段时必须显式展示等待原因，避免把能力缺失写成完成。

只改当前初始化，不迁移、不回填旧库。回退停止 Worker 并回退代码；需重建的仅限开发/任务隔离库，不删除运行时证据或改写历史报告。外部能力未接通要保留限制；PA-1、PA-2 及 ARM 验收不在本任务中关闭。
