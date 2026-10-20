# V0-07 实施计划

## 开工条件

- [ ] 复核 PRD 与最新领域契约；前置任务：[V0-02](../archive/2026-10/10-20-v0-02-real-source-collection/prd.md), [V0-06](../10-20-v0-06-evidence-analysis-report/prd.md), [V0-08](../10-20-v0-08-document-collection-fixes/prd.md)
- [ ] 重新检查现有代码及测试，落实建议路径、具体 Schema 和必要技术决策。
- [ ] 用户确认本子任务的最终实施范围；之后才运行 task.py start。当前未授权实施。
- [ ] implement.jsonl / check.jsonl 已提供真实规范与基线，派发前按实际改动裁剪复核。

## 执行顺序

- [ ] 补齐只读报告/证据 API 及安全原件响应。
- [ ] 更新本地 Compose/CI 的必要检查及 quickstart。
- [ ] 执行 V0 全链路与版本/失败演示，保存验收记录并汇总阶段退出条件。

## 最小必要验证

- cd backend && python -m pytest -q tests/test_report_api.py（新增后）
- docker compose config --quiet；仅部署有变动时构建并进行 API smoke。
- 执行一次真实采集 + pgvector 检索 + 真实模型研究 + 旧证据读取；外部服务不可用时明确阶段未验收。

新增测试名为计划，文件存在后才执行；不得把本文件命令写成已通过。按风险验证关键行为，不默认全量测试，纯文档变更不新增代码测试。

## 收尾

- [ ] 逐项记录 PRD AC 的实际结果、未完成和外部限制。
- [ ] 契约变更只更新唯一归属文档，运行能力更新 quickstart。
- [ ] 检查后续依赖与阶段验收；保留原始证据、输入快照及最后成功结果。
- [ ] 完成实现/质量检查后按 Trellis 工作流交付，不在本轮提前归档或标完成。
