# 技术设计

## 分工与配置

用户已明确将原先的单模型候选方案修正为三个路由组。Pi 的 model 字段引用 Magpie 的 `group/<id>` 虚拟模型；具体型号和来源完全由 Magpie 组管理，不在项目固化。

| 角色 | 配置 |
| --- | --- |
| Planner | `.pi/settings.json`：`defaultProvider: magpie`、`defaultModel: group/openai` |
| Implement | `.pi/agents/trellis-implement.md`：`model: magpie/group/deepseek` |
| Check | `.pi/agents/trellis-check.md`：`model: magpie/group/qwen` |
| Review | 不创建；以后明确启用时使用 OpenAi 组 |

组 ID 来自 Magpie 实际配置，三个引用已导出到 Pi，当前成员符合对应家族。复用 Provider，不改全局设置或 Magpie。项目默认值只影响启动选择，不强制切换已有 / 恢复会话；当前会话通过 `/model` 自主选择组。

## 派发边界

行为边界在 `.pi/extensions/trellis/index.ts` 的共同派发入口：

1. 保留原生解析和优先级：工具级 model > Agent frontmatter；只有 Implement / Check 必须有显式组，不继承主会话或子 Pi 默认值。
2. 对原生解析后的实际子 Pi 引用分离 thinking 后缀，要求 `magpie/group/<非空组ID>`，具体模型引用或模糊名均拒绝。工具级覆盖遵循同一限制。
3. 通过当前 Pi 同步 registry 的 `find('magpie', 'group/<id>')` 精确确认；registry 不可读取或未注册时在创建子进程前抛出错误。
4. single / parallel / chain 均从共同入口通过；Research 等其他 Agent 的模型选择保持原生行为。
5. 扩展不读取或管理组内成员、不硬编码组名与家族。具体型号、重试、配额和同家族来源选择由 Magpie 管；不添加意图识别来决定角色。

存在性检查不证明凭据、配额、上游可用性或实际启动成功；Pi 记录组引用，最终具体模型以 Magpie Routing / Usage 为准。

## 改动范围

- `.pi/settings.json`：增加主会话项目默认组，保留原有资源设置。
- 两个原生 Agent：只改 frontmatter 的组绑定与说明，正文 / 工具不变，移除单模型候选 TODO。
- 原生 Trellis 扩展：在现有最小派发检查中要求组引用，保留上下文加载及工具 override 机制；工具提示明确实际平台为 Pi，环境探测等执行操作交给 Implement。
- 共享 start / continue 技能：按实际宿主选择 platform，不写死 Codex；继续执行提示明确使用 Pi 原生派发。
- Pi 专用 continue prompt：同步环境探测 / 构建 / 测试 / 实施的派发边界。
- `.trellis/model-routing.md` 和本任务材料：同步当前组契约及验收限制。

本配置任务由主会话处理，配置后业务任务回到原生 Implement / Check。无 Planner / Review 子 Agent、额外依赖、常驻脚本或业务变更。

## 用户操作

规划：主会话默认 OpenAi 组，可随时 `/model` 切换。创建与拆解多个任务后只保存 planning，不自动 start。

执行：用户指定一个已规划任务并批准，主会话审阅后 start，通过 Agent frontmatter 派发 Deepseek 组的 Implement，再按原生工作流使用 Qwen 组 Check。主会话不在派发前自行进行 SSH 环境探测、构建、测试或实施；这些同属 Implement 职责。显式用户指令优先。不自动连续执行其他任务。

主会话保持自己的 OpenAi 模型协调。组绑定不会自动拦截主会话 bash 或替它切模型；派发指引是原生工作流要求，不是权限隔离。

换具体模型：在 Magpie 调整对应组，组 ID 不变时项目无需修改；若需更新 Pi 导出条目或 API 元数据，沿用现有 Magpie Pi 集成同步。换组 ID 时才改项目设置 / Agent。

## 进度与阻塞反馈

继续复用 runPi / applyEvent / 原生 onUpdate 卡片，不增加工具、依赖、常驻服务或任务状态。

- 按 Pi 1.3 JSON 事件展示公开 text 进度、当前工具和耗时、工具失败摘要、自动重试 / 压缩状态。tool_execution_update 更新活动时间；stderr 触发可见更新。诊断限制长度并遮蔽常见凭据字段；不显示 thinking 原文或完整工具结果。
- 每 5 秒刷新耗时和最后事件时间，60 秒无子事件时显示静默提示。计时仅报告观察，不宣称仍有实质进展，也不自动杀掉长任务；退出 / 取消后清理 timer。
- agent_end 仅表示低层运行结束；agent_settled 与进程退出才用于最终状态。模型 error / aborted 或明确阻塞报告，即使 CLI 退出 0 也返回失败；失败理由优先于较早的进度文本。
- 在原生 buildPrompt 注入简短反馈约定：阶段变化和可恢复失败时公开说明；无法解除的环境 / 权限 / 输入阻塞，最终回复第一行 `BLOCKED: <原因>`，列出已完成、未完成与缺少条件后结束，不在 headless 子进程等待用户。此显式报告作为失败交回主会话并停止 chain，普通可恢复工具错误不立即终止任务。
- 主会话收到阻塞后解释原因和后续选项，不用相同缺失条件反复派发。保留原生任务 review/start gate、组绑定与 Agent 正文 / 工具。

这是本轮新用户反馈的修复范围。当前已运行的其他会话不被中断；它们需要结束当前调用并 reload 后才会使用新逻辑。

## 更新、权限和验证

Check 原生可以修改文件，不是只读审计。可选 Review 尚未创建，不声称工具清单等于操作系统隔离。

本任务涉及的设置 / Agent / 扩展 / 技能 / prompt 均可能受到 Trellis 模板更新影响。先 dry-run，保留本地修改或通过官方 `.new` 合并，重新核对组与派发检查，再 reload；不改 hashes。

回滚只撤销本任务新增块，保留用户原有设置；不重置整个文件、不改 Magpie 或清空任务。回滚检查会恢复原生继承行为，应同时更新说明。

项目只允许在指定测试服务器隔离目录运行验证；此前 SSH 认证失败，不在本机回退，也不公开 loopback 网关或搬运真实认证。运行测试和真实转发均未验证。真实 smoke 会消耗额度，需先说明。
