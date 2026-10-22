# F-02 实施计划

- [ ] 取得本叶子实施指令；读取父任务与当前设计、执行边界，复核后 task.py start 本任务。
- [ ] 在远程隔离环境构造 A → 失败 Run → 重新解析 B → retry，先证明当前实现拒绝有效旧引用。
- [ ] 核对所有 validate_selected_versions 调用者，将历史身份校验与新取材 current 条件分离。
- [ ] 覆盖初始包和追加输入重试；验证伪造身份、错配版本和真实缺失批次仍拒绝。
- [ ] 验证新建 Run 继续使用 B，重试不会改写原包/台账。
- [ ] 按实际变化同步 docs/research.md 或 docs/execution.md 的唯一归属说明和必要 spec。

## 最小验证

所有测试在 100.120.14.84 的本任务隔离目录执行，使用 testing-guidelines.md 规定的环境。本机不运行测试/构建。

在 tests/test_execution.py 增加或扩展 retry 生命周期回归；tests/test_research.py 覆盖历史批次合法及错配引用；tests/test_research_supplement.py 选择追加输入历史引用的必要用例。使用 python -m pytest -q 对新增的具体用例或 -k 筛选执行，并记录实际选中数量；不以零选中或跳过表示通过，不默认全量回归。

## 收尾

- [ ] AC-1 至 AC-5 有实际证据和限制。
- [ ] 与 F-03 的共享修改合并后核对输入允许集和冻结字段；父任务 I-2 仍需 F-01/F-04 的集成场景。
- [ ] 质量检查、文档与记录同步，按授权交付。当前仅为计划，没有运行结果。
