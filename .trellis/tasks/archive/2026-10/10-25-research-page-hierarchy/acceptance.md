# 验收记录

## 结论：三层分层完成并已部署生产（md5 核对 + 容器内核验）

## 实现

| 层 | 内容 |
| --- | --- |
| 第一层（始终可见） | 标题+状态+生成时间；**信任摘要行「独立原件 N · 引用 M 条」**；缺口单条 warning Alert（仅非空）；截断 Alert（仅截断）；正文 Markdown（75ch） |
| 第二层（Tabs，懒挂载） | 证据包（默认）/ 来源与指标 / 研究用量；未激活 Tab 返回 null，**零请求** |
| 第三层 | 采集指针移至 Runs 详情 Sheet（仅当 Run 有报告时懒取报告详情；空数据不渲染） |

**对照独立入口**（Owner 确认拿出去）：报告列表每行加「对照」切换（aria-pressed，
不嵌套按钮），写入 `?compare=<otherReportId>`；阅读列此时渲染对照视图（选择框 +
警告 + 双侧 + 结论/资料/指标差异），带「返回报告」。深链接 `?report=X&compare=Y`
可用；选择其他报告清除 compare。对照 API 仅在对照模式请求。

## 交互语义保留（审查确认）

`?report=` 选择、最新成功报告自动选中 effect（含 matchMedia 守卫）、aria/键盘、
证据/缺口展示口径逐项比对无回归；无 API 变更、无新依赖。

## 审查发现并处理

[P3] 对照目标「同 Task 优先」规则失去回归保护（fixture 全部同 task_id）→
新增不同 Task 的更新报告 fixture + 断言仍选同 Task 报告的回归用例。

## 验证

| 检查 | 结果 |
| --- | --- |
| 远程 typecheck / vitest / build | 退出码 0，**10 文件 41 passed**（新增同 Task 优先回归） |
| 生产同步 | 5 文件 + 测试文件 md5 一致 |
| web 镜像重建 + 容器强制重建 | healthy，/health 200 |
| 容器内核验 | research 分包含「来源与指标」等新 Tab 标记，不含「当前采集指针」；该标记现位于 runs 分包 |

## 限制

真实浏览器视口视觉验收属 PA-3 待办。

## 补充：Owner 报告「未更新」的排查结论（2026-10-25 晚）

Owner 换浏览器后仍报旧版。逐层排查结论：**服务端与 CDN 链路全部是新版**——

- index.html 从 `100.120.14.84:8080` 与 `127.0.0.1` 拉取 md5 一致，引用 `index-aw_uKa5Q.js`
- 该主包引用 `research-page-PDlxhLfG.js`（含「支撑信息」Tabs 标题）
- 从 Mac 与服务器双端下载 chunk md5 均与容器内一致
- 无第二个 web 容器/旧栈抢占 8080；`Cache-Control: no-cache` 正常

期间为排除 Docker layer 缓存做了一次 `build --no-cache`，并在正文与 Tabs 之间补了
「支撑信息」小节标题（提交 f9a0ec4），使 Tabs 区块在视觉上有明确分区。

**唯一无法服务端排除的剩余变量是 Owner 浏览器本地状态**：请用无痕窗口测试，或在
开发者工具 Network 勾选 Disable cache 后刷新，并核对 research-page chunk 文件名
是否为 `research-page-PDlxhLfG.js`。若该文件名已是 PDlxhLfG 而界面仍像旧版，
请描述具体看到的样子（有没有三个 Tab），那将指向渲染问题而非部署问题。
