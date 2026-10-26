# 远程验证记录

日期：2026-10-26。所有应用测试、数据库初始化、查询和 API 验证均在 `root@100.120.14.84` 的 `/tmp/stocks-v0-application-pipeline-audit-jev` 执行。本机仅阅读、编辑、打包及保存脱敏验证产物。

## 环境

- Python 3.12，复用 `/opt/stocks-test-runtime/tool-venvs/stock-tools`；当前项目通过 editable 安装指向本任务 `source/backend/src`。
- 独立 PostgreSQL/pgvector：`stocks-test-application-pipeline-audit-jev-postgres`，仅 loopback `15547`。
- 独立 SeaweedFS：`stocks-test-application-pipeline-audit-jev-s3`，仅 loopback `18347`，数据目录在本任务路径内。
- 全新数据库执行 `alembic upgrade head`、`alembic check`，均 exit 0；未验证历史迁移。
- 随机运行凭据只保存在服务器任务目录，未复制至仓库。

## 实际结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| `pytest tests/test_classification_jev.py tests/test_classification.py tests/test_providers.py tests/test_worker_processing.py -q --tb=short` | exit 0，113 passed | `jev-regression-final.log` |
| execution/embedding 的 provider、route_snapshot、model_configuration、embedding_configuration、embedding_response 相关用例 | exit 0，23 passed，53 deselected | `shared-routing-tests.log` |
| 两个审查探针，断言配置路径一致、历史 Fact 应保留 | exit 1，两个断言失败，分别复现 MP-01 / MP-02 | `audit-probes.log`、`../research/audit_probes.py` |
| 同批数据手动链路 | exit 0，所有检查点通过 | `manual-smoke-passed.log`、`manual-smoke-manifest.json` |
| 补充边界时意外执行全套后端 pytest | 642 passed，1 warning；后续七个 node ID 是 shell 命令错误，不是单独 pytest 结果 | 工具 stdout 摘录与说明在 `execution-notes.md` |
| `docker compose --env-file .env.example config --quiet` | 隔离副本从示例创建 `.env` 后 exit 0 | `execution-notes.md` |
| Magpie 真实 Jev / 当前适配器 | exit 0，36 个问题，`jev-1.14.0`，4108 tokens，约 1.833 秒 | `live-adapter-result.json`、`real-answers.json`、`../research/magpie-live.md` |

先前定向回归共 **136 passed**；后续意外全量结果为 **642 passed**，两组有重叠，不相加。两个探针的失败是对未修复原有问题的复现，不计作通过，也不混入 Jev 回归结果。

完整 smoke 从远程项目根执行 `backend/.venv/bin/python .trellis/tasks/10-26-application-pipeline-audit/research/manual_pipeline_smoke.py`，提前导出本任务 `runtime.env` 与隔离 S3 配置。来源及五类模型调用使用受控 HTTP transport；PostgreSQL/pgvector、原件 S3、API 认证/CSRF、Worker 执行、解析、分类归一化、实体与 Fact 校验、选证、Analysis/Report 保存、引用和原件接口均执行真实实现。Worker 执行函数直接调用，未启动长期 Worker 子进程或浏览器。

首次 smoke 在采集原件保存时失败：本任务 SeaweedFS 默认可用卷数量不足，日志确认 `No writable volumes`。调整本任务容器为 `-volume.max=32 -master.volumeSizeLimitMB=32` 后复跑通过。此项归为隔离测试设施问题，不列为应用缺陷。成功记录保留 `external_success=false`。

## 未验证与后续条件

- 单独官方 Jev 已在用户授权的 Magpie 代理上真实验证；没有打印或导出上游 TypeSafe 密钥。真实来源、研究生成模型及向量供应商联合运行仍未验证，不把单次 Jev 与前述受控 smoke 拼接成真实完整闭环。
- 未进行浏览器渲染或从浏览器点击引用的验收；已验证报告、引用、原件及语义搜索 API。
- 未构建 Compose 镜像或部署生产服务；已通过示例环境的 Compose 结构检查，不能以此替代部署验收。
- 原计划不运行全量测试，但补充边界命令因 shell 续行错误意外运行一次全套后端 pytest，已如实记录且没有重跑。未运行全量前端测试，未扩大审查到定时调度。

## 清理

2026-10-26 已完成清理，远程命令 exit 0：删除 `stocks-test-application-pipeline-audit-jev-postgres`（含其匿名测试卷）、`stocks-test-application-pipeline-audit-jev-s3`，以及 `/tmp/stocks-v0-application-pipeline-audit-jev` 全部任务目录（含测试数据、临时凭据与传输归档）。删除后检查两个容器和目录均不存在。公共 `/opt/stocks-test-runtime` 保留，未触及其他任务资源。

`manual-smoke-manifest.json` 中 `cleanup` 保留的是 smoke 结束时的原始状态；本节记录主 Agent 随后的实际清理结果。所有复核 Agent 已结束，没有遗留后台测试进程。

后续 Magpie 真实 Jev 使用独立 `/tmp/stocks-v0-application-pipeline-audit-jev-live`，其目录和私有网关凭据也已删除（exit 0）；临时 SSH 反向转发已关闭。它没有创建数据库或 S3 容器。
