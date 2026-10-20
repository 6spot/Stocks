# V0-07 报告证据读取 API 与 V0 真实闭环验收

## 目标与用户价值

Owner 通过本机 API 阅读成功报告并打开报告实际使用的片段和原件。

## 背景与依据

阶段：V0。归属契约：`docs/interface.md`, `docs/research.md`, `docs/validation.md`, `docs/quickstart.md`。代码现状见[基线](../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

[V0-02](../archive/2026-10/10-20-v0-02-real-source-collection/prd.md), [V0-06](../archive/2026-10/10-20-v0-06-evidence-analysis-report/prd.md), [V0-08](../archive/2026-10/10-20-v0-08-document-collection-fixes/prd.md)

V0-08 是归档审查后追加的修复任务，必须先完成版本与采集边界回归，再开展本任务最终闭环验收；编号不改变这一前置关系。

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- 提供报告列表/详情、实际引用片段及对应版本原件的读取接口，包含来源、发布时间/采集时间、版本及覆盖缺口。
- V0 选择文档已允许的 API 阅读路径，不为阶段验收先建设 React 控制台。
- HTML 原件按安全文本或附件提供，不执行脚本；仅本机绑定，未认证入口不跨设备开放。
- 串联真实来源手动采集、入库、解析、索引、研究和阅读步骤；quickstart 只记录实际可运行命令和限制。

## 验收条件

- [ ] AC-1: Owner 按 quickstart 完成一次真实端到端操作，读取报告并打开实际引用的原件。
      **未验收**：测试服务器与本工作区均无模型 Provider 凭据（`MODEL_API_KEY`/`MODEL_NAME` 与可用 `/embeddings`），
      无法跑真实 Embedding 索引与真实 Research Agent；SEC Feed 已实测可达（`HTTP 200`）。
      已完成的替代证据：真实 `store_snapshot` 写入真实 S3 并按引用读回、真实 uvicorn HTTP 全链路 smoke。
- [x] AC-2: 同 URL 更新产生新版本后，旧报告仍显示并读取旧片段/原件；研究失败不替换最后成功报告。
      由 `tests/test_report_api.py` 两个用例在真实 PostgreSQL 上验证，并已在全新库 `alembic upgrade head` + `check` 下通过。
- [x] AC-3: 恶意 HTML 不执行脚本；部署仍只绑定本机；记录真实外部依赖和未验证项。
      原件以存储字节原样返回且 HTML 降级为 `text/plain` + `nosniff` + `Content-Security-Policy: default-src 'none'`；
      smoke 服务仅 `127.0.0.1` 监听；外部依赖与限制见[验证记录](research/verification-2026-10-20.md)。

## 不在范围内

完整 Web 控制台、认证后的跨设备部署、计划任务、增量与 Fact。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。

本轮已实施只读报告/证据 API（`backend/src/stocks/api.py`）、对应测试（`backend/tests/test_report_api.py`）
及 quickstart 阅读能力说明；未新增表、字段或迁移。实现结果与未验收边界见
[验证记录](research/verification-2026-10-20.md)，工程契约已沉淀至
`.trellis/spec/backend/api-guidelines.md`。AC-1 因缺模型凭据保持未验收，不得写成已接通。
