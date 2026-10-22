# F-02 技术设计

## 当前调用链

retry_run() 复制 EvidencePackage 与 research_inputs；_agent_evidence() 合并引用后调用 validate_selected_versions()。当前查询将 DocumentParseState.current_batch_id == parse_batch_id 作为保存引用可读的条件，混淆了“新取材当前批次”和“历史引用身份存在”。

## 建议方案

在冻结引用验证中按 Document、document_version、Chunk、parse_batch_id 的真实关联读取历史记录；必要时核对该批次归属和成功状态。去除仅为 current 才可读取的限制，但不移除其它身份、内容和授权约束。

新取材的 current 批次筛选保留在 Repository/Evidence Selection 边界。应检查 validate_selected_versions() 的所有调用者，避免将针对新取材的检查意外移入共享历史读取函数。

补证仍只能从本 Run 合并台账形成允许集。旧批次缺失不回退当前批次；重试复制过程不能更新原包的 ID、文本或引用。

## 实施落点（2026-10-22）

已按本方案把 `validate_selected_versions()` 的批次条件从“必须为当前批次”改为“属于该 Document 且成功完成、内容哈希与 Document 版本一致”；Repository 的当前批次筛选未改动，新取材仍只读当前成功批次。历史报告与重试读取复用同一函数，无需新增 API。

测试已编写但未运行：`test_research.py` 覆盖历史批次可读、未完成批次拒绝、跨 Document 拒绝；`test_execution.py` 覆盖失败 Run → 批次 B → 重试仍读 A，以及追加台账输入。远程认证受阻，任务保持未验收，详见 [验收记录](acceptance.md)。

## 关键文件与集成

- backend/src/stocks/research/service.py：validate_selected_versions、合并引用。
- backend/src/stocks/execution/service.py：retry_run、_agent_evidence。
- backend/src/stocks/repository.py、backend/src/stocks/parsing/service.py：按需要核对批次选择与保留，不预设必须修改。
- backend/tests/test_execution.py、test_research.py、test_research_supplement.py。

与 F-03 共享函数，建议顺序合并；若并行使用隔离分支并审查合并后的字段投影和允许集。预期不涉及数据库结构。
