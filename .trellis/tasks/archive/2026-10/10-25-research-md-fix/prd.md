# 研究页修复：布局崩溃 + Markdown 渲染

Owner 反馈两点：
1. 研究报告页「布局崩溃」——指视觉排布崩坏，非 JS 报错。
2. 报告内容是 Markdown（## 标题、- 列表），当前只以 whitespace-pre-wrap 原样显示符号。

## 布局崩溃根因（分析）

报告列表的 role="table" 用了 CSS grid + `col-span-full` 行。在 grid 容器内，隐式行
由内容生成，`col-span-full` 行没有设置 `min-width:0`，而内部 button 有
`min-w-0 flex-1` + `line-clamp-2`——当标题或 task_id 过长时，grid 轨道
`minmax(0,1fr)` 应能收缩，但 `role=row` 的 `col-span-full` div 本身是 grid item，
其内部 button 的 `w-[calc]` 类已删，可能仍然把 grid 撑破。此外 xl 断点下列表列
`minmax(18rem,0.56fr)` 与阅读列并存，小屏回退后列表行文字挤压换行错乱。
更直接的疑似点：**表格化行用了 grid 但没有对 header/rows 建立可靠的列对齐**，
长标题把「任务/状态/时间」列推出容器，视觉上即「崩溃」。

## 修复方案

1. **列表**：放弃手写 grid 表格，报告列表改回语义化结构但用可靠实现——
   `divide-y` 行列表（标题一行截断 + 次要行元信息 + 状态徽章），与 Runs/Intelligence
   的行列表一致。报告是阅读场景，列表不需要真表格的列对齐。
2. **正文渲染 Markdown**：报告内容是受控的后端生成 Markdown（标题/列表/少量强调），
   引入 `marked` + `dompurify` 渲染为安全 HTML（无 raw HTML 透传，dompurify 兜底），
   配 Typography 风格的自定义 CSS（h2/h3/ul/ol/p/strong/code）。
   - 不用 react-markdown（依赖重）；marked 是单一小依赖。
   - 渲染容器 `prose-report` 自定义类，不引 @tailwindcss/typography（避免新 dev 依赖）。
3. **阅读列宽**：xl 断点下保持两栏；正文加 `max-w-[75ch]` 防长行。

## 验收
- 列表在任何宽度下不撑破、不错行。
- 报告正文标题/列表正确渲染，`<script>` 等被清除。
- 远程 typecheck/build/vitest 通过；生产部署容器内核验。
