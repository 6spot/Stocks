# V1-08 实施计划

## 开工条件

- [x] 复核 PRD 与最新领域契约；前置任务：
  [V1-07](../../archive/2026-10/10-20-v1-07-owner-console-api/prd.md)
- [x] 重新检查 V1-07 API、测试和响应 Schema，落实具体页面、依赖与交互。
- [x] Owner 已明确授权按 V1-07、V1-08、V1-09 顺序继续；`task.py start` 已执行。
- [x] implement.jsonl / check.jsonl 已提供真实规范与基线，实施时按实际改动复核。

## 执行结果

- [x] 初始化 Vite/React/TypeScript/Tailwind 与 Nova/Base UI 本地原语，记录真实
  包管理器和运行命令。
- [x] 实现运行时 API 解码、同源 Cookie 会话、内存 CSRF、错误映射、路由与响应式壳层。
- [x] 实现 Overview、Intelligence、Tasks、Research、Runs 以及证据/处理详情交互。
- [x] 补充最小行为测试，验证会话边界、证据追溯和恢复确认。
- [x] 更新前端规范、quickstart 和验收记录，逐项核对 PRD AC。

## 实际验证

- 在测试服务器 `/tmp/stocks-v1-08-web-console.B5LbjV` 执行
  `npm ci --ignore-scripts`、`npm run typecheck`、`npm run test` 和
  `npm run build`，四项均成功；最终 6 个测试文件中的 11 个行为测试全部通过。
- `npm audit --omit=dev` 返回 0 个生产依赖漏洞。`react-router-dom` 已锁定
  `7.18.4`。
- 生产构建主包 452.88 kB（gzip 142.58 kB），页面路由产物独立按需加载。
- 使用系统 Chromium 和同源受控 fixture 执行桌面 1440×1000、手机 390×844
  browser smoke。最终 23 项断言全部通过，覆盖会话恢复、已登录访问登录页跳转、
  未登录跳转与登录回跳、手机
  键盘导航、无横向溢出、语义检索与详情、报告 HTML 纯文本、旧引用版本、原件入口、
  Run 恢复控件和 Processing 重试；浏览器控制台无错误。
- StrictMode 首次浏览器 smoke 暴露了会话恢复永久停在 loading 的真实缺陷。修复为
  可重放恢复边界，并新增 StrictMode 回归测试；修复后远端重跑通过。
- 真实 API 数据链路由受控同源 fixture 替代；真实 Provider/S3、真实手机视觉、
  跨设备登录、目标 ARM 和公网部署未在本任务验收，留待 V1-09 或 Owner 验收。

## AC 对照

- AC-1：通过。报告正文、实际引用片段/版本、当前采集指针和安全原件入口分开展示；
  手机为单列可操作布局，browser smoke 验证 390px 无横向溢出。
- AC-2：通过。成功空结果、加载失败、解析/索引未完成、部分内容、来源错误分别展示；
  语义查询不依赖股票标签，精确模式支持代码/指标。
- AC-3：通过。Runs 显示失败步骤与模型安全错误，恢复按钮遵循服务端
  `actions`；Processing 可筛选失败阶段并重试，`created=false` 和
  `accepted=false` 均显示服务端状态。实际重试需真实 Worker/Provider，浏览器 smoke
  只验证受控请求边界。
- AC-4：通过。类型检查、生产构建、行为测试和浏览器交互断言均实际执行，未用纯截图
  替代行为验证。

## 收尾

- [x] 逐项记录 PRD AC 的实际结果、未完成和外部限制。
- [x] 契约变更只更新唯一归属文档，运行能力更新 quickstart。
- [x] 检查后续依赖与阶段验收；保留原始证据、输入快照及最后成功结果。
- [x] 完成实现/质量检查后按 Trellis 工作流交付。
