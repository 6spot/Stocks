# V0-03 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务 V0-01、V0-02 已完成归档。
- [x] 重新检查现有代码及测试，落实批次/状态/定位 Schema 与手动 CLI。
- [x] 用户明确要求按路线图继续；V0-03 已获实施授权。
- [x] implement.jsonl / check.jsonl 已提供真实规范、基线和技术决策。

## 执行顺序

- [x] 增加解析批次、最新/当前状态及 Chunk 批次/定位字段，同步模型与初始化结构。
- [x] 实现 MIME 选择、严格解码、HTML 语义提取、文本分块及稳定错误。
- [x] 实现 Repository 批次持久化、同指纹复用、强制重处理和当前 Chunk 读取。
- [x] 增加手动 CLI 与 `PARSE_CHUNK_SIZE` / `PARSE_CHUNK_OVERLAP` 配置。
- [x] 用固定 HTML/文本/失败响应验证定位、数字与单位、表格、幂等和旧 Chunk 保护。
- [x] 更新 `docs/data.md` 的实际契约、`docs/quickstart.md` 的命令与当前限制。
- [x] 在远程 Python 3.12 + 全新 pgvector 空库执行结构检查和相关测试。
- [ ] 用真实 SEC HTML 执行解析，核对数据库与批次/定位样例后记录脱敏验收；
  当前 SEC 联系人仍为示例邮箱，该项待外部配置完成。
- [x] 完成质量检查与规范沉淀；提交、Trellis 归档和会话记录在本次收尾执行。
- [x] 同步当前解析器定位字段、验收结果与真实来源限制；修正仍引用旧字段和
  旧成功结果的任务记录。
- [x] 将 `HTMLParser` 字符引用事件差异和基础 span 后扩展 overlap 的测试规则写入
  后端测试规范。
- [ ] 真实 SEC 验收后的提交门槛：先取得已有 Document 版本的可读原件，或由
  Owner 提供可信联系身份重新采集；不能用示例邮箱或零散临时 HTML 代替。

## 最小必要验证

- `cd backend && python -m pytest -q tests/test_processing.py`
- `cd backend && python -m pytest -q tests/test_repository.py`
- 全新 pgvector 空库：`alembic upgrade head`；随后 `alembic check`。
- 真实 SEC HTML：手动处理至少一份 V0-02 Document，核对状态、Chunk 数、源位置、
  包含数字/单位的正文样例和重复运行结果。

实施前以上是计划命令，不得写成已通过。按风险验证关键行为，不默认全量测试；
结构变更必须核对全新数据库，真实来源验收不能由 Mock 替代。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制；见 `acceptance.md`。
- [x] 契约变更更新唯一归属文档，运行能力更新 quickstart。
- [x] 检查后续依赖与阶段验收；保留原始证据、输入快照及最后成功结果。
- [x] 完成实现/质量检查后按 Trellis 工作流提交、归档并记录会话（据已归档任务、acceptance.md 与会话记录补齐；未执行的真实 SEC 验收仍保留上方未勾选项）。

## 上下文读取注意

`docs/data.md` 当前约 36 KB，超过默认单文件注入上限 32,768 字节。自动注入可能
截断末尾；开工时必须完整读取原文件，尤其解析、检索与来源安全契约。不要把已注入
前缀当成全文；无需为此修改 Trellis 全局配置。
