# 验收记录（2026-10-25，测试服务器 100.120.14.84）

隔离目录 `/tmp/stocks-daily-loop-1`；探测库 `stocks_dl_probe`（stocks-test-v0-03-postgres 容器，127.0.0.1:55435）。模型 chat 端点 TokenHub（deepseek-v4.1-flash），embedding 经 `http://172.17.0.1:13426/v1`（bge-m3，1024 维），S3 指向 `127.0.0.1:18346`（桶 `stocks-both`）。

## 实施内容

1. **R3 进程日志配置**：`worker.py` 与 `processing/__main__.py` 的 `main()` 增加 `logging.basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")`。此前 Worker 容器日志 0 行；探测运行中 `worker-final.log` 可见 httpx 请求与 WARNING（例：`Collection ingestion failed ... error_type=RuntimeError`），正是这条日志暴露了初期 S3 未配置导致的 INGEST_FAILED。
2. **R1 采集 Task**：`config/tasks.yaml` 新增 `collect_enabled_sources`（type: collection、manual、sources: [sec_press_releases, fed_press_releases]）；两个来源补 `expected_freshness: 7d`。
3. **R2 真实研究 Task**：新增 `fed_policy_brief`（研究美联储政策与宏观；scope regions [US] / topics [macro, policy, market_sentiment]；3 条查询、limit_per_query 8、max_tokens 30000）。
4. **超时缺陷修复（R4 期间发现）**：`providers.py` 的 `research_chat` 此前用 provider 级 `target.timeout_seconds`（60s）构造 HTTP 客户端，`models.yaml` 中 `agents.research.routing.timeout_seconds: 300` 从未生效——10-25 已归档任务 `10-25-research-timeout-budget` 中两次"成功"（54.4s/55.9s）只是模型恰好跑得快。修复为 `max(target.timeout_seconds, routing.timeout_seconds)`，并新增回归测试 `test_research_request_uses_the_routing_timeout_budget`。

## AC-1 采集 Task 可触发

- 探测库手动触发 `collect_enabled_sources` → Run `4ab85aa2-…` **succeeded**；40 篇文档入库（sec 20 + fed 20，`documents` 按 source_id 分组计数）。
- Worker 持续运行后 40/40 文档四阶段完成：parse/classify/index completed 40，facts completed 32 + skipped 8（`should_extract=false` 显式跳过）。
- Fact 总数 286。

## AC-2 禁用来源未被采集

`documents` 仅来自 `sec_press_releases` 与 `fed_press_releases`；`sec_edgar_submissions`、`bls_cpi_example`（未配置/禁用）0 篇。另：`select_sources`（`collection/config.py:179`）已实现 `SOURCE_NOT_FOUND` / `SOURCE_DISABLED` 稳定错误码（代码核实，本任务未改）。

## AC-3 真实研究 Task 产出报告

Run `628a9522-…`（fed_policy_brief）**succeeded**，耗时 231.5s：

- 报告《美联储政策与宏观简报》28,227 字符，28 条 findings、9 条 open questions、71 条持久证据引用，含确定性 `## Sources and Conflicts` 节。
- step usage：prompt_tokens 897,799 / completion_tokens 35,478 / total 933,277，1 次尝试，`cost_unknown_attempts: 1`（供应商未返回费用，保持未知）。
- 修复前同 Run 两次 MODEL_TIMEOUT（60.1s×2，model_calls 有记录）；修复后单次尝试 230.9s 成功——证明 300s 路由预算生效。

> 注：该成功尝试的 model_calls 行因 `on_conflict_do_nothing` 与同 run/step/attempt_number 的旧失败行冲突而未落库——这是本验收用 SQL 手工重排 Run 状态产生的副作用（正常流程 retry 会创建新 run_id），usage 已完整记录在 StepResult 上。

## AC-4 / AC-5

由并行任务 `10-25-fact-stage-robustness`（commit `377d17e`）实现并验收：PROCESSOR_FAILED 保留异常类名、期间表达式容错、5 个 failed 阶段全部恢复（0 failed）。本任务补充的进程日志配置让这些异常栈在容器日志可见。

## AC-6 端到端证据

完整链路在隔离环境实测：Task 触发 → 采集（40 文档）→ S3 快照 → 四阶段处理（含分类与 Fact 抽取，286 Facts）→ 研究检索（114 evidence items、40 evidence groups）→ Research Agent（93.3 万 token 输入）→ Analysis + Report 落库可读。中途实测复现真实模型延迟（研究调用 180s+）验证了超时预算修复的必要性。

## 生产部署验证（2026-10-25 18:20–18:45 UTC）

改动已部署到 `/opt/stocks`（5 个文件同步后重建 api/worker 镜像，容器 healthy；旧文件备份在 `/opt/stocks/backup-daily-loop/`）：

- **Worker 日志**：容器日志从 0 行变为可见采集/分类请求流（httpx GET federalreserve.gov、POST chat/completions 均为 200）。
- **AC-1 生产路径**：登录 Owner 会话 → `POST /api/tasks/collect_enabled_sources/runs` 排队 Run `d02f2860` → succeeded；生产库 Document 20→41（fed 20 篇新入库，1 篇 sec 更新），四阶段积压全部清零（parse/classify/index completed 41，facts completed 30 + skipped 11）。
- **AC-3 生产路径**：`POST /api/tasks/fed_policy_brief/runs` → Run `614b6c3e` **succeeded**；报告《美联储政策与宏观简报》14,909 字符，23 条 findings、21 条证据引用；`model_calls` 正常落库（research attempt 1 succeeded，68.0s，592,541 token）——确认探测库 model_calls 缺行只是验收 SQL 手术的副作用，正常流程的调用记录完整。

## 遗留

- 11 个 facts skipped 为分类决策显式跳过，非故障。
