# 补充执行记录

以下是本次工具实际输出的摘录与解释，不是单独保存的远程 pytest 原始日志。所有执行均在 `100.120.14.84` 当前任务隔离副本。

## 意外的全量后端测试

原意仅补充 7 个处理重试、未完成解析与空选证边界用例。远程脚本在 `.venv/bin/python -m pytest -q --tb=short` 后缺少 shell 续行，导致这一行先独立执行，实际运行了全套后端测试。

实际 pytest 输出结尾：

```text
642 passed, 1 warning in 39.03s
```

Warning 来自 FastAPI TestClient 的 `StarletteDeprecationWarning`，提示将来安装 `httpx2`。

随后 7 个 `tests/...::test_...` 被 shell 当成命令，分别报“没有那个文件或目录”（exit 127）。末尾单独的重定向创建了空的 `manual-boundary-tests.log` 并返回 0，因此外层脚本 exit 0 **不代表原计划的 7 条命令各自成功**。未将该空文件保存为测试日志，未另行重跑或把这 7 项列为单独测试结果。完整 pytest 已包含这些已有用例。

本次是一次可避免的命令错误，已在对用户的进度更新及审查报告中说明。后续复用节点选择命令时应同一行传参数或正确使用续行；不为记录整齐重跑已通过的全套测试。

## Compose 结构检查

首次 `docker compose --env-file .env.example config --quiet` exit 1：项目 `env_file` 引用的 `.env` 未传输（按规范排除），不是配置语法失败。仅在任务隔离副本执行 `cp .env.example .env` 建立临时示例环境后，同一结构检查 exit 0。未输出插值后的配置、未使用真实密钥、未启动生产服务、未构建镜像。
