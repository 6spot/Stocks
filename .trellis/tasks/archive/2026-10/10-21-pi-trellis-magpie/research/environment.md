# 环境与组绑定调查

调查日期：2026-10-21。用户补充创建了 Qwen、Deepseek、OpenAi 三个角色组，并明确只绑定组，具体型号由 Magpie 管理。本记录更新为当前方案，取代早先的单模型候选方案；它是只读配置 / 文档调查，不代表模型调用成功。

## 版本与接入

- Pi 1.3.0、Trellis CLI 0.6.17：CLI 版本输出。
- Magpie 0.1.1306：应用元数据；应用内可执行文件支持官方 CLI，未在 PATH 暴露命令。
- Trellis 与 Pi 集成已经存在，包含原生 Agent、settings、extension 和 prompts，无需初始化。
- Pi 已有 magpie Provider。地址从 Pi agent-dir 的 models.json 读取，调查时为 loopback 网关，进程与监听信息已观察；项目不固定地址或端口。
- 只筛选 Provider API 类型、模型 / 组 ID 与显示名，未输出或复制认证值、账户身份、对话内容。

## 用户创建的角色组

来源：`~/.config/magpie/providers.json` 的 groups；Pi 条目来源：agent-dir 的 models.json 中 providers.magpie.models。

| 显示名称 | 实际 ID | Pi 完整引用 | Pi 配置 API | 当前成员检查 |
| --- | --- | --- | --- | --- |
| Deepseek | deepseek | `magpie/group/deepseek` | openai-completions | 5 个成员，来自不同来源的 DeepSeek 模型 |
| Qwen | qwen | `magpie/group/qwen` | openai-completions | 1 个成员，Qwen 家族 |
| OpenAi | openai | `magpie/group/openai` | openai-responses | 1 个成员，OpenAI GPT 家族 |

三个组均实际存在，并已导出到 Pi；当前未发现跨角色家族混入。项目只绑定表中组引用，不固化具体成员。成员、账号、API 元数据及配额变化由用户和 Magpie 管理；清单存在不证明运行可用。

其他旧组仍存在且成员混合，不用于角色绑定，也未修改。全局启动值不由本任务改写；项目 settings 设置 OpenAi 组作为新会话默认，保留用户在现有会话自主切换。

## 原生机制与本地检查

- Agent frontmatter 支持 model / thinking / tools；模型优先级是工具级显式 override > frontmatter > 主会话。新增检查对 Implement / Check 禁止缺省继承，且要求完整组引用。
- 单一 runSubagent 入口覆盖 single / parallel / chain。检查 registry 的精确组引用后才 spawn；不管理成员、不按名称判断角色、不引入意图模型或调度器。
- Pi 1.3.0 公开同步 `ctx.modelRegistry.find(provider, modelId)`；本地检查使用它确认 `magpie` / `group/<id>`，不发出模型请求。
- isTrellisAgent 通过 .pi/agents 定义存在性识别，无需新注册服务。
- buildPrompt / buildContext 保留原生任务、JSONL 和规划材料加载，派发第一行仍须 Active task。
- Check 原生 tools 含 write / edit / bash，职责包括直接修复，不是只读审计。仅限制 tools 也不等于 OS 隔离；本任务不创建 Review。
- fallbackModels 字段在当前扩展被解析但不被执行逻辑消费，不能宣传其实现角色回退。
- 旧会话需要 reload 才加载磁盘扩展；设置启动默认值不强制切换恢复会话。

## 更新保留与官方依据

已只读核对 Trellis Pi configurator 和 update 实现，Pi Agent / settings / extension 属模板管理文件；当前仅发现 Codex 专门的模型字段保留调用，不能保证 Pi 在 force 更新后保留本地绑定。使用官方 dry-run、保留本地或 create-new 合并，不手改 template hashes。

已完整阅读当前安装 Pi 的 README、models / configuration / settings / cli / extensions / security 文档，核对 model-registry 类型与 permission-gate 示例。Trellis 依据为本地 workflow、trellis-meta 引用和 CLI 帮助。

Magpie 依据为安装 CLI --help、实际配置及官方 [README](https://github.com/yetone/magpie/blob/main/README.md)；[Reference](https://github.com/yetone/magpie/blob/main/docs/reference.md) 为后续查询入口。在线 main 可能领先安装版本，以实际 CLI / 配置为准。

## 验证状态

未运行 Pi registry 枚举、healthcheck、编译、类型、格式、离线派发或真实 API / Subagent 专项测试。项目仅允许指定服务器验证；此前 SSH 认证失败，不回退本机。后续用户会话中已有实际角色组派发观察，但不代替本轮验收；本轮再次 SSH 探测仍认证失败。新增进度反馈问题、修复范围和待运行回归见 `progress-feedback.md`，实际限制与验收记录见 `../acceptance.md`。
