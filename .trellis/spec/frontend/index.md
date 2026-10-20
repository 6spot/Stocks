# 前端开发规范

前端选型以 `docs/architecture.md` 为准：React、TypeScript、Vite；UI 原语采用 shadcn/ui 与 Base UI，样式使用 Tailwind CSS。此目录只规定工程实现方式，不定义页面内容或产品流程。

| 主题 | 适用范围 |
| --- | --- |
| [组件与样式](component-guidelines.md) | 组件拆分、Props、交互原语 |
| [类型与状态](type-safety.md) | API 类型、运行时边界、本地状态 |
| [质量与验证](quality-guidelines.md) | 可访问性、检查和测试选择 |

## Quality Check

按[质量与验证](quality-guidelines.md)选择与修改范围相称的检查。组件行为、类型边界和样式问题分别验证，不把全量构建或全量测试作为每次编辑的固定步骤。
