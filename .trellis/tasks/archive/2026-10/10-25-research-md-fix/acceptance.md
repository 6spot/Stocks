# 验收记录

## 结论：两处修复完成并已部署生产（md5 核对 + 容器内核验）

## 修复 1：报告页列表布局崩溃

根因：报告列表用手写 CSS grid 伪表格（`grid-cols` + `col-span-full` 行），长标题把
「任务/状态/时间」列推出容器，行错位挤压，视觉上即「崩溃」。

修复：改回与 Runs/Intelligence 一致的 `divide-y` 行列表（首行标题截断 + 任务徽章 +
状态，次行生成时间 + run 短 id），原生 button、aria-pressed、选中 bg-muted 均保留；
两栏 xl 布局不变。

## 修复 2：报告 Markdown 渲染

报告内容是后端生成的 Markdown（`## 标题`、`- 列表`），此前以 `whitespace-pre-wrap`
原样显示符号。现引入 `marked@18` + `dompurify@3`：

- `frontend/src/lib/markdown.ts`：`renderMarkdown()` = parse + sanitize，
  ALLOWED_ATTR 白名单（href/title/src/alt/colspan/rowspan）、FORBID_TAGS style；
  注释明确「后端生成仍按不可信输入消毒」。
- 正文容器 `report-prose`（自定义排版：h2/h3/p/ul/ol/strong/code/blockquote，
  未引入 typography 依赖）+ `max-w-[75ch]` 防长行 + `pre` overflow-x。
- 全应用唯一 `dangerouslySetInnerHTML` 注入点，输入仅来自已存储报告。

## 审查发现并处理

1. [P2] PRD 要求的 75ch 阅读宽度缺失 → 已加。
2. [P3] 消毒测试未覆盖 javascript: href 与 style 标签两条关键路径 → 补 2 个用例。
3. [P3] pre 长行溢出 → 加 overflow-x。
4. security 检查通过：DOMPurify 配置可中和 script/事件处理器/javascript: URI/style
   注入；注入点唯一；无依赖漂移。

## 验证

| 检查 | 结果 |
| --- | --- |
| 远程 typecheck / vitest / build | 退出码 0，**10 文件 34 passed**（新增 2 个消毒用例） |
| 生产同步 | 3 个后续变更文件 md5 与仓库一致 |
| web 镜像重建 + 容器强制重建 | healthy，/health 200 |
| 容器内核验 | 新指纹 `index-C5SzCnfA.js` / `index-pDL1dRFd.css`；research 分包含 `report-prose`；CSS 含 `.report-prose` 与 `75ch` |

## 限制

真实浏览器视口下的视觉验收未做（PA-3 待办）。
