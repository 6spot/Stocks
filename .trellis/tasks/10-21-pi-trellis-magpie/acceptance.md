# 配置交付与待验收记录

日期：2026-10-21。收尾决定：按用户明确的“归档提交”指令交付并归档当前配置版本。用户先批准配置实施，随后明确只绑定其创建的三个路由组；当前记录对应最终组方案。**配置及反馈改动已完成，运行验收未执行；归档不表示 AC 全部通过。**

## 当前配置

| 角色 | 引用 | 生效位置 |
| --- | --- | --- |
| Planner 主会话默认 | `magpie/group/openai` | 项目 settings 的 defaultProvider / defaultModel |
| Implement | `magpie/group/deepseek` | 原生 Agent frontmatter |
| Check | `magpie/group/qwen` | 原生 Agent frontmatter |
| Review | 未创建 | 如需添加，使用 OpenAi 组并先核对权限 |

具体型号由 Magpie 管理；没有单模型选择 TODO。项目默认组不强制切换已有 / 恢复会话，当前会话可通过 /model 自主选择。

## 实际改动

- `.pi/settings.json`：保留原有资源设置，添加项目默认 magpie Provider 和 group/openai。
- `.pi/agents/trellis-implement.md`：使用 Deepseek 组，移除单模型候选注释。
- `.pi/agents/trellis-check.md`：使用 Qwen 组，保留原生可写修复职责说明。
- `.pi/extensions/trellis/index.ts`：对 Implement / Check 要求显式完整 Magpie 组，精确查询实际将传给子 Pi 的组引用，在缺省、错误类型或组未注册时拒绝派发。工具级覆盖同样只接受组。
- `.trellis/model-routing.md`：更新组配置索引、换模职责、加载与任务操作、验证和更新风险。
- `.agents/skills/trellis-start/SKILL.md`、`trellis-continue/SKILL.md`：修正共享平台提示，避免 Pi 因模型名称而使用 Codex 的 phase 指令。
- `.pi/prompts/trellis-continue.md` 及扩展的工具 promptGuidelines：明确 SSH 环境探测、构建、测试和实施由 Implement 承担，主会话保留审阅 / 启动 / 协调。不是权限拦截或自动调度。
- 当前任务 PRD / design / implement / research：同步用户明确修正和当前状态。

没有改动业务代码、其他任务、原生生命周期或全局配置，没有修改 Magpie Provider / 路由组 / 认证，也没有新增 Agent、依赖、服务或调度器。

## 实际完成的只读调查与静态复核

- 从 Magpie groups 和 Pi 导出清单找到三个实际组 ID，确认引用注册，当前成员分别符合 DeepSeek / Qwen / OpenAI 家族。没有把组名当作唯一依据，没有输出密钥或账户身份。
- 阅读 Agent 和 settings 及完整 tracked diff，确认原生 Agent 正文 / 工具集合保留；Check 仍可编辑和修复，并非只读。
- 静态追踪共同入口中的检查及三个派发分支；无显式组时拒绝发生在子进程创建前，不依赖当前主会话模型。Research 保留原流程。
- 原生任务 / JSONL 上下文加载、任务创建和 start 分离机制未改动。
- 配置索引明确 Pi 记录组、Magpie 决定最终具体模型，更新后需 reload，启动默认值不会自动改变现有会话。

这些是文件阅读与代码路径审阅，不是运行通过结果。

## 运行验收限制

此前尝试：

```sh
ssh -o BatchMode=yes -o ConnectTimeout=8 root@100.120.14.84 'command -v node; command -v pi; command -v magpie'
```

退出码 255：`Permission denied (publickey,password)`。远端命令未执行，没有远端目录或资源残留。组调整本轮未重试，也没有在本机运行测试，遵守项目唯一测试服务器规定。

未执行：Pi registry 命令、task validate、编译 / 类型 / 格式检查、缺组与直接模型引用拒绝测试、合法组派发 / override / thinking / 三模式测试、主会话默认加载验收、Research 回归、网关 HTTP healthcheck、真实 API 或 Subagent 启动。

本轮没有额外模型测试 token 消耗。新版扩展尚未在当前会话 reload，用户使用前需 reload 或重启；当前会话要切至 OpenAi 组需通过 /model 选择。

AC-1、AC-3、AC-4、AC-6、AC-7 的配置和说明已作静态复核；AC-2、AC-5 的运行行为及真实转发仍待验收，不将全任务标为验证通过。

## 用户会话回溯

只读检查会话 `01a15c10-e070-7777-95aa-8e4fd8a7d762`：用户要求执行下一任务后，OpenAi 主会话确实执行了 `ssh ... 'uname -m'` 环境探测，且使用过错误的 `--platform codex` 指令；截至读取快照没有 start / trellis_subagent 或业务代码改动。问题表现为主会话没有及时派发，不能描述成用户没看见任何执行，也不能说已发生子 Agent 模型回退。

已修正上述平台与执行操作委派提示；诊断详见 `research/session-routing.md`。未重放该会话、未启动 V1-01、未调用模型来验证新提示。主会话底部保持 OpenAi 与 Deepseek 子 Agent 是否运行是两项不同观察。

## 子进程反馈补丁与新增验收

用户进一步反馈子进程遇到问题缺少反馈。只读回溯会话 `01a15c24-fd56-7747-9cbd-bc09b7f0e7e9`，观察到实际 Deepseek / Qwen 派发，以及两次约 97 / 92 分钟的 Qwen 调用；大量工具操作说明不能仅凭时长断言卡死。详见 `research/progress-feedback.md`。

本轮在同一 Pi 扩展中补齐公开进度 / 工具耗时 / 失败摘要 / retry / compaction / 静默刷新，注入阻塞退出要求，修正 agent_end 和退出码 0 的成功误报，以及较早进度文字遮盖失败原因的问题。没有改 Agent 正文、角色组、业务代码、任务生命周期或并发业务会话；没有自动中断正在运行的子进程。

已准备 `verification/progress.test.mjs`，使用实际扩展副本和无网络子进程替身。用例尚未运行；没有本机语法 / AST / 编译 / 类型 / 格式检查或运行测试，也未 reload 验证卡片。模型遵守阻塞提示的实际效果仍需后续运行观察。

本轮实际执行：`ssh -o BatchMode=yes -o ConnectTimeout=8 root@100.120.14.84 'command -v node'`，退出 255，认证失败，远端命令未执行；没有远端目录或资源残留，没有真实模型测试消耗。AC-8 仅完成代码阅读复核与回归材料准备，运行验收尚未执行。

历史会话的角色组记录是已有运行观察，不代替本轮补丁测试，也没有核对 Magpie 最终 served 模型。

## 用户要求的归档交付

用户在得知反馈补丁尚未运行验证后明确要求“归档提交”。本次将配置实现、进度反馈修复、离线回归材料和诊断记录一并提交，并通过原生 task.py 归档；未执行项保持原状，不将配置任务的 completed 状态当作运行验收成功。

最新测试规范区分 SSH 认证与网络可达性：此前 BatchMode 退出 255，只说明本命令未提供可用认证方式。本配置会话没有可用的临时密码凭据，未做密码认证验证，也不能据此断言测试服务器不可达。本轮按用户收尾指令归档当前未验证版本，不新增测试资源。

待后续具备认证条件后，在指定服务器隔离目录执行反馈回归、组派发边界 / 设置加载测试和实际转发核对。本次提交范围仅为本任务集成和任务材料；并发 V1-01 的代码、规范与归档由其会话负责。

## 后续与规范归属

恢复服务器访问后按 implement.md 做最小离线边界测试；已有可访问网关与授权认证时再说明消耗并做最小组调用，对照 Pi 组引用和 Magpie 最终 served 记录。不公开网关或复制真实凭据来绕过环境限制。

集成契约、错误矩阵、正反示例和测试点已统一保留在 `.trellis/model-routing.md`；不在业务 backend / frontend 规范或 AGENTS 重复同一内容。Trellis 更新需保留 / 合并项目设置、Agent 和扩展，不承诺 force 更新保留配置。
