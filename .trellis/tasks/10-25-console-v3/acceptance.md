# 验收记录

## 结论：v3 完成并已部署生产（含容器内核验）

## 配色（对齐 multica 灰白体系）

拉取了 multica-ai/multica 的实际 `packages/ui/styles/tokens.css` 对齐：页面画布
`oklch(0.988 0 0)`、表面纯白、前景 `0.141`、边框/输入 `0.92`、hover/选中灰、
**primary 为近黑灰 `oklch(0.21 0.006 286)`**（不再是绿色主题色）、语义色只用于状态
（success/warning/info/destructive）。侧边栏从深色块改为浅灰。Accent 黄色调删除。
字体栈改为系统字体（-apple-system/Segoe UI/PingFang SC），移除 Geist/Newsreader
依赖与导入；`index.html` 的 theme-color 从深青色改为画布灰白。

## 交互重设计（核心）

新增 `ui/sheet.tsx`（Base UI Dialog 原语，右侧抽屉 44rem，遮罩/Esc 关闭，200ms 滑入，
reduced-motion 收敛）：

| 页面 | 交互变化 |
| --- | --- |
| Intelligence | 左右双栏 → **全宽 Table**（标题/来源/版本/完整度/发布时间/相似度列）；点行打开右侧 **Document Sheet**：标题+徽章 → 元数据 dl → 原件 → Separator → 处理状态小表 → Separator → 编号片段（正文+定位折叠+读上下文）→ Separator → Fact Table |
| Runs | Run 列表 → 全宽 Table（任务/状态/创建/步骤/Run ID/错误摘要）；点行打开 **Run Sheet**：头部 dl+错误+操作 → Separator → 步骤紧凑行（输出折叠）→ 模型调用 Table → 用量 |
| Research | 保留列表+阅读两栏（Owner 认可），列表改紧凑表格化行；阅读区不变 |
| Overview / Tasks | 维持 Table 化 |

URL 语义保留：`?document=` / `?run=` 仍驱动 Sheet 开关（replace:true），深链接与
后退可用。行是原生 button，aria-pressed/键盘 Enter/Space 保留。

## 审查发现并处理（提交前全部修复）

1. [P1] `text-warning` 无令牌定义，警告文字渲染成无样式灰 → 新增
   `--warning/--warning-text/--success/--info` 语义令牌；文字用途用较深的
   `warning-text`（0.55）保证对比，表面/边框用规格值（0.75）。
2. [P5] 字体栈声明了未加载的 Inter → 改为系统栈。
3. [P2] 测试重复断言块 → 去重。
4. [P3] 死代码 `DocumentSheet` 包装器 → 删除并清理导入。
5. [P4] Research 列表手写 ARIA 表格 → 改 grid 对齐列宽（role 保留）。

## 验证

| 检查 | 结果 |
| --- | --- |
| 远程 typecheck / vitest（9 文件 29）/ build | 全部退出码 0 |
| 生产同步 | 8 个变更文件 scp 后 md5 与仓库一致 |
| web 镜像重建 + 容器强制重建 | healthy，/health 200 |
| 容器内核验 | 新指纹 `index-CQeriObV.js` / `index-BxJWMmtI.css`；CSS 含 `warning-text` 令牌；无 Inter 残留声明 |

## 限制

真机/浏览器视觉验收属 PA-3 待办；「行点击」实现为标题单元格全宽按钮（可访问性更稳），
整行任意位置点击的命中区域差异如 Owner 在意可再调。
