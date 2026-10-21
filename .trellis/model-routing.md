# Pi / Trellis 路由组配置索引

Magpie 管理组内具体模型、Provider、认证和转发，Pi 运行会话与子进程，Trellis 保持原生任务流程。项目只绑定路由组，不固定组内模型。此文件是索引，不参与运行配置解析。

## 角色绑定

| 角色 | 完整 Pi 引用 | Magpie 分组 | 实际配置位置 |
| --- | --- | --- | --- |
| Planner（主会话） | `magpie/group/openai` | OpenAi，组 ID `openai` | [.pi/settings.json](../.pi/settings.json)：`defaultProvider: magpie`、`defaultModel: group/openai` |
| Implement | `magpie/group/deepseek` | Deepseek，组 ID `deepseek` | [trellis-implement.md](../.pi/agents/trellis-implement.md) 的 `model` |
| Check | `magpie/group/qwen` | Qwen，组 ID `qwen` | [trellis-check.md](../.pi/agents/trellis-check.md) 的 `model` |
| Review（可选） | 未启用；以后如需启用，使用 `magpie/group/openai` | OpenAi | 当前没有 Review Agent |

三个组的实际 ID、Pi 导出条目及当前成员家族已通过只读配置核对，无待选择具体模型的 TODO。用户会话中已观察到 Deepseek / Qwen 组的实际子进程记录；本配置任务的专门运行验收及本轮反馈补丁仍未完成。详细依据见[环境调查](tasks/archive/2026-10/10-21-pi-trellis-magpie/research/environment.md)和[进度反馈诊断](tasks/archive/2026-10/10-21-pi-trellis-magpie/research/progress-feedback.md)。

Planner 负责需求、方案、任务拆解和验收；Implement 负责实现及必要测试；Check 按原生流程检查并直接修复。**Check 拥有 `write`、`edit`、`bash`，不是只读独立审计。**

## 接入与模型管理

复用 Pi 已有的 `magpie` Provider，不添加上游 API key，不修改 Magpie Provider、订阅账户、路由组或其他 CLI 全局配置。

- Pi Provider / 导出条目位于 Pi agent-dir 的 `models.json`，默认 `~/.pi/agent/models.json`；自定义目录由 `PI_CODING_AGENT_DIR` 指定。
- 网关地址来自该文件的 `providers.magpie.baseUrl`，项目扩展不写死地址或端口。
- [项目设置](../.pi/settings.json) 保留原有扩展、prompts 和技能配置，仅增加主会话默认 Provider 与组。
- 现有其他混合路由组不用于这三个角色；不使用 Magpie 意图选模来决定角色。

日后更换具体模型，只需在 **Magpie 中调整对应路由组**。保持组 ID 不变时，无需改 Agent 或重新初始化 Trellis。若 Magpie 调整组后需要刷新 Pi 导出清单或 API 元数据，沿用现有 Magpie Pi 集成同步，再通过 `/model` 核对组条目。保持各组成员和下游回退符合该角色需求。

若更换组 ID，则修改上述对应设置或 frontmatter；角色不在代码中绑定模型家族。不要把显示名称的大小写当作 ID：显示名为 OpenAi，实际引用是 `group/openai`。

## 加载与主会话切换

修改项目配置后，在 Pi 执行 `/reload` 或重启，加载扩展和资源改动。**启动默认值不等于强制切换已打开或恢复的会话**；当前会话要立即使用 OpenAi 组，请通过 `/model` 选择 `magpie/group/openai`。主会话以后仍可随时切换。

新会话使用项目默认 OpenAi 组，不修改全局启动设置。不要通过保存全局默认值来代替本项目配置。

## 派发契约与错误行为

原生工具为 `trellis_subagent({ agent, prompt, mode?, prompts?, model?, thinking? })`。prompt 第一行仍须为 `Active task: <task.py current 返回路径>`。

对 Implement / Check，非空工具级 `model` 优先于 Agent frontmatter `model`，但**两种入口都必须是已在 Pi 注册的完整 `magpie/group/<组 ID>`**。普通派发应省略工具级 `model`，让 Agent 的角色组生效。不要用具体模型覆盖组。

| 条件 | 行为 |
| --- | --- |
| 没有显式组 | 报错，不创建子进程，不继承主会话或子 Pi 全局默认值 |
| 具体模型引用、模糊名或非 Magpie 组引用 | 报错，要求完整组 ID |
| Pi registry 不可读取或组未精确注册 | 停止派发，不换其他组 |
| 有效组，可含原生 `:thinking` 后缀 | 按原生 single / parallel / chain 派发，保留任务 / JSONL 上下文 |
| Research 等其他 Agent | 保持原生继承行为 |

检查使用同步 `ctx.modelRegistry.find`，核对实际将传入子 Pi 的引用，本身不发出模型请求。它检查组的显式选择和注册，不读取组成员、不判断投资或工程意图、不管理组内具体模型或配额。

错误示例：把具体模型写入 Implement 的 `model`，或只写显示名 `Deepseek`。

正确示例：`model: magpie/group/deepseek`，后续在 Magpie 管理这个组的模型。

边界仅覆盖本项目的该扩展派发，不约束用户手动启动其他 CLI，也不构成操作系统隔离。Magpie 组内选路和实际来源以其 Routing / Usage 为准。

## 规划与执行

**规划保存：** 主会话使用 OpenAi 组，通过已有 `/trellis-start`、`/skill:trellis-brainstorm` 或直接要求“只规划、拆解并保存任务，完成后停止”。可保存多个 `planning` 任务；规划结束不调用 Implement、不自动 `start`。

**自主选择执行：** 用户明确指定某个已规划任务并批准实施，主会话按原生流程审阅材料、执行 `task.py start`，再派发 Deepseek 组的 Implement 和 Qwen 组的 Check。未获明确指令时，不连续执行其他任务。明确的“提交并推送，然后开始下一个任务”覆盖唯一确定的下一项，归档或补齐规划不撤销该执行授权；细则归属 [workflow.md 的 Authorization Carryover](workflow.md#authorization-carryover)。实际重复确认的[诊断记录](tasks/archive/2026-10/10-21-pi-trellis-magpie/research/authorization-handoff.md)已保留。

主会话继续使用 OpenAi 协调，不会因 start 自动变成 Deepseek。SSH 环境探测、构建、测试和代码实施都应交给 Implement；主会话不要在派发前自行完成这些执行操作，除非用户明确指定由主会话做。判断是否已用 Deepseek，应看 `trellis_subagent` 的 Agent 和子进程组记录，不能只看主界面模型标签。

共享 start / continue 技能需要按实际宿主选择平台：Pi 使用 `--platform pi`，不能因为模型是 GPT / Codex 就选择 `--platform codex`。相关[会话诊断](tasks/archive/2026-10/10-21-pi-trellis-magpie/research/session-routing.md)记录了实际观察。

这些派发提示沿用 Trellis 的指令工作流，不是阻止主会话使用 bash / edit 的权限隔离；显式用户指令优先。

原生命令：

```sh
python3 ./.trellis/scripts/task.py list
python3 ./.trellis/scripts/task.py current --source
```

`task.py start <选定任务目录>` 是开始实施的明确操作。`/trellis-continue` 按当前状态继续，不代表自动批准实施。任务状态、finish 和 archive 继续遵循 [workflow.md](workflow.md)。

## 子进程进度与阻塞

主会话同步等待 `trellis_subagent` 返回；子 Agent 在 headless 模式里提问，不会自动形成与主会话的实时对话。公开进度通过原生工具卡片显示，确需外部输入时必须结束本次调用，交回主会话处理。

- 卡片显示公开进度、当前工具及耗时、失败摘要、模型重试次数 / 等待和上下文压缩状态。折叠视图也保留失败原因；Alt+O 展开更多工具记录。新增诊断限长并遮蔽常见凭据字段，不展示 thinking 原文或完整工具结果。
- 每 5 秒刷新时间；60 秒未收到子事件时明确显示静默时长。静默提示是观察，不证明死锁，不会自动终止长任务。需要中断时可用 Pi 的 Esc 取消当前调用；已有文件改动可能保留。
- 子 Agent 遇到无法自行解除的凭据、权限、环境或用户决策阻塞时，最终回复第一行使用 `BLOCKED: <原因>`，列出已完成 / 未完成 / 所需条件，然后结束。该显式报告作为失败交回主会话，chain 不继续后续步骤；主会话说明阻塞，不在条件未改变时反复派发。
- Pi 1.3 的 agent_end 只结束一次低层运行，可能仍会自动重试。进程退出后结合最终模型状态和报告判断；退出 0 的模型 error / aborted、截断或缺少最终报告也不能显示成功。错误原因优先于较早的进度文本。

反馈约定由 buildPrompt 注入，保留原生 Agent 文件和工具。它依靠模型遵守阻塞退出要求，不是自动识别所有阻塞的监控系统。正在运行的子进程不热加载这些修改；结束 / 取消当前调用后 reload，后续派发才使用新逻辑。

本轮已准备无真实模型的[离线回归用例](tasks/archive/2026-10/10-21-pi-trellis-magpie/verification/progress.test.mjs)，尚未运行。相关证据和限制见[诊断记录](tasks/archive/2026-10/10-21-pi-trellis-magpie/research/progress-feedback.md)。

## 可选 Review

当前没有 Review Agent，也没有强制第二审查。用户明确需要时再使用现有自定义 Agent 机制配置，引用 OpenAi 组。启用前应核对只读工具、扩展与 MCP；只去掉 `edit` / `write` 不能保证其他工具无法写文件，不宣称可靠只读隔离。停用时停止派发，必要时移除该自定义定义。

## 验证与限制

三个组已在 Magpie 配置和 Pi 导出清单中找到，当前成员分别符合 Qwen / DeepSeek / OpenAI 家族。配置和完整 diff 已作静态阅读复核，这不等于运行测试通过。

实际转发核对分两层：Pi 的组引用应是 `magpie/group/…`；Magpie Routing / Usage 的 requested / sent / served 记录显示该组最终使用的具体模型。具体型号变化是组的职责，不能要求 Pi 固定它。不要用模型自述代替证据。

真实最小 smoke 会消耗 tokens / 订阅额度，应在执行前说明，每个角色先做一次简短且禁止改文件的测试。

本项目所有运行验证仅允许在指定测试服务器的任务隔离目录执行，详见[测试环境规范](spec/backend/testing-guidelines.md)。本轮再次以 BatchMode 探测 Node，SSH 认证失败、退出 255，远端命令未执行，也未回退本机测试。**尚未运行**配置专项验收及进度反馈回归、Pi registry 命令、网关 HTTP healthcheck、编译 / 类型 / 格式检查或本轮真实模型 smoke；用户既有会话的实际派发记录单独列为历史观察。

后续测试重点为：缺组时零子进程、直接模型引用被拒、精确组 ID 传入子 Pi、合法组覆盖、三种模式、thinking 后缀、Research 继承和原生上下文完整。配置任务的[验收记录](tasks/archive/2026-10/10-21-pi-trellis-magpie/acceptance.md) 保留实际结果与限制。本配置版本按用户明确要求归档提交；运行验收仍未执行。

## Trellis update

当前 Trellis 0.6.17 未发现 Pi 模型字段专用保留机制。workflow、Agent、项目设置、扩展、共享技能和 Pi prompts 均在模板管理范围，强制更新可能覆盖授权承接规则、组绑定、平台 / 派发提示或派发检查。

先用 `trellis update --dry-run` 看差异，保留本地修改，或用 `--create-new` 生成 `.new` 后合并。不要手改 `.template-hashes.json`。更新后复核项目默认组、两个 Agent 组和派发检查，再 reload。

复用到其他项目时，重新检测其实际网关、组 ID 和工具版本；保持相同角色分工，不复制上游凭据。
