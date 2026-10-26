# Web 控制台移动端适配（纯 CSS）

Owner 反馈：当前控制台「没有手机端兼容」。核查后结论是**外壳已适配、表格靠滚动**——
`frontend/index.html` 有 `width=device-width, initial-scale=1`，`app-shell.tsx` 在 `lg`
以下隐藏侧栏并给出顶栏汉堡 + 导航对话框，`app-shell.tsx:235` 的 `min-w-0 flex-1`
保证内容不会撑破视口，`ui/table.tsx:5-8` 的 `<Table>` 自带
`relative w-full overflow-x-auto` 容器。因此表格在手机上**本来就能横向滚动**，
桌面并排面板也都写成 `sm:grid-cols-*` 起始，窄屏本就是单列。

真正缺的只有两类纯 CSS 事项：**写操作在手机上仍然可点**（半屏弹窗里误触风险），
以及**触屏命中区**（`[E1]` 引用按钮、`<summary>` 折叠标题偏小）。本任务只做这两类，
不引入任何 JS 断点判断、卡片化或列折叠。

## 目标

在 390×844 与 360×800 下，页面无整页横向滚动、表格容器自身可横向滚动读完所有列，
写操作按钮在手机上直接不出现，触屏可点元素命中区 ≥44×44。桌面宽度行为不变。

## 需求

### 1. 表格：保持原列，容器内横向滚动

- **不做卡片化、不做列折叠、不做列隐藏。** 表格结构、列数、字段与桌面完全一致。
- 手机宽度下 `<Table>` 容器自身横向滚动（`scrollWidth > clientWidth`，且可改变
  `scrollLeft`），一屏显示不下时由用户横向滑动读取剩余列。
- 页面级不得横向滚动：`document.documentElement.scrollWidth <= viewport width`。
  横向滚动只允许发生在 `<Table>` 的 `overflow-x-auto` 容器内部。
- 现有 `whitespace-nowrap` 表头保留——它正是让列不被挤压、触发容器滚动的机制。

### 2. 写操作在手机上隐藏

- 手动触发任务、重试/继续/结束 Run、处理积压重试：`sm` 以下**不渲染为可见元素**
  （`display: none`）。不加提示文案——单人自用，Owner 已知桌面才有写入口。
- 隐藏用纯 CSS 类（`hidden sm:flex` / `sm:hidden`），不用 `pointer-events-none`
  假禁用，也不用 JS 读断点。
- `sm` 及以上完全保持现有按钮、`disabled` 条件、加载态与文案，服务端能力字段仍是唯一
  权威判断。
- 元素仍留在 DOM 中（`display:none`），只是不渲染、不进入可见无障碍树；这是 Owner
  明确选择的取舍：宁可手机上完全不可达，也不出现半屏可点却做不完的操作。
- 「操作」列的表头与单元格一并隐藏，避免手机上留下空白列。

### 3. 单列回退：只需验收，不需要改

- 报告对照、Run 详情信息网格、用量摘要、证据字段网格均已从 `sm:grid-cols-*` 起，
  窄屏本身就是单列，**不改**。
- 仅两处固定两列需在验收中确认可读：`overview-page.tsx:87` 的
  `grid-cols-2` 存量数字条、`report-comparison.tsx:374` 的 `grid grid-cols-2` 侧栏。
  390px 下每列约 170px 且内容为短数值，预期可读；只有实测出现挤压才改
  `grid-cols-1 sm:grid-cols-2`。

### 4. 触屏命中区

- 报告正文 `[E1]` 引用按钮（`research-article.tsx:68`）：窄屏命中区 ≥44×44，
  保持原有 `aria-label`、焦点环与关闭后焦点回位。
- 9 处 `<summary>` 折叠标题：窄屏命中区 ≥44×44，视觉字号不变，只扩内边距/最小高度。
- 抽屉底部内容不被系统安全区遮挡（`env(safe-area-inset-bottom)`）。
- 已合格的保持不动：`Button` 的 `lg` 尺寸为 `h-10`；`Sheet` 已 `w-full overflow-y-auto`；
  `Dialog` 已 `w-[calc(100%-2rem)] max-h-[calc(100dvh-2rem)]`。

### 5. 实现约束

- **零 JS**：不新增 hook、不读 `matchMedia`、不按断点切换渲染结构。
- **零新依赖**、零新文件（除非验收发现必须共享的样式）。
- 只增删 Tailwind 类，不改组件 API、不改数据结构、不改路由。

## 非目标

- 不做表格卡片化、列折叠、列隐藏或响应式表格引擎。
- 不做 JS 断点渲染或 `useMediaQuery`。
- 不引入新依赖，不加 Playwright、TanStack Table、Recharts。
- 不改 API、schema、证据语义或任何服务端契约。
- 不做 PWA、离线、原生 App、手势导航。
- 不重做视觉风格；沿用 Nova 风格与现有设计变量。
- 不新增自动化测试。

## 验收标准

- [x] 390×844 与 360×800 下九个页面 `document.documentElement.scrollWidth` 不超过视口
      宽度（概览、情报、采集源、任务、研究、报告详情、运行、处理积压、登录）。
- [x] 每处表格容器在手机宽度可横向滚动，最右列可滚动到可见（逐表记录）。
- [x] 手机宽度下写操作按钮与「操作」列不可见，不新增任何提示文案；`sm` 以上按钮、
      禁用条件与文案与改动前一致。
- [x] `[E1]` 引用按钮与 9 处 `<summary>` 在手机宽度命中区 ≥44×44；
      引用弹窗键盘可开、关闭后焦点回到触发按钮。
- [x] `npm run typecheck` / `npm run test` / `npm run build` 在测试服务器通过，
      现有 vitest 全部通过且未新增用例。
- [x] 桌面 1280×800 抽查概览、任务、研究详情、运行详情：表格外观与写操作行为未变。
- [x] 浏览器验收记录写入 `acceptance.md`：真实 viewport、逐页 `scrollWidth`、
      逐表 `scrollWidth/clientWidth`、命中区测量、控制台错误、fixture 位置。

## 约束

- 所有验证只在测试服务器 `100.120.14.84` 的当前任务隔离目录执行，本机仅编辑与打包。
- 若改动导致现有用例失败，修实现而不是放宽断言。
- 遵循 [前端组件规范](../../spec/frontend/component-guidelines.md) 与
  [质量与验证](../../spec/frontend/quality-guidelines.md)。
