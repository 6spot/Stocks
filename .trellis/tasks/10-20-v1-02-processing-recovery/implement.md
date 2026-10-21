# V1-02 实施计划

## 开工状态

- [x] 复核 V1-01 已推送交付及归档验收；PA-1 继续延后。
- [x] 阅读当前 parse/index、Repository、Worker、模型和相关测试，记录[开工复核](research/kickoff-review.md)。
- [x] 收敛 PRD、技术设计与执行顺序；分类/Fact 算法留在 V1-03/V1-04。
- [x] 裁剪 implement.jsonl/check.jsonl，补充远程测试、Worker 及本轮研究上下文。
- [x] Owner 在最终规划后回复“开始”，批准本方案；随后激活任务并派发 Implement。

## 执行顺序与审查点

1. [x] 统一持久状态和事务入口：修改当前 ORM/初始化；Document 入库同事务创建待办；解析/索引最终结果与阶段完成同事务提交；所有入口共用 token 校验。先实现并发领取与失败状态，不接自动轮询。（批次 A）
2. [x] 阶段服务：依赖、适用性/处理器可用性、有限自动重试与退避、恢复和显式重处理。解析失败真实重试，索引继续复用已存向量。完成本步后审查“成功结果已提交而状态仍未完成”的中断边界。（批次 A）
3. [x] 固定处理器验证分类/Fact 接入边界：分类失败仍索引、明确数值无需向量可跳过、未接入处理器保留等待、重分类不重开 Fact。不加入真实分类/Fact 算法。（批次 A 协议 + 批次 B 下游重开修正与 CLI 可见性）
4. [x] Worker 独立文档槽位与有界等待；线程池容量明确对齐，Run/调度保持进度；更新 --once 输出和安全错误处理。（批次 B）
5. [x] CLI 查询/重试/重处理与旧手动解析/索引入口统一；补设置、部署传入和归属文档，删除被替代的平行执行路径，不做兼容。（批次 B）
6. [x] 分批完成远程最小验证；Check 全范围审查本任务 diff，修复后仅重跑受影响项；更新 spec、验收记录与逐项 AC。

最终 Check 已完成（三轮收敛）：首轮修复 7 项确认缺陷（显式重处理请求保留、中断重建不误判完成、中间向量批次所有权校验、
依赖跳过传播与重开、文档槽位独立 token、`--once` 暴露阶段失败、手动默认目标只取可领取行），新增 8 个固定用例；
远程受影响范围 185 passed + 34/34 真实子进程 smoke + 全新库结构一致。第二轮修复 4 项 `expire_on_commit=False`
下的并发/缓存实体缺陷（决定读未锁住权威行、行锁内用缓存解析指针、新当前批次未作废旧输入的运行中阶段、
索引结局缺处理器/配置身份），补 2 条真并发用例（修复前均失败、修复后通过）。

第三轮 NARROW 复审：逐项确认锁顺序单向、全链决定读已改为权威行、所有权切换由两个独立 Session 真实完成、
新批次作废/重领不吃重试预算、已完成 facts 不被重置、结果与阶段同事务回滚。本轮仅修 1 处同类新鲜度缺口
（`runner` 的 index 前置判断改 `populate_existing`），并修正两处文档不准确表述（状态推导只适用于
`completed`/`failed`；研究 Run 仍读并核对自身 Embedding 路由快照）。最终远程 106 passed（5 文件）
+ 34/34 真实子进程 smoke；无结构变更，未重建数据库。详情与逐项命令见 [research/review.md](research/review.md)。

批次 A 记录见 [research/batch-a-notes.md](research/batch-a-notes.md)，批次 B（Worker/CLI/文档与远程 smoke）见 [research/batch-b-notes.md](research/batch-b-notes.md)。

每次派发范围要有界：先持久化/服务，再 Worker/CLI，最后验证和 Check。SSH/依赖阻塞立即报告，不长时间静默迭代。主会话负责协调，Implement/Check 不递归派发。

## 最小必要验证（只在测试服务器）

本轮规划未运行以下命令。远程目录使用 `/tmp/stocks-v1-02-processing-recovery-<run>`，数据库/容器只使用本任务名字及独立端口。先检查本任务遗留资源，复用持久 Python/uv；重新 editable 安装到本任务目录并核对 `stocks.__file__`，不重装整套依赖。

远程命令模板（密码由会话临时注入，不能写入记录）：

```bash
cd /tmp/stocks-v1-02-processing-recovery-<run>/backend
ln -s /opt/stocks-test-runtime/tool-venvs/stock-tools .venv
/opt/stocks-test-runtime/bin/uv pip install --python .venv/bin/python --no-deps --force-reinstall -e .
.venv/bin/python -c 'import stocks; print(stocks.__file__)'
.venv/bin/alembic upgrade head
.venv/bin/alembic check
.venv/bin/python -m pytest -q tests/test_processing_retry.py
```

`DATABASE_URL` 只指向任务新建的空库。新增 test_processing_retry.py 后才执行；其他相关文件按实际变更选用例：

- `test_repository.py`：新版本登记待办、重复字节复用、结果/阶段事务一致。
- `test_processing.py`：失败批次重试、成功复用、重解析失败保留当前成功结果与引用；不修改解析算法就不默认重跑全文件。
- `test_embeddings.py`：分批中断继续、模型空间隔离、显式重索引复用 Chunk、旧批次引用保留。
- `test_scheduler.py`：文档槽位与 Run/调度互不饿死、空闲等待、--once 及并发领取。只跑受影响组。
- `test_report_api.py`：若重处理/引用路径受影响，选择旧报告读取及引用稳定性用例。

需要实际验证的关键边界：两个独立 Session 不能领取同一阶段；Owner 确认停机后的恢复拒绝旧 token；持久化分批结果后中断能续做；重试耗尽不热循环；缺少分类模型不妨碍索引；处理器未实现不是 skipped；完成资料不因模型切换自动重开。

新增容器 Worker/CLI 有界 smoke，覆盖入库→后台 parse/index→status→失败/中断→恢复；模型/S3 默认固定响应或任务隔离存储。只有部署文件受影响时追加 Compose 配置/构建检查。不得把固定处理器结果当真实 Provider 或 API Source Mapper 验收；不默认全量回归。

## 上下文读取

`task.py validate` 已确认 implement/check 各 15 条上下文路径有效；`docs/data.md` 为 41,268 字节，超过默认单文件 32,768 字节注入上限。Implement/Check 必须分段完整读取该文件，不能把注入前缀当全文；不为此修改全局配置。这是规划文件校验，不是代码验证。

## 收尾与回退

- [x] 逐项记录 PRD AC 的实际结果、命令、退出码及未验证限制；未接入能力继续明确待接入。
- [x] 同步唯一归属文档和实际 CLI，检查 V1-03/V1-04 对本任务接入契约的依赖。
- [x] 更新必要工程 spec，保留原始证据、旧批次、失败信息与最后成功结果。
- [x] 清理本次创建的远程资源；不删除其他任务环境、公共运行时或工作区无关改动。（2026-10-21 10:49 UTC：3 个容器及 2 个专用匿名卷、a1/b1/c1 与 S3 数据目录、17 份传输归档已移除；无遗留 Worker/smoke 进程，公共运行时及其他 10 个容器保留。精确清单见 acceptance.md 的清理记录。）
- [x] 交付前生成 [精确提交清单](commit-plan.md)；Owner 已明确授权工作提交、推送与归档，按本清单执行。

风险文件：repository.py 的隐式提交、处理入口所有权和 worker.py 常驻槽位。出现结果与状态分离或 Run 调度回归时先暂停文档槽位，修复事务/执行边界后再推进。不得靠删除历史结果、增加兼容路径或扩大到分类/Fact 算法绕开问题。
