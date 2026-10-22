# F-01 实施计划

## 开工

- [x] 取得本叶子实施指令；阅读父任务、AGENTS.md、执行边界及 docs/data.md，复核现状后 task.py start 本任务。
- [ ] 在测试服务器当前任务隔离目录建立历史报告引用 F 的回归场景，确认修复前旧 ID 失效的运行结果。当前仅有静态缺陷链路证据；远程认证受阻，未执行基线复现。

## 实施顺序

- [x] 核对两条 facts 路径、fact_key、变更水位和阶段所有权检查。
- [x] 移除破坏性清空，复用同身份事实并保留已存原值；删除无用途的旧替换函数与注释。
- [x] 更新原先预期清空/替换的测试；已编写相同事实、未再次产出事实、新位置事实、同身份不同候选值回归，保留已有新 Document 版本独立性覆盖。测试尚未执行。
- [x] 保留原事务和所有权检查；补充新增 Fact/事件/水位在阶段完成前失败时回滚的用例，同步 docs/data.md 与数据库 spec。运行验证待完成。

## 最小验证

所有操作只在 100.120.14.84 当前任务隔离目录执行，认证、持久环境与数据库遵循 .trellis/spec/backend/testing-guidelines.md。本机只读写与打包。

在远程 backend 工作目录按测试环境规范配置 TEST_DATABASE_URL，使用任务环境的 Python 执行：

```bash
.venv/bin/python -m pytest -q tests/test_facts.py
.venv/bin/python -m pytest -q tests/test_report_comparison.py -k test_reprocessing_keeps_a_report_fact_readable_and_its_comparison_unchanged
```

历史报告场景复用 test_report_comparison.py 的真实持久化夹具，核对旧 Fact 详情、原文、Analysis 引用、EvidencePackage 和既有报告比较。仅在这些测试出现相关失败或留下水位回归疑点时，追加 test_change_watermarks.py 的事实事件用例，不默认全量测试。新增用例通过真实数据库和阶段流程验证行为，不只断言某个删除函数未被调用。

若结构变化，验证全新空库 alembic upgrade head 和 alembic check；没有结构变化时无需机械重建数据库。无法远程执行则保持未验证。

## 收尾

- [ ] PRD AC-1 至 AC-5 有实际结果、命令及限制。
- [ ] F-04 获得可依赖的 Fact 保留契约；父任务 I-2 尚需与 F-02/F-04 联合验收。
- [ ] 质量检查、归属文档与任务记录同步，按授权交付；本计划命令不代表已通过。

## 本轮交回（2026-10-22，阻塞返回）

`f01-start` 与 `f01-implement` 已完成，`f01-verify` 保持进行中。代码、测试及归属文档已更新，人工静态复核未发现本次改动引入的额外偏差；这不等同于执行检查通过。详细证据与 AC 状态见 [acceptance.md](acceptance.md)。

SSH 免交互认证返回 `Permission denied (publickey,password)`，exit 255。此结果只说明当前认证不可用，不证明服务器不可达；本会话没有可用密码，未搜索其他会话或文件中的凭据。没有执行测试、编译、类型、格式或数据库检查，也没有回退本机。未创建远程目录、容器或后台进程，无本轮新增远程资源待清理。

下一步是取得本会话可用的测试服务器 SSH 认证，按既有测试规范同步当前任务并运行上述最小范围测试，修复实际失败后再完成验收。任务保持 `in_progress`，未提交、未归档，也未推进 F-02 至 F-05。
