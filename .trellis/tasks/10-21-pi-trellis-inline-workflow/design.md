# 技术方案

## 边界

撤回此仓库 `.pi/` 路径的 Trellis 子进程派发，改成 Pi 主会话直接完成实施与检查；同时删除项目级 `.opencode/` 集成目录。保留 Pi 扩展中派发以外的 session/task context、workflow breadcrumb 与相关持久化更新；保留项目默认主模型和 Trellis 任务系统。其他平台继续采用现有 Agent 流程，共享 Trellis CLI 仍保留 OpenCode 通用适配。

不使用 `trellis init`。当前 CLI 的 Pi 模板本身包含子进程工具；re-init 的平台快速路径跳过已配置 Pi，force full-init 又可能覆盖用户 workflow/spec。定向修改能保留任务、规范和上下文功能，范围更小。

## 本地契约调整

1. **Pi 扩展**：停止注册 `trellis_subagent`；删除只服务于该工具的子 Pi 进程启动、single/parallel/chain 编排、进度状态/卡片、`Alt+O` 快捷键和子代理失败回传处理。保留无关的上下文注入事件与 session 消息机制。
2. **Pi 角色配置**：移除 Implement/Check 文件中的 DeepSeek/Qwen model frontmatter 和派发专属说明；不改 OpenAI 项目默认模型。
3. **Pi 执行路由**：将 `.agents/skills` 与 `.pi/prompts` 中的 Pi 指引改为主会话使用 `trellis-before-dev` 读取任务/spec，再直接实施和自检。其他平台的 sub-agent 流程维持原有行为。
4. **共享工作流**：在 `.trellis/workflow.md` 的 Pi 平台路径上明确 inline，避免通用 Phase Index 覆盖 Pi-only 指引。JSONL manifests 对其他 dispatch 平台继续有效；Pi inline 不依赖派发专用流程。
5. **项目说明**：更新 `.trellis/model-routing.md`，记录 Pi 主会话模式、OpenCode 集成移除范围、保留范围和验证限制。
6. **OpenCode 项目文件**：删除 `.opencode/` 目录；不手工编辑 `.trellis/.template-hashes.json`，相关模板记录由后续 Trellis 更新流程处理；不改共享 Trellis CLI 的 OpenCode 适配或其他平台文件。

## 数据与兼容

- 不迁移、删除或重写 `.trellis/tasks/**`、`.trellis/spec/**`、业务代码及 V1-02 并发改动。
- 不更改 `.pi/settings.json` 的项目扩展加载入口、主会话默认组、global Pi/Trellis 配置或安装源码。
- 不删除共享 Trellis CLI 的 OpenCode 通用适配；仅移除当前仓库的 `.opencode/` 项目集成。
- `.trellis/.template-hashes.json` 按 Trellis 规范保留原样；遗留 OpenCode 模板记录待 Trellis update 管理。
- 移除 Pi 子进程后，Pi 内的实现和检查由主会话负责；SSH、构建、测试等仍按仓库环境规则在指定测试服务器执行。

## 验证策略

按用户对撤回子进程机制的要求，不用子代理完成本任务。源码检查覆盖目标扩展中派发注册、子 Pi 进程路径和派发专属快捷键已移除，context 注入仍保留；另确认 `.opencode/` 目录已删除且 `.trellis/.template-hashes.json` 未手工修改。不得在本机运行语法、编译、测试或格式检查。若需要运行检查，只能在测试服务器任务隔离目录执行；未能访问服务器时明确记录未验证，不把文件搜索当运行验收。
