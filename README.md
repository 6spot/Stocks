# Stocks

一个面向个人美股研究的 AI 投资情报系统：采集多来源信息，沉淀可检索、可追溯的情报，通过可配置 Task 和 Agent 完成研究与报告，为人工投资决策提供参考。

## 文档

- [系统架构与设计入口](docs/architecture.md)：产品原则、模块职责、系统链路与文档导航。
- [数据](docs/data.md)：Source、采集、分类、Repository、金融数据与检索。
- [执行](docs/execution.md)：Task/Run、调度、Agent/模型与 Tool。
- [研究](docs/research.md)：Evidence Selection、Research Agent、Analysis/Report、证据追溯。
- [界面](docs/interface.md)：Web 页面、研究报告与证据交互。
- [验收](docs/validation.md)：原型边界、端到端验收场景。
- [本地运行说明](docs/quickstart.md)：环境准备、Docker Compose、全新数据库初始化与命令入口。

## 核心约定

Task 可以只执行采集，也可以组合可选的 Agent、工具和分析步骤。投资策略是 Task 的可选属性，不限制底层执行能力。模型通过可配置 HTTP Provider 访问，不依赖某个特定服务商或部署方式。

设计调整请修改对应领域文档；只有涉及整体模块边界或系统原则时，才同步修改 [架构总览](docs/architecture.md)。
