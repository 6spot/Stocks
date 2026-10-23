# 执行计划

用户授权：创建任务并逐个处理三个复审问题。

- [x] 子任务 1：补证 Fact 阶段推进，运行补证回归与相关研究测试。
- [x] 子任务 2：SSE 增量读取，运行 Tool 回归及受控流边界用例。
- [x] 子任务 3：报告页初始选择，运行 Research 组件回归、TypeScript 检查及构建。
- [x] 整体审查：核对作用域、文档、测试结果与未验证项，记录并提交交付。

测试目录：100.120.14.84:/tmp/stocks-review-fixes-20261023。复用现有运行时和 PostgreSQL 容器，使用专用 stocks_review_fixes 数据库；不对 stocks_both 或 stocks_v2_acceptance 运行清表测试。各子任务验收后再进入下一项。

## 整体审查结论

- 产品代码改动仅三项：`backend/src/stocks/research/supplement.py`、`backend/src/stocks/tools/client.py`、`frontend/src/features/research/research-page.tsx`，外加各自测试与一处归属文档。未新增依赖、迁移、Alembic 修订、配置项或兼容层。
- 三份归属文档描述与实现一致：`docs/research.md`（复用已完成阶段后继续、解析不可用止步、`NOT_APPLICABLE` 不记缺口）、`docs/execution.md`（SSE 完整响应即返回及限额）、`docs/interface.md`（初始选择语义）。
- 阶段所有权、MCP 授权/协议/资源上限、报告证据读取语义均未改变。
- 审查提出的两点已处理：补一条走真实入库/领取路径的复用回归（已证基线失败）；文档补充解析不可用止步与空 `report` 参数按未指定处理。
- 未运行：全量回归、真实外部 MCP 服务、真实浏览器视口、物理设备/部署验收。
