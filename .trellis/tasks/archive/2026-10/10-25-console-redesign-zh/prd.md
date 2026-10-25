# Web 控制台界面重新设计与报告中文输出

Owner 对当前界面的三点反馈：视觉与动画「太 low」；研究报告必须输出简体中文；
情报检索页提示「API 响应与当前控制台版本不兼容」。

## 问题 1：情报检索报「API 响应与当前控制台版本不兼容」（优先查清）

该消息来自 `frontend/src/api/client.ts:146`——Zod `safeParse` 校验失败时的兜底文案。
**已用 curl 逐字段核对 `/api/search` 与 `/api/facts` 的真实响应：与 `schemas.ts` 完全匹配
（`hits` 20 条、`facts` 71 条零违规），API 直接调用不报错。**

因此该报错极可能是**前端构建产物过期**：部署的 web 镜像构建于 10-24 13:52，而
`docs` 相关 schema 在 10-24 之后有过多次提交。需要：
- 先在隔离环境用**当前镜像**复现（浏览器或 jsdom 走完整 `dashboardApi` 路径），
- 若复现，定位具体字段；若不复现，重建镜像验证是否为陈旧构建产物。

## 问题 2：研究报告输出为英文

`config/tasks.yaml` 的 `research_writer` 提示词未指定语言，模型跟随输入（英文证据）输出英文。
- 在 `research_writer` 提示词中明确：报告正文、findings、open questions、覆盖说明一律
  **简体中文**；保留原文关键术语（如机构名、规则编号）时括注英文。
- 这是任务配置变更，不改代码、不改 schema。

## 问题 3：界面视觉与动画重新设计

- 用 `apple-design` 与 `emil-design-eng` 技能重审全部页面（Overview / Intelligence / Tasks /
  Research / Runs / 登录），产出并落实统一的设计改进：层级、留白、字号节奏、色彩、
  状态反馈与过渡动画（遵循 `prefers-reduced-motion`）。
- 不改任何 API 契约与证据语义；沿用 Nova 风格与 shadcn/ui + Base UI 原语。
- 交付前以组件测试 + 类型检查 + 生产构建验证，浏览器验收记录真实 viewport。

## 验收

- 情报检索页不再出现版本不兼容报错，且明确根因。
- 新触发的真实研究 Run 报告为简体中文。
- 设计改进落地并有过视觉确认记录；不引入新依赖（动画用 CSS/Motion 既有能力）。
