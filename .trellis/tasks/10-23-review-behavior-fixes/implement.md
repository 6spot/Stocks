# 执行计划

用户授权：创建任务并逐个处理三个复审问题。

- [ ] 子任务 1：补证 Fact 阶段推进，运行补证回归与相关研究测试。
- [ ] 子任务 2：SSE 增量读取，运行 Tool 回归及受控流边界用例。
- [ ] 子任务 3：报告页初始选择，运行 Research 组件回归、TypeScript 检查及构建。
- [ ] 整体审查：核对作用域、文档、测试结果与未验证项，记录并提交交付。

测试目录：100.120.14.84:/tmp/stocks-review-fixes-20261023。复用现有运行时和 PostgreSQL 容器，使用专用 stocks_review_fixes 数据库；不对 stocks_both 或 stocks_v2_acceptance 运行清表测试。各子任务验收后再进入下一项。
