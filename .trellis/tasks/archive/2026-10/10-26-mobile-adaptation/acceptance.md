# 移动端适配验收记录

- 任务：`.trellis/tasks/10-26-mobile-adaptation`
- 日期：2026-10-26
- 测试服务器：`root@100.120.14.84`，隔离目录 `/tmp/stocks-mobile-adaptation-10261602`
- 分支：`feat/research-article`
- 结论：**通过**，90/90 断言通过，控制台零报错。

## 环境与命令

```bash
# 构建与静态检查（远程隔离目录）
cd /tmp/stocks-mobile-adaptation-10261602/frontend
npm ci --ignore-scripts   # exit 0
npm run typecheck         # exit 0
npm run test              # exit 0 — 13 test files / 70 tests passed
npm run build             # exit 0 — built in 7.10s

# 浏览器验收
cd /tmp/stocks-mobile-adaptation-10261602/smoke
node smoke.mjs /tmp/stocks-mobile-adaptation-10261602   # exit 0 — 90/90
```

数据来源：运行中的测试服务器实例 `stocks-*`（真实库 41 文档 / 250 Fact / 3 报告 / 2 来源 /
7 Run / 115 chunk）。用 Owner 会话 `curl` 抓取真实 API 响应存入 `fixtures/`，再由本地
HTTP server 回放，页面加载本次构建的 `frontend/dist`。所有断言针对真实渲染结果，不是桩数据渲染。

**已知夹具修正**（记录在案，不隐瞒）：部署的 API 是旧构建，`analysis` 缺 `title`/`sections`、
`findings` 缺 `id`/`title`，与当前分支 `reportDetailSchema` 不符，页面报「API 响应与当前控制台版本
不兼容」。我在夹具中由**真实 `report.content` 的 Markdown 标题**生成 `sections`，由真实
`statement` 首句派生 `title`，未编造研究内容；正文、证据、引用编号全部来自真实响应。

## 页面级横向滚动（无整页横向滚动）

| viewport | 页面 | `documentElement.scrollWidth` | `innerWidth` | 结果 |
| --- | --- | --- | --- | --- |
| 390×844 | overview | 390 | 390 | 通过 |
| 390×844 | sources | 390 | 390 | 通过 |
| 390×844 | tasks | 390 | 390 | 通过 |
| 390×844 | intelligence | 390 | 390 | 通过 |
| 390×844 | research | 390 | 390 | 通过 |
| 390×844 | runs | 390 | 390 | 通过 |
| 390×844 | processing | 390 | 390 | 通过 |
| 390×844 | report-detail | 390 | 390 | 通过 |
| 390×844 | login | 390 | 390 | 通过 |
| 360×800 | 同上 9 页 | 360 | 360 | 通过 |

`document.body.scrollWidth` 与 `documentElement` 一致，没有元素撑破视口。

## 表格：容器内横向滚动到最右列

每张表都执行「把容器 `scrollLeft` 推到最大，确认停在 `scrollWidth - clientWidth`」，
证明内容真的能滚到末列，而不是被父级裁剪。

| viewport | 页面 | 表格 | scroll/client | 可见表头 | 结果 |
| --- | --- | --- | --- | --- | --- |
| 390 | overview | 来源分布 | 510/358 | 5/5 | 通过 |
| 390 | overview | 最新报告 | 358/358 | 4/4 | 通过（无需滚动） |
| 390 | overview | 最近入库 | 618/358 | 4/4 | 通过 |
| 390 | sources | 采集源 | 1037/358 | 9/9 | 通过 |
| 390 | tasks | 任务 | 503/358 | 5/6 | 通过（「操作」列已隐藏） |
| 390 | intelligence | 检索结果 | 498/358 | 5/5 | 通过 |
| 390 | research | 报告列表 | 537/358 | 4/4 | 通过 |
| 390 | runs | Run 列表 | 553/358 | 6/6 | 通过 |
| 390 | processing | 处理积压 | 587/358 | 5/6 | 通过（「操作」列已隐藏） |
| 360 | 同上 | — | client 328 | 同上 | 全部通过 |

桌面 1280×800：概览 3 张表 `scrollWidth == clientWidth == 976`，`maxScroll = 0`，
无回归。

## 写操作在手机上隐藏

| 断言 | 390×844 | 360×800 |
| --- | --- | --- |
| 任务页「手动触发」在 DOM 中但宽度为 0 | 通过 | 通过 |
| 任务页「操作」表头列不可见 | 通过 | 通过 |
| 未新增「写操作…」提示文案 | 通过 | 通过 |
| Run 页「重试失败步骤」宽度为 0 | 通过 | 通过 |
| Run 页「继续未完成扫描」宽度为 0 | 通过 | 通过 |
| 处理积压「重试此阶段」宽度为 0 | 通过 | 通过 |
| 处理积压「操作」表头列不可见 | 通过 | 通过 |

说明：`结束异常 Run` 未覆盖——该 Run 的 `actions.can_end=false`，服务端不返回该动作，
按钮本就不渲染。断言从「必须存在但不可见」改为只检查服务端实际提供的动作，避免把
「服务端没给」误判成「前端隐藏」。

桌面 1280×800：「手动触发」「操作」列、Run 恢复动作（重试失败步骤 / 继续未完成扫描）
全部可见且宽度 >40px，行为未变。

## 触屏命中区

`getBoundingClientRect()` 实测，两端 viewport 一致：

- `[E1]`、`[E23]`、`[E42]`、`[E52]`、`[E53]` 引用按钮：宽高均 ≥44×44。
- 6 个 `<summary>` 实例（「查看分析依据 · 5 项发现」×3、「· 4 项发现」×2、「研究资料与生成信息」）：
  高度均 ≥44。

**测量口径**：这 6 个实例全部位于报告详情页，覆盖 `research-article.tsx:118`（×5）与
`research-page.tsx:393`。9 处 `<summary>` 的另一半（`research-page.tsx:476`、`:647`、
`tasks-page.tsx:97`、`intelligence-page.tsx:216`、`run-detail-view.tsx:243`、
`fact-list.tsx:90`、`document-details.tsx:26`）未逐一测量，其中后三处在抽屉与懒挂载 Tabs 内。
它们与已测的 9 处类名完全同构（同一组 `min-h-11 py-3 sm:min-h-0 sm:py-0`），高度由
`min-h-11` 保证，因此记为**同构推断**而非实测。

桌面 1280×800：引用按钮高度 16px、`<summary>` 高度 16px——`sm:min-h-0`／`sm:py-0`
正确抵消，桌面正文行距未被撞开。

## 抽屉与弹窗

- 390×844 与 360×800：文档详情抽屉宽度 ≥ 视口宽 − 2px（全宽），`overflow-y: auto`，内容可滚动。
- 证据弹窗：键盘 `Enter` 从引用按钮打开，宽度 ≤ 视口宽；`Escape` 关闭后
  `document.activeElement` 的 `aria-label` 仍以「查看来源」开头，焦点正确回到触发按钮。

### Sheet 底部安全区的真实状态

`sheet.tsx` 写入的是 `pb-[max(1.25rem,env(safe-area-inset-bottom))]`。但本应用
`index.html:5` 的 viewport **不含 `viewport-fit=cover`**，而 iOS Safari 在未声明
`viewport-fit=cover` 时 `env(safe-area-inset-*)` 恒为 0。

因此实际效果有两面，需如实记录：

- 该声明是**无害的防御性写法**，不是缺陷：不 opt-in `cover` 时，Safari 会把页面自身
  内缩到安全区，抽屉底部本就被系统避开，PRD「不被系统安全区遮挡」由 Safari 默认行为达成。
- 同时它也是**当前无效的代码**：`max(1.25rem, 0)` 退化为 1.25rem，与改动前等价。
  一旦将来为了满屏效果加 `viewport-fit=cover`，这段 `pb` 会立即生效，无需再改。
- 未反向添加 `viewport-fit=cover`：那会让内容顶到刘海/状态栏，需同时补顶部安全区并重测，
  超出本任务「纯 CSS、最小改动」范围，也超出 PRD 要求。

## 控制台

两轮移动端走查 + 桌面回归，`console` 错误与 `pageerror` 均为 **0**。

（过程中曾出现 4 条 404，定位为夹具缺 `search.hits` 命中文档的详情路由——我抓 fixture 时
只取了文档列表第一项。补齐 8 份命中文档的 detail/facts 后归零，非应用缺陷。）

## 未覆盖 / 限制

1. **未在真机验证**。使用 Chromium + `isMobile: true, hasTouch: true` + 精确 viewport，
   不是 iOS Safari / Android Chrome。Sheet 底部安全区见上节：`env()` 无 `viewport-fit=cover`
   时恒为 0，实际遮蔽保护来自 Safari 默认内缩，未在真机读到真实 inset 值。
2. **报告对照视图（`?compare=`）未走查**。当前库中 3 份报告的任务/范围不能构成有效对照，
   生成真实 fixture 成本高于收益；该视图的网格本就是 `sm:grid-cols-*` 起，本轮未改动。
3. **`结束异常 Run` 的隐藏未验证**（该 Run `can_end=false`）。代码路径与另两个按钮同组，
   同一 `hidden sm:flex` 容器，风险低但未实测。
4. **两处固定两列未改**：`overview-page.tsx:87` 存量数字条与 `report-comparison.tsx:374`
   侧栏 `<dl>`。390px 实测页面无整页横向滚动、数字未被挤压，故按计划保持不动。
5. **未测横屏**（844×390）与平板宽度（`sm`–`lg` 区间）。
6. **表格测量只覆盖页面级表格**。21 条表格记录 = 9 页 × 2 viewport 的页表（18 条）+ 桌面概览
   3 张。未测量抽屉与懒挂载 Tabs 内的表：`document-details.tsx:135` 解析批次表、
   `processing-stages.tsx:19`、`fact-list.tsx:44` Fact 表、`source-independence.tsx:135` 对照表。
   它们与页表共用同一份未改动的 `ui/table.tsx` 容器（`overflow-x-auto`），机制同源。
7. **7 处 `<summary>` 命中区为同构推断**，见「触屏命中区 · 测量口径」。

## 产物

`/tmp/stocks-mobile-adaptation-10261602/artifacts/`：

- `report.json`：90 条断言明细 + 21 条表格测量 + 控制台错误
- 24 张截图：`390x844-*`（9 页 + 抽屉）、`360x800-*`（9 页 + 抽屉）、`1280x800-*`（4 页）
- 验收脚本：`.trellis/tasks/10-26-mobile-adaptation/smoke.mjs`（复用，不加入生产依赖）
