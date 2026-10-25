# 验收记录

## 三个问题的结论

### 1. 情报检索「API 响应与当前控制台版本不兼容」——真实前端缺陷，已修复

- 根因：`documentDetailSchema` 要求 `current_chunks` 内有 `has_more: boolean`，但后端
  `/api/documents/{id}`（`api.py` 的 `current_chunks` 分页）从不发送该字段，只发送
  `{count, items, next_cursor, limit}`。点击任何检索命中加载详情时 Zod `safeParse` 失败，
  触发 `client.ts:146` 的兜底文案。
- 修复：`frontend/src/api/schemas.ts` 为 `current_chunks` 定义显式结构（无 `has_more`）；
  `versions` 字段确实带 `has_more`，保持原分页结构。
- 不是陈旧构建：镜像内 bundle 与 HEAD schema 字节一致；是对账遗漏（此前 curl 只核对了
  search/facts，漏了 document detail）。
- 回归：`intelligence-page.test.tsx` 用生产形状 fixture（float similarity、locator dict、
  int evidence_ref.position、无 has_more 的 current_chunks），断言三处渲染且不兼容文案
  从不出现，4/4 通过。

### 2. 界面重新设计——已落地

按 `apple-design` 与 `emil-design-eng` 技能原则：

| 方面 | 改动 |
| --- | --- |
| 设计令牌 | `--primary` 加深至 oklch(0.4 0.07 165)（对白 ~5.5:1）、次要文字加深至 0.44 满足 WCAG AA、边框增强、背景光晕收敛；新增运动令牌 `--motion-fast/ui/page` 与统一 `--ease-out: cubic-bezier(0.22,1,0.36,1)` |
| 按压反馈 | 新 `press` 工具类（120ms，只过渡廉价属性），应用于全部按钮、侧边栏链接、所有可选列表行；共享 Button `active:scale-[0.98]` |
| 入场动效 | `rise-in`（8px 上移、200ms ease-out）按 pathname 挂载触发（路由级，非筛选参数）；登录卡、加载面板、toast 同样处理；`prefers-reduced-motion` 与 `MotionConfig reducedMotion="user"` 双重收敛 |
| 层级与状态 | 卡片 1px 环境阴影、可选卡片 hover 微环、失败模型调用行 `bg-destructive/5`、骨架屏柔化、空面板图标降调 |

审查（trellis-check）发现 link 变体丢失过渡，已补 `press`。类型检查、生产构建、全量
vitest（9 文件 28/28）在远程通过。

### 3. 研究报告输出简体中文——已落地并实测

- `config/tasks.yaml` 的 `research_writer` 提示词追加明确要求：所有用户可见文本
  （summary、findings、rationales、uncertainties、open questions、覆盖说明）一律简体中文；
  机构名、规则/文号、引用原文等需精确的术语保留原文并在首次出现时括注
  （例：美国证券交易委员会（SEC））；数值、单位、ISO 时间保持原形。
- 真实验证：Run `c8462898-13a4-42d2-8e03-5f48dda70f2a` succeeded，报告正文 Summary 起
  全部为简体中文（如「本次检索共纳入 5 个独立原件……」），数字/单位/ISO 时间保持原形。
  注：第一次触发因模型偶发 60s 超时失败（保留失败记录），重试即成功，符合既有
  「失败 Run 不伪装、重试新建 Run」契约。

## 验证

| 检查 | 结果 |
| --- | --- |
| 远程 vitest（intelligence-page.test.tsx） | 4/4 通过 |
| 远程全量 vitest | 9 文件 28/28 通过 |
| `npm run typecheck` / `npm run build` | 退出码 0 |
| 生产 web 镜像重建并部署 | 四服务 healthy，/health 200 |
| 真实中文研究 Run | succeeded，正文简体中文 |
| 配置加载 | `load_task_configuration` 5 tasks / 1 agents 无报错 |

## 限制

- 对比度结论中 `--primary` 对白字约为 4.5–5:1，属 AA 达标但余量小；最终以浏览器实际
  viewport 观感为准（真机视觉验收仍属 PA-3 待办）。
- 第一次中文研究 Run 的失败由模型偶发超时导致，与本次改动无关，失败记录保留。
