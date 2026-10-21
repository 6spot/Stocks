# V1-01 实施计划

## 开工条件

- [x] 已复核前置交付，V0-07 阅读 API 已归档；PA-1 真实闭环保持延后。
- [x] 已核对当前配置、执行服务、采集 CLI、模型与已有并发测试。
- [x] 用户在本轮范围说明后明确要求“开始实施”，批准最小采集 Task 接入和 V1-02 边界。
- [x] design.md 与本计划已细化；派发上下文含远程测试规范与最新开工证据。

## 执行顺序

- [x] Implement 读取完整领域/工程规范和相邻实现，确定最小调度库、重试历史/关联及配置结构。
- [x] 扩展 Task/trigger/采集步骤配置、Run/调度/重试持久模型及当前初始化定义；更新示例，清除被替代约束。
- [x] 拆分触发入队、数据库原子领取与执行；接入现有 collect_source；保持模型配置失败与其他 Task 隔离。
- [x] 实现持久 due 调度、独立 Worker 单轮/连续执行与独立任务执行能力；增加 Compose Worker。
- [x] 实现 Run 列表/详情、Owner 显式确认停机后异常结束、重试及完成步骤复用；所有权与历史记录不得丢失。
- [x] 将实际配置/调度/恢复行为更新到 docs/execution.md、docs/quickstart.md；必要时更新 docs/validation.md。
- [x] Implement 在唯一测试服务器执行最小相关验证并记录准确结果（首批 90 项 + 新库 `upgrade head`/`check`；第二批 52 项回归、Compose `config`/构建、9 阶段 Worker/CLI smoke、AC-3 `SIGKILL` + Owner `end`/`retry` 演练 18/18 通过），详见 `acceptance.md` 与 `research/remote-verification.md`。

## 最小必要验证（仅远程）

由 Implement 在 `root@100.120.14.84` 创建本任务隔离目录，遵循 `.trellis/spec/backend/testing-guidelines.md`，不读取或输出真实凭据。不改其他任务的资源，复用持久 Python 3.12/uv。

- 优先运行 `tests/test_execution.py` 和新增 scheduler/worker 测试文件；采集适配涉及原服务行为时运行相关 collection 用例。不默认全量回归。
- 确定性时间测试：cron 时区/DST、every 固定锚点、首次注册/变更、disabled、停机合并与繁忙跳过。
- 真实 PostgreSQL：并发 manual/schedule/retry 至多一个活跃 Run；多 Worker 不重复领取；跨 Task 故障隔离；已有模型初始化失败不阻止采集入队/执行。
- 独立进程：中断 Worker 后保留步骤/原件/错误线索；Owner 确认停机后结束/重试，成功步骤复用且旧报告引用不变。固定来源与 mock 模型可以测试恢复行为，不冒充真实模型闭环。
- 新空库 `alembic upgrade head` + `alembic check`，不验证旧库升级。
- Compose 发生变化，执行相应 config/构建及 Worker CLI smoke；检查工具未配置时不要临时引入整套 lint/typecheck 依赖，说明适用范围。
- SSH 不可用：记录实际错误、退出码及所有未运行项；继续完成实现与静态 Check，不在本机运行任何检查。

## 收尾

- [x] acceptance.md 逐项映射 AC 与命令/结果，注明未验证限制、远程清理情况与 PA-1。
- [x] Check 审查完整任务 diff（含新增 scheduler/worker/测试与 `ac3_drill.py`）、模型与初始化一致性、
  配置/示例、CLI/文档以及所有权/恢复边界；发现与修复记录见 [review.md](review.md)，
  acceptance.md 已重写为唯一结论（远程 142 项 pytest + 18 项真实进程检查；旧“全未运行”叙述已作废）。
- [x] 工程知识已写入 [.trellis/spec/backend/worker-guidelines.md](../../spec/backend/worker-guidelines.md)
  （Session/领取/调度轮/两层所有权/空闲等待与退避/自有线程池/重试复制）并链接进 index；
  `testing-guidelines.md` 补充密码认证与会话凭据、`BatchMode` 失败不等于服务器不可达、环境缺失须及时
  反馈，并在错误矩阵拆分认证失败与网络不可达（未写入任何凭据）。
- [x] 在远程重跑一次 `tests/ac3_drill.py drive`，确认 Check 收尾对 `reset()` 的收窄（只清本演练 Task的 Run
  及关联行、有活跃 Run 时拒绝）仍得 `result=passed`：退出码 `0`、`checks_total=18 passed=18`、9s。
  这是 PRD AC-3 已勾选的依据；同步修正了“等待复跑”的旧结论（acceptance / review / remote-verification）。
- [x] 清理本任务远程资源（容器 `stocks-test-v1-01-postgres`、测试镜像、远程任务目录、`.tgz`、
  `/root/.stocks-v1-01-dbpass`、`/tmp/ac3-*` 演练产物），未动其他任务资源与共享工具环境；
  脱敏清单与退出码见 remote-verification.md 第 10 节。
- [x] 已整理 [commit-plan.md](commit-plan.md)：一个工作提交与精确文件清单，排除现有 Pi/Trellis 改动；等待用户按 Phase 3.4 确认后提交。当前不 push、不归档。

## 上下文读取

`docs/data.md` 可能超过自动注入上限，子 Agent 必须完整读取（必要时分页），不要将注入前缀当作全文。研究增量、解析处理链不在本项提前实现。
