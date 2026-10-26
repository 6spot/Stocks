# 移动端适配技术设计（纯 CSS）

## 现状：大部分「缺口」其实已有可用行为

| 关注点 | 现状 | 结论 |
| --- | --- | --- |
| viewport meta | `index.html` 有 `width=device-width, initial-scale=1` | 已有 |
| 移动导航 | `app-shell.tsx:154,164,236,257` 侧栏 `lg` 以下隐藏 + sticky 顶栏 + 汉堡 + `Dialog` 导航 | 已有 |
| 内容不撑破视口 | `app-shell.tsx:235` `min-w-0 flex-1` | 已有 |
| 表格横向滚动 | `ui/table.tsx:5-8` 容器 `relative w-full overflow-x-auto`；`TableHead` 带 `whitespace-nowrap` | **已有**，列不会被挤压，超宽即容器内滚动 |
| 并排面板单列回退 | `report-comparison`、`run-detail-view:145`、`usage-summary:12`、`research-page:575`、`EvidenceRow` 全部 `sm:grid-cols-*` 起 | 已有 |
| 抽屉 | `SheetContent` `w-full max-w-[44rem] overflow-y-auto` | 已有 |
| 弹窗 | `DialogContent` `w-[calc(100%-2rem)] max-h-[calc(100dvh-2rem)] overflow-y-auto` | 已有 |

真正的缺口只有两个，都是纯 CSS 问题：

1. **写操作在手机上可点**：`tasks-page.tsx:169` 手动触发、`run-actions.tsx` 三个按钮、
   `processing-panel.tsx:211` 重试此阶段，在 390px 宽下仍完整可见可点，且会打开
   半屏 `Dialog` 或发起请求。
2. **触屏命中区偏小**：`research-article.tsx:68` 的 `[E1]` 按钮是
   `px-1 text-xs`（约 20×16px），9 处 `<summary>` 是纯行内文本高度。

两处固定两列（`overview-page.tsx:87` 存量数字条、`report-comparison.tsx:374` 侧栏
`dl`）内容都是短数值，390px 下每列约 170px，预期可读——**先不改**，由验收决定。

## 关键决策

### D1：表格不做任何结构改动

上一版设计打算用 JS 断点渲染卡片、或用 `hidden sm:table-cell` 折叠列。两者都不需要：

- 卡片化要 15 处改写 + 新组件 + 新 hook，改动面从「几个 class」变成「重构数据展示层」，
  且必须在手机上重新验证每一个字段，收益只是「少横向滑动」。
- 列折叠会让被隐藏的列在手机上**无处可读**——这与「字段不丢」直接冲突，除非再补一套
  「展开全部列」的机制，复杂度又回到卡片化。
- 「显示不下就滚动」是这个 Owner、这个数据密度下最合适的行为：表格本就是给桌面设计的
  高密度对照视图，手机上横向滑动读同一张表，字段、语义、列对齐、`<th>` 关联全部保留，
  读屏软件和键盘导航的行为与桌面一致。

结论：`ui/table.tsx` 与 15 处调用点**一行不改**。表头的 `whitespace-nowrap` 保留，
它保证列不被压缩到不可读，从而触发容器滚动而不是文字折成一列。

### D2：写操作用 `display:none` 隐藏，不用假禁用

Owner 选择 B：手机上不显示写操作。实现方式：

- 按钮外层或按钮自身加 `hidden sm:flex`（按钮是 `inline-flex`，用 `sm:flex` 保持
  布局语义）。**不加任何提示文案**：单人自用，Owner 知道写操作只在桌面提供。
- **不用 `pointer-events-none`**：那只是视觉禁用，键盘 Tab 仍能聚焦并回车触发，
  属于无障碍缺陷（`aria-disabled` 与真实行为不一致）。`display:none` 让元素彻底
  离开可访问树，语义诚实。
- 元素仍留在 DOM：`display:none` 的元素会被 Testing Library 的
  `getByRole`/`getByText` 默认排除（`ignore` 只影响 `getByText` 的隐藏节点，
  `getByRole` 要求 accessible），因此 `run-actions.test.tsx` 的
  `getByRole("button", { name: "结束异常 Run" })` 在 jsdom 下**仍然命中**——
  jsdom 不加载 Tailwind CSS，`hidden`/`sm:flex` 类不产生实际 `display` 计算，
  所有元素可见。现有用例安全。

被影响的三个位置：

| 位置 | 改动 |
| --- | --- |
| `tasks-page.tsx:169` 手动触发 | `<Button className="hidden sm:inline-flex">`；`<TableHead>操作</TableHead>` 加 `hidden sm:table-cell`，`<TableCell>` 同步 |
| `run-actions.tsx:87` 三个按钮 | 整组 `<div className="hidden flex-wrap gap-2 sm:flex">`；`ConfirmStopDialog` 保持不变（桌面仍可用） |
| `processing-panel.tsx:211` 重试 | `<Button className="hidden sm:inline-flex">`；「操作」列 `hidden sm:table-cell` |

「操作」列表头与单元格必须同时隐藏，否则手机上的表格会多出一列空白。

`run-actions.tsx` 有一个提前返回分支（`!can_retry && !can_continue && !can_end`
时显示「当前 Run 状态没有可用的恢复动作。」）——这是纯文本状态说明，不是写操作，
手机上也应显示，不改。

### D3：命中区用 `min-h-11` / `min-w-11`，不用 JS 测量

- `[E1]` 引用按钮：`px-1` → `inline-flex min-h-11 min-w-11 items-center justify-center px-1 sm:min-h-0 sm:min-w-0`。
  窄屏保证 44×44；`sm` 以上恢复原来的行内紧凑排版，避免桌面正文里出现 44px 高的引用角标
  撑开行距。
- 9 处 `<summary>`：**加 `py-2 min-h-11`，不要改成 `flex`**。`<summary>` 默认
  `display: list-item`，折叠三角标记（`::marker`）只在 `list-item` 下渲染；改成 `flex`
  会静默去掉这个「可展开」的视觉提示。靠内边距 + 最小高度撑到 44px，`text-xs` 的那两处
  （`research-article.tsx:118`、`research-page.tsx:393`）若实测不足 44 就加到 `py-3`。
  带 `w-fit` 的两处保留 `w-fit`，否则折叠标题会拉满整行、点击区横跨正文。
- `min-h-11` = 2.75rem = 44px，是 Tailwind 现有标度（`tailwindcss 4.1.17`），
  不新增 utility。
- 不用 JS 测量元素尺寸——`getBoundingClientRect` 只该出现在验收脚本里，不进生产代码。

### D4：抽屉安全区

`SheetContent` 的 `p-5` 改为 `p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))]`，
避免 iOS Safari 底部工具栏遮住最后一个可操作元素。`SheetHeader` 已有 `pr-8`
（避开 `top-3 right-3` 关闭按钮），无需改。

## 为什么不改的部分

| 组件 | 为什么不改 |
| --- | --- |
| `ui/table.tsx` | 已有 `overflow-x-auto` + `whitespace-nowrap`，行为正确 |
| `ui/dialog.tsx` | `w-[calc(100%-2rem)]`、`max-h-[calc(100dvh-2rem)]`、`overflow-y-auto` 已够用 |
| `report-comparison.tsx` 各 grid | 已 `sm:` 起，窄屏单列 |
| `run-detail-view.tsx:145`、`usage-summary.tsx:12` | 已 `sm:` 起 |
| `research-page.tsx:575` | 已 `sm:` 起 |
| `app-shell.tsx` | 移动导航已完整 |
| `overview-page.tsx:87`、`report-comparison.tsx:374` | 短数值两列，实测可读则不动 |

## 数据流与边界

- 纯样式改动，**零 JS 逻辑**：不新增 hook、不读 `matchMedia`、不改渲染结构、不改 props。
- 断点切换完全由 CSS 承担，不存在「JS 认为桌面、CSS 认为手机」的不一致风险，
  也不需要 `min-width: 40rem` 与 Tailwind 断点同步这类额外约束。
- 不触碰 `api/`、`schemas.ts`、`use-resource.ts`、路由、URL 参数语义。
- 服务端能力字段（`actions.can_*`、`enabled`、`configured`）仍是唯一权威；手机端只是
  不呈现操作入口，不改变任何判断逻辑。

## 风险与回滚

| 风险 | 处置 |
| --- | --- |
| 手机上某表格滚动容器失效（父级 `overflow` 裁剪） | 验收逐表检查 `scrollWidth > clientWidth` 且可改 `scrollLeft`；异常时检查祖先是否有 `overflow-hidden` |
| 「操作」列只隐藏按钮未隐藏表头/单元格，出现空白列 | 三个位置的表头与单元格成对修改，验收核对列数 |
| `[E1]` 的 `sm:min-h-0` 泄漏到桌面 | 抽查桌面报告详情行距 |
| `<summary>` 改成 `flex` 后折叠三角消失 | 只用 `py`/`min-h` 撑高度，保持 `display: list-item`；验收确认三角仍在 |
| jsdom 用例因 `hidden` 失败 | 理论不会（jsdom 不计算 Tailwind），若失败说明我判断错了，改为在 `run-actions.test.tsx` 用 `render` 后不依赖可见性 |
| 两处固定两列在 390px 挤压 | 实测确认后再改 `grid-cols-1 sm:grid-cols-2` |

回滚：单个前端提交，`git revert` 即恢复；无数据库、无配置、无 API、无依赖变更。

## 验证方式

1. 远程测试服务器（`/tmp/stocks-mobile-adaptation-<run>` 隔离目录）：
   `npm ci --ignore-scripts` → `npm run typecheck` → `npm run test` → `npm run build`。
2. 浏览器走查 390×844 与 360×800，逐页记录：
   - `document.documentElement.scrollWidth` 与 `window.innerWidth`；
   - 每处 `<Table>` 容器的 `scrollWidth` / `clientWidth`，以及把 `scrollLeft` 设到
     最大值后最右列是否真的可见；
   - 写操作按钮的 `offsetParent === null`（不可见），且手机宽度下页面不出现写操作文案；
   - `[E1]` 与 9 处 `<summary>` 的 `getBoundingClientRect()` 高度/宽度 ≥44；
   - 打开文档详情 / Run 详情抽屉与证据弹窗，确认可滚动、可关闭、焦点回到触发元素；
   - 控制台报错数。
3. 桌面回归 1280×800：概览、任务、研究详情、运行详情，确认表格外观、写操作按钮
   与 `select` 筛选行为未变。
4. 结果写入 `acceptance.md`：viewport、逐页数值、逐表滚动数值、命中区测量、
   控制台错误、fixture 位置、未覆盖项。
