# 控制台布局重构：去卡片化与 ShadCN 原生组件

Owner 反馈：当前页面「一眼 AI 图，大 card 块」。要求：能用列表就用列表、能用 Table 就用
Table、优先 ShadCN 官方组件、避免层级过深、页面交互布局与交互逻辑全部重新设计。

## 诊断（为什么像 AI 生成的）

1. 全站 135 个 Card 包裹一切 UI 区块，存在三层 Card 嵌套（页面 > 区块 > 条目）。
2. 4 个统计数字用 4 张带图标大卡展示，密度极低。
3. 检索命中、Run 列表、报告列表、处理积压条目全部是 rounded-xl 卡，列表被做成一摞卡。
4. 报告页一个逻辑区域被拆成 6 张互不相干的卡。
5. eyebrow 大写间距、衬线 editorial 标题、径向渐变背景、网格纹理等装饰堆料。

## 设计方向

- **内容即界面**：数据密度优先；分隔靠间距与 1px 分隔线，不靠卡框。
- **ShadCN 原生组件优先**：Table、Tabs、Badge、Button、Alert、Separator、Skeleton、
  Dialog、Checkbox。Card 仅保留登录页等真正浮出的独立内容。
- **层级 ≤ 2**：页面 > Section（标题+描述+分隔线），区内直接是行/表/定义列表。
- 列表一律 Table 或 divide-y 行；详情用 dl 定义列表；报告正文直接排版。
- 选中态 = 左侧 2px 主色竖线 + bg-muted/40，不再用整圈 ring。
- 去装饰：PageHeader 去 eyebrow 大写与衬线、body 去渐变/网格纹理。
- 动效沿用已有 press/motion-rise 令牌与 reduced-motion 收敛。

## 各页要求

- Overview：统计改一行数字摘要（无框）；各区块改 Section + Table/dl。
- Intelligence：表单去卡框；命中与 Document 列表改行列表；详情侧改连续排版
  （元数据 dl → 处理状态 Table → 片段列表 → Fact Table）。
- Tasks：2 列大卡改 Table（行内触发按钮），scope 收进行内展开。
- Research：列表改行列表；阅读页去卡直接排版，指标对比/对照改 Table。
- Runs：筛选行内化；Run 列表改行列表；详情连续排版（步骤行 + 模型调用 Table）；
  处理积压改 Table。
- 登录：保留单卡，去重阴影与图标色块。

## 不变

路由/URL 语义、交互逻辑（选中、筛选、恢复操作）、数据流、API 契约、可访问性
（aria/键盘）、既有 reduced-motion 行为。测试更新选择器但断言行为不变。

## 验收

- 全站页面内容区不再出现嵌套 Card；Card 仅登录页。
- 列表均为 Table 或行列表；层级 ≤ 2。
- 远程 typecheck/build/相关 vitest 通过；关键交互（检索选中、报告选择、积压重试）
  行为测试不回归。
