# 已确认的具体提交方案

状态：用户已回复“行”确认完整分组及归档/日志收尾。功能提交 `77c86ff` 已完成；规范与验收记录随后独立提交。未推送、未部署。当前所有 dirty paths 均是本会话及已交接子 Agent 的已知变更；没有未识别的用户编辑。此方案取代前一任务较早的 commit-plan.md 草案。

## 分组原则

原生 Jev 接入属于 `10-26-application-pipeline-audit`；候选名称/定义/别名、完整指纹、NVIDIA 示例属于 `10-27-taxonomy-research-flow`。两项在 decision/classification/config/docs 等文件连续演进，最终源码采用一个一致的功能提交，避免把同一文件的最终状态错误地全部归为前一任务。两份任务验收和证据分别保存，不能合并为全部真实外部闭环。

## 1. feat(classification): add native Jev and semantic label candidates

产品实现、当前配置、必要测试与唯一归属文档：

- `.env.example`
- `compose.yaml`
- `backend/src/stocks/providers.py`
- `backend/src/stocks/decision.py`
- `backend/src/stocks/taxonomy.py`
- `backend/src/stocks/classification/jev.py`（新增）
- `backend/src/stocks/classification/service.py`
- `backend/tests/test_classification.py`
- `backend/tests/test_classification_jev.py`（新增）
- `backend/tests/test_execution.py`
- `config/models.yaml`
- `config/taxonomy.yaml`
- `config/tasks.yaml`
- `docs/data.md`
- `docs/execution.md`
- `docs/quickstart.md`

包含：原生协议/严格答案与概率校验、独立分类 Provider 配置、显式开放实体角色、共享候选语义、稳定完整指纹、手动股票研究示例与正确 API 检查流程。无数据库模型改动、迁移、兼容层或生产部署。

## 2. docs(audit): record manual pipeline and taxonomy validation

工程约定及两项独立任务的计划/审查/去敏验证记录：

- `.trellis/spec/backend/error-handling.md`
- `.trellis/spec/backend/quality-guidelines.md`
- `.trellis/spec/backend/testing-guidelines.md`
- `.trellis/tasks/10-26-application-pipeline-audit/`：PRD/design/implement、审查报告、脚本和该任务实际验证证据。
- `.trellis/tasks/10-27-taxonomy-research-flow/`：PRD/design/implement、独立复核、acceptance、三文档受控/真实 Jev 两次运行及清理证据。

证据中的配置只含固定受控占位 key；没有 `.env`、网关授权文件、上游密钥、数据库/Owner/会话私钥或本机配置文件。原始失败与 warning 保留，不把它们改成成功。

## 后续工作流

按用户确认依上述两组先完成 work commits；再按 finish-work 归档这两项任务并单独提交 archive，最后记录本次 journal 并单独提交。不 amend，不 push，不部署。原有 MP-01/MP-02 不在本次修复范围，未识别文件列表为空。

确认入口依据：`.trellis/workflow.md` Phase 3.4 第 5 步：**“Present the plan once, ask for one-shot confirmation”**。已获用户建任务与直接实施授权，因此实施、测试、修正与清理均已自主完成；具体提交方案的一次确认现已取得，不再重复请求。
