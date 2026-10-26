# 较早的提交方案（已被合并方案取代）

本文件保留前一任务当时的拆分草案；实际待确认方案现归 [taxonomy-research-flow/commit-plan.md](../10-27-taxonomy-research-flow/commit-plan.md)。后续候选语义任务连续修改了相同源码文件，不能再用这里的旧文件分组代表当前最终状态。

状态：实施、远程受控验证、独立复核、报告与测试资源清理已完成。尚未提交，未部署或推送。按 `.trellis/workflow.md` Phase 3.4 第 5 步先展示文件分组，再取得一次提交确认。

## 1. `feat(classification): integrate native Jev with explicit entity extraction`

本次官方原生接入、独立环境配置、必要测试、归属文档与工程规范，一起形成可审查的功能变更：

- `.env.example`
- `compose.yaml`
- `config/models.yaml`
- `config/taxonomy.yaml`
- `backend/src/stocks/providers.py`
- `backend/src/stocks/decision.py`
- `backend/src/stocks/classification/service.py`
- `backend/src/stocks/classification/jev.py`
- `backend/tests/test_classification_jev.py`
- `docs/data.md`
- `docs/execution.md`
- `docs/quickstart.md`
- `.trellis/spec/backend/error-handling.md`
- `.trellis/spec/backend/testing-guidelines.md`

## 2. `docs(audit): record manual pipeline findings and controlled verification`

- `.trellis/tasks/10-26-application-pipeline-audit/`：需求/设计/执行状态、审查结论、两个复现探针、受控完整 smoke、独立复核、脱敏运行证据、清理记录与本提交方案。

没有未识别或其他窗口的变更纳入分组。探针的两个失败表示尚未修复的已有问题，不把它们作为通过测试。

仅创建上述本地提交，不推送、不部署，不自动修复 MP-01/MP-02。任务暂留 `in_progress`，真实 Jev/来源联合验收与浏览器检查未完成，提交不等同真实闭环验收或归档。
