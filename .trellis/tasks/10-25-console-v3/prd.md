# 控制台 v3：交互重设计（灰白配色 + Table/抽屉）

Owner 反馈：样式可以再大胆些；配色差，参考 multica（灰白、无彩度）；交互逻辑仍混乱——
情报等列表页应直接 Table，点详情从侧边抽屉（Sheet）展开，详情内容要有层级关系。

## 参考对齐（multica-ai/multica，已拉取其实际 tokens.css）

灰白体系核心值（我们逐项对齐，微调色相以适配金融情报场景）：
- 页面画布 page-canvas: oklch(0.988 0 0)（近白灰）
- 表面 surface: oklch(1 0 0)（纯白，承载内容）
- hover: oklch(0.967 0 0)、selected: oklch(0.95 0.002 0)、border: oklch(0.92 0 0)
- 前景: oklch(0.141 0.005 286)、次要文字: oklch(0.505 0.016 286)
- **primary = 近黑灰** oklch(0.21 0.006 286)（按钮/主操作），不是绿色主题色
- 语义色只用于状态：success oklch(0.55 0.16 145)、warning oklch(0.75 0.16 85)、
  info oklch(0.55 0.18 250)、destructive oklch(0.577 0.245 27)
- 侧边栏从深色块改为**浅灰**（与画布同系，靠分隔线区分）
- 字体 Inter Variable + PingFang SC（去 Geist/衬线显示字体的特殊感）

## 交互重设计（核心）

### 主-详交互统一模式
现在三页（Intelligence/Runs/Research）都是「左列表+右详情」两栏并存，右栏内容长、
层级深，来回滚动。改为：
- **列表页 = 全宽 Table**（不再左列表右详情）。
- **点击行 → 右侧 Sheet 抽屉**（shadcn Sheet，44rem 宽，遮罩可关，Esc 关闭）。
- URL 语义保留：?document= / ?run= / ?report= 仍驱动抽屉打开，深链接/后退可用。
- 移动端 Sheet 自然全屏。

### Intelligence
- 检索命中与 Document 列表合并为**一张 Table**（命中含相似度列；默认浏览展示
  最近 Document，无相似度列）。列：标题 / 来源 / 版本 / 时间 / 完整度 / (相似度)。
  行点击打开 Sheet。
- 命中正文摘要：Table 内不放大段正文，移入 Sheet；表格行保持单行+次要行。
- Sheet 内层级：标题+元数据 dl → 处理状态（解析/索引两列小表）→ 当前片段
  （编号行+正文+定位折叠+「读取上下文」）→ Fact Table → 原件按钮。

### Runs
- Run 列表 → 全宽 Table（任务/状态/创建/步骤/ID）。点击行开 Sheet。
- Sheet 内：头部 dl + 错误 Alert + 操作按钮行 → 步骤紧凑行（折叠输出）→
  模型调用 Table → 用量。
- 处理积压 Tab 保持全宽 Table，操作列行内。

### Research
- 报告列表 → 全宽 Table（标题/任务/状态/生成时间），点击开 Sheet？**否**：
  报告是阅读场景，保留「列表 + 阅读区」两栏（Owner 对左右布局无意见），但列表
  也表格化；正文阅读区保持。
- 报告阅读区内部维持现有连续排版（已达标）。

### Overview
- 全部 Table 化保持；统计条保留。

## 配色落地
- index.css :root 全套令牌替换为上述灰白值；sidebar 浅灰；badge/状态色沿用
  语义色。dark 模式不在本次范围（保持 light-only）。
- 删除 --accent 黄色调遗留。

## 不变
路由/URL 语义（Sheet 由 URL 驱动）、数据流、API 契约、键盘/aria、reduced-motion。

## 验收
- 列表页全宽 Table；详情均在 Sheet 中且 URL 可深链。
- 灰白配色无彩色主题色（状态色除外）。
- 远程 typecheck/build/vitest 通过；生产部署核对容器内产物（吸取上次教训）。
