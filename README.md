# Stocks

一个面向个人美股研究的 AI 投资情报系统：采集多来源信息，沉淀可检索、可追溯的情报，通过可配置 Task 和 Agent 完成研究与报告，为人工投资决策提供参考。

## 文档

- [产品与系统设计](docs/architecture.md)：项目的设计基线；包含架构、Source/Connector/Task/Agent/Tool 边界、数据契约、可信度、存储、技术选型和部署。
- [本地运行说明](docs/quickstart.md)：环境准备、Docker Compose、迁移与命令入口。

## 核心约定

Task 可以只执行采集，也可以组合可选的 Agent、工具和分析步骤。投资策略是 Task 的可选属性，不限制底层执行能力。模型通过可配置 HTTP Provider 访问，不依赖某个特定服务商或部署方式。

设计调整直接修改 [docs/architecture.md](docs/architecture.md) 的相应章节。
