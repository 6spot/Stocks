# 执行计划（纯 CSS）

全部改动为 Tailwind 类增删，零 JS、零新文件、零新依赖。顺序无关紧要，但按下面推进便于
分步 typecheck。每步之后在远程测试服务器跑 `npm run typecheck`。

## 1. 写操作：任务页手动触发

`frontend/src/features/tasks/tasks-page.tsx`

- 表头 `<TableHead className="text-right">操作</TableHead>` →
  `className="hidden text-right sm:table-cell"`。
- 对应 `<TableCell className="text-right">` → `className="hidden text-right sm:table-cell"`。
- 触发按钮加 `hidden sm:inline-flex`（`Button` 基础类是 `inline-flex`，用 `sm:inline-flex`
  在桌面恢复同一布局，不要写 `sm:flex` 以免改变内部图标对齐）。
- **不加提示文案**，直接隐藏。
- `disabled` 条件、加载文案、`trigger()` 调用一律不改。

## 2. 写操作：Run 恢复动作

`frontend/src/features/runs/run-actions.tsx`

- 三个按钮的容器 `<div className="flex flex-wrap gap-2">` →
  `<div className="hidden flex-wrap gap-2 sm:flex">`。
- **不加提示文案**。
- 提前返回分支（无可用动作时显示「当前 Run 状态没有可用的恢复动作。」）**不改**——
  它是状态说明，不是写操作，手机上也应可见。
- `ConfirmStopDialog` 与其 `open` 状态不改：桌面路径仍需要它。

## 3. 写操作：处理积压单行重试

`frontend/src/features/runs/processing-panel.tsx`

- 表头 `<TableHead className="text-right">操作</TableHead>` 加
  `hidden ... sm:table-cell`。
- 行内 `<TableCell className="text-right">` 同步。
- 重试按钮加 `hidden sm:inline-flex`，不加提示文案。
- `disabled` 条件（`pendingDocument`、`completed`/`skipped`、`processor_available`）不改。

## 4. 触屏命中区：证据引用按钮

`frontend/src/features/research/research-article.tsx:68`

- `DialogTrigger` 的 `className` 由
  `cursor-pointer rounded-sm px-1 text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`
  改为开头插入
  `inline-flex min-h-11 min-w-11 items-center justify-center sm:inline sm:min-h-0 sm:min-w-0`。
- 其余类（颜色、下划线、焦点环、`aria-label`）保持不动。
- 桌面必须回到行内紧凑角标——`sm:min-h-0 sm:min-w-0` 抵消窄屏的 44px。
  若 `sm:inline` 与 `items-center` 在桌面产生多余行高，改用 `sm:block` 并复查打印度。

## 5. 触屏命中区：9 处折叠标题

给以下 `<summary>` 的 `className` 追加 `py-2.5 min-h-11`（**不要加 `flex`**）。
`<summary>` 默认 `display: list-item`，折叠三角 `::marker` 只在 `list-item` 下渲染，
改 `flex` 会静默去掉「可展开」的视觉提示。已有 `w-fit` 的保留在最前：

- `src/features/research/research-page.tsx:393`（`w-fit` + `text-sm`）
- `src/features/research/research-page.tsx:476`
- `src/features/research/research-page.tsx:647`
- `src/features/research/research-article.tsx:118`（`w-fit` + `text-xs`）
- `src/features/tasks/tasks-page.tsx:97`
- `src/features/intelligence/intelligence-page.tsx:216`
- `src/features/runs/run-detail-view.tsx:243`
- `src/components/fact-list.tsx:90`
- `src/components/document-details.tsx:26`

`text-xs` 的两处（`research-article.tsx:118`、`research-page.tsx:393`）若 `py-2.5`
实测仍不足 44px，加到 `py-3`。验收时逐处确认折叠三角仍可见。

## 6. 抽屉底部安全区

`frontend/src/components/ui/sheet.tsx:34`

- `p-5` → `p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))]`。
- 其余定位、`max-w-[44rem]`、过渡类不改。
- `SheetHeader` 已有 `pr-8`，不动。

## 7. 验收中的条件改动（可能为零）

只有实测不达标才动，否则跳过：

- `overview-page.tsx:87` 的 `grid grid-cols-2 ... lg:grid-cols-4`：若 390px 下数字被挤压，
  改 `grid-cols-1 sm:grid-cols-2 lg:grid-cols-4`。
- `report-comparison.tsx:374` 的 `grid grid-cols-2 gap-1 text-xs`：同上改
  `grid-cols-1 sm:grid-cols-2`。
- 任何表格容器实测无法横向滚动时，先查祖先 `overflow-hidden`，不要直接改
  `ui/table.tsx`。

## 8. 验证与记录

远程测试服务器（`/tmp/stocks-mobile-adaptation-<run>` 隔离目录）：

```bash
cd frontend
npm ci --ignore-scripts
npm run typecheck
npm run test
npm run build
```

浏览器走查（390×844、360×800，桌面回归 1280×800）：

- 九个页面：概览、情报（含文档抽屉）、采集源、任务、研究列表、报告详情（含 `[E1]`
  弹窗）、对照视图、运行列表（含 Run 抽屉）、处理积压、登录。
- 每页记录 `document.documentElement.scrollWidth` vs `window.innerWidth`。
- 每处 `<Table>` 记录容器 `scrollWidth` / `clientWidth`，并把 `scrollLeft` 推到最大，
  确认最右列进入可视区。
- 写操作：确认按钮 `offsetParent === null`（DOM 在但不可见），手机宽度下不出现写操作文案。
- 命中区：`[E1]` 与 9 处 `<summary>` 的 `getBoundingClientRect()` 宽高 ≥44。
- 抽屉/弹窗：可滚动、可关闭、焦点回到触发元素。
- 控制台报错数。
- 结果写入 `.trellis/tasks/10-26-mobile-adaptation/acceptance.md`。

## 完成判据

- [ ] `typecheck` / `test` / `build` 远程全绿，且未新增测试文件或用例。
- [ ] 九个页面 × 2 个手机 viewport 无整页横向滚动。
- [ ] 每处表格容器可横向滚动到最右列。
- [ ] 写操作在手机不可见且无提示文案；桌面行为未变。
- [ ] `[E1]` 与 9 处 `<summary>` 命中区 ≥44×44。
- [ ] 零 JS、零新依赖、零新文件（第 7 步条件改动除外）。
- [ ] `acceptance.md` 含真实 viewport、逐页/逐表数值、命中区测量、控制台错误。
