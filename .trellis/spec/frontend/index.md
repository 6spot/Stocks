# 前端开发规范

前端选型以 `docs/architecture.md` 为准：React、TypeScript、Vite；UI 原语采用 shadcn/ui 与 Base UI，样式使用 Tailwind CSS。此目录只规定工程实现方式，不定义页面内容或产品流程。

当前仓库尚未建立 `frontend/` 或 `frontend/package.json`，因此没有可执行的前端安装、lint、类型检查、测试或构建命令，也没有已确认的目录布局、测试框架或浏览器验收工具。以下规范适用于 V1-08 建立前端工程时；在该任务落地前不得声称前端已实现或相关检查已通过。

| 主题 | 适用范围 |
| --- | --- |
| [目录与模块](directory-structure.md) | 页面、功能组件、API 边界与测试位置 |
| [组件与样式](component-guidelines.md) | 组件拆分、Props、交互原语 |
| [Hook](hook-guidelines.md) | 自定义 hook 的职责、上下文与数据边界 |
| [状态管理](state-management.md) | 本地/共享状态、服务端权威数据 |
| [类型安全](type-safety.md) | API 类型、运行时边界、时间字段 |
| [质量与验证](quality-guidelines.md) | 可访问性、检查和测试选择 |

## 工程落地门槛

初始化前端时，先读取 `docs/interface.md`、`docs/architecture.md` 和 V1-08 任务，再按实际创建的 `package.json` 记录包管理器、Node 版本、目录、脚本与测试框架。选型保持 React + TypeScript + Vite、shadcn/ui + Base UI、Tailwind CSS；除非领域文档先改变契约，不引入第二套 UI 组件库或全文搜索前端。

## Quality Check

按[质量与验证](quality-guidelines.md)选择与修改范围相称的检查。组件行为、类型边界和样式问题分别验证，不把全量构建或全量测试作为每次编辑的固定步骤。
