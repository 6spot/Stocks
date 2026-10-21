# 前端开发规范

前端选型以 [docs/architecture.md](../../../docs/architecture.md#技术与部署) 为准：React、TypeScript、Vite；UI 使用 shadcn/ui 的 Nova 风格、Base UI 与 Tailwind CSS，图标使用 Lucide，动画使用 Motion，客户端共享状态使用 Zustand。出现实际拖拽或可调整面板需求时，统一使用 dnd-kit 与 react-resizable-panels。此目录只规定工程实现方式，不定义页面内容或产品流程。

当前 Web 客户端位于 `frontend/`，使用 npm 与锁文件安装，要求 Node.js
20.19 或更高版本。真实命令如下：

```bash
cd frontend
npm ci --ignore-scripts
npm run typecheck
npm run test
npm run build
```

Vitest 使用 jsdom 和 Testing Library 覆盖 API 会话边界、报告证据阅读、停止确认、
Run 恢复和 Processing 重试。生产构建使用 Vite；页面级路由按需加载。lint 尚未
单独配置，`typecheck` 与构建共同检查当前代码。浏览器验收可使用测试服务器
Chromium，但浏览器驱动和截图 fixture 不进入应用依赖。

| 主题 | 适用范围 |
| --- | --- |
| [目录与模块](directory-structure.md) | 页面、功能组件、API 边界与测试位置 |
| [组件与样式](component-guidelines.md) | Nova 风格、设计变量、Lucide、Motion、面板与拖拽 |
| [Hook](hook-guidelines.md) | 自定义 hook 的职责、上下文与数据边界 |
| [状态管理](state-management.md) | 组件本地状态、Zustand 共享状态、服务端权威数据 |
| [类型安全](type-safety.md) | API 类型、运行时边界、时间字段 |
| [质量与验证](quality-guidelines.md) | 可访问性、检查和测试选择 |

## 工程落地门槛

新增依赖时先读取 `docs/interface.md`、`docs/architecture.md` 和实际
`frontend/package.json`。按架构中的统一选型实现，不引入承担相同职责的第二套 UI、
图标、动画、面板、拖拽或全局状态库，也不引入全文搜索前端。当前页面不需要
react-resizable-panels、dnd-kit、TanStack Table 或 Recharts；真实需求出现前不安装
这些库。依赖版本以 `package-lock.json` 为准，升级后重跑类型检查、行为测试和生产
构建。

V1-08 已实现从 API 读取解析状态和可定位 Chunk 的页面。界面必须把
`Document.content_type`、`Document.completeness`、解析最新状态
`latest_batch_id`、当前成功批次 `current_batch_id` 和 Chunk 批次分开呈现；不得把
“已采集”“解析失败”“解析中/未解析”“当前有可读 Chunk”折叠成一个状态。
Chunk 详情需显示来源、Document 版本、批次、原始定位和当时的 `as_of` 或处理时间。
HTML 的 `source_ranges` 必须按源码中的原始实体范围解释，不能直接使用元素级
`char_start` / `char_end` 当作实体解码后的可见字符范围；表格 `separators` 是系统
生成的排版位置，需与原文单元格边界分开解释。原件 HTML 应安全显示为源码或提供
下载，不能注入页面的 HTML DOM。

## Quality Check

按[质量与验证](quality-guidelines.md)选择与修改范围相称的检查。组件行为、类型边界和样式问题分别验证，不把全量构建或全量测试作为每次编辑的固定步骤。
