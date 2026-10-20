# 规划依据与代码现状（2026-10-20）

## 证据

- `docs/architecture.md:7` 与 `docs/validation.md:5` / `:16` / `:28` 已明确定义 V0/V1/V2。本轮不重定义版本含义。
- `docs/quickstart.md` 明确当前无采集命令；原 RSS collector 因将摘要和整 Feed 快照当文章证据而删除。不能恢复旧路径充当 V0。
- `backend/src/stocks/models.py:20` 有 Document，但 `:23` 的 `(source_id, dedupe_key)` 唯一约束不能表达一个资料的多个内容版本，V0-01 直接修改当前模型和初始化定义。
- `backend/src/stocks/models.py:41` 有 Chunk 与 `:48` 的 Vector 字段，但无实际解析、定位、Embedding 生成和 pgvector 查询；不能据此宣称检索已实现。
- `backend/src/stocks/s3.py:6` 只提供保存指定原始字节及 SHA-256 的 helper；尚无统一 Repository 入库、版本或证据读取闭环。
- `backend/src/stocks/providers.py:41` 只实现兼容 HTTP chat client 和有序 fallback；没有 Embedding、Decision 专有协议、Agent runner 或调用持久记录。
- `backend/src/stocks/api.py:8` / `:12` 只有 health 和文档列表，未认证；`compose.yaml` 仅提供 PostgreSQL 与本机绑定 API。
- 无 `frontend/`；前端选型已由 `docs/architecture.md:124` 确认。当前仅 `test_s3.py` 与 `test_providers.py`，不可声称采集/研究等已有测试覆盖。
- 已归档任务仅 Bootstrap Guidelines，不存在可复用的业务交付任务树。

## 规划决策

- V0 阅读入口采用设计允许的本机 API，先不建设 React 界面；pgvector、真实原件、版本与引用底线不后移。
- V0 手动运行各处理步骤；V1 接入独立持久 Worker。V0/1 只研究已存 Repository，V2 才按需访问外部补证。
- V1-05 单独负责提交可见水位及所有写入点接入，避免以自增 ID 或采集时间制造不漏数假象。
- V1 已实现基本同源去重和数值冲突保留；V2-03 增强研究解释与呈现，不能将底线推迟到 V2。
- 仅建到现有文档明确的 V2。证券身份主数据、按证券 Fan-out、全文检索、自动交易、复杂 DAG 和多 Agent 平台不属于后续候选。

## 使用限制

上述为本轮读到的实现状态。各子任务启动前重新核对已合入前置结果；新文件名、测试名和接口组织是建议落点，不是已实现能力。来源名单、Decision/Tool 协议及认证细节在对应任务开工前细化；不阻塞本轮版本拆分，也不代表批准外部服务费用或部署发布。

仓库原有 AGENTS.md、docs/ 及 Trellis 配置等未提交改动保持原样。本轮仅创建本路线图和其新子任务，不改产品代码。
