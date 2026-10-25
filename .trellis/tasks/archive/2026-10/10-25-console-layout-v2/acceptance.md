# 验收记录

## 结论：全站布局重构完成，Card 从 135 个减到 1 个（登录页），已部署

## 诊断与方案

Owner 判定此前界面「一眼 AI 图、大 card 块」。根因：135 个 Card 包裹全部内容区块，
存在三层嵌套；4 个统计数字做成 4 张带图标大卡；列表项全是 rounded-xl 卡；报告页一个
逻辑区域拆成 6 张卡；eyebrow 大写间距、衬线标题、渐变背景、网格纹理等装饰堆料。

方案（design.md）：内容即界面——分隔靠间距与 1px 分隔线，不靠卡框；ShadCN 原生组件
优先；层级压到 2 层；列表一律 Table 或 divide-y 行；选中态改为左侧 2px 主色竖线 +
bg-muted/40；去全部装饰（eyebrow/衬线标题/渐变/网格）。

## 各页改动

| 页面 | 改动 |
| --- | --- |
| 全局 | PageHeader 去 eyebrow 与衬线；body 去渐变；新增轻量 `Section`（标题+分隔线+内容，非卡）；所有数字 tabular-nums |
| Overview | 4 张统计卡 → 无框 4 列数字条；近期 Run/最新 Document/来源状态/近期报告 → Table；覆盖与用量 → dl |
| Intelligence | 表单去卡框；命中与 Document 列表 → divide-y 行；详情列连续排版（元数据 dl → 处理状态 Table → 片段行 → Fact Table） |
| Tasks | 2 列大卡 → 单 Table（行内触发按钮，scope 折叠展开） |
| Research | 列表 → divide-y 行；阅读页全部去卡：元数据行 → Alert → 正文排版 → 缺口 Badge → 来源独立性 dl → 对照 Section → 指标对比 Table → 用量 dl → 证据包编号行 |
| Runs | 筛选行内化；Run 列表 → divide-y 行；详情连续排版（头部 dl + 步骤紧凑行 + 模型调用 Table）；积压 → Table |
| 登录 | 唯一保留的 Card；去重阴影、图标色块、eyebrow |

## 交互与语义不变（审查确认）

URL 参数、选中/最新报告逻辑、aria-pressed/键盘处理、处理重试与确认停止流程逐字节
比对无回归。测试仅更新选择器；审查发现 usage-summary 测试丢失了 per-model 未知费用
断言，已恢复（新增 UsageModels 用例）。

## 审查发现并处理

1. [P2] usage-summary 测试断言弱化 → 恢复 per-model 未知费用断言。
2. [P3] 报告正文 font-serif 的 --font-serif 主题令牌被删除 → 恢复含中文回退的
   serif 令牌，移除无引用的 Newsreader 导入。

## 验证

| 检查 | 结果 |
| --- | --- |
| Card 使用 | 全站仅登录页 1 个（审查 grep 确认） |
| 远程全量 vitest | 9 文件 **29/29 通过**（新增 1 个用例） |
| `npm run typecheck` / `npm run build` | 退出码 0 |
| 生产 web 镜像重建并强制重建容器 | healthy，/health 200 |

## 补充：一次部署事故的纠正（2026-10-25）

Owner 反馈「你确定你更新了吗」——核实后发现**生产部署目录 `/opt/stocks/frontend/src`
仍是旧源码**：重构代码只同步到了测试目录 `/tmp/stocks-review-fixes-20261023`，
web 镜像从旧源码构建，导致线上界面未变。属部署同步遗漏，验收时只核对了测试目录
构建结果，未核对生产镜像内容。

纠正措施：
1. 从 `main` 重新打包 `frontend/src` 同步到 `/opt/stocks`（md5 比对 runs-page 一致）。
2. 重建 web 镜像并 `--force-recreate` 容器（created=2026-10-25T03:58Z）。
3. 在**生产容器内**逐包核对产物：runs/intelligence/research 分包含 `border-l-2`+
   `divide-y`，overview/tasks 分包引用 Table 组件族与「近期运行」等区块文案，
   `section-*.js`（新 Section 组件）与 `border-l-2` CSS 工具类均存在，eyebrow 特征
   `tracking-[0.16em]` 已无残留；index.html 引用指纹与容器内文件一致（Cache-Control:
   no-cache，无缓存问题）。

教训：跨目录部署时必须核对目标容器内的实际构建产物，不能以测试目录结果代替。

## 限制

真机/浏览器视觉验收仍未执行（PA-3 既有待办）；密度与对齐的最终观感以 Owner
实际浏览为准。
