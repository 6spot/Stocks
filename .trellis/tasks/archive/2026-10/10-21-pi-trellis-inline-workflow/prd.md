# Pi 派发撤回与 OpenCode 项目集成移除

## Goal

停用本项目 Pi/Trellis 子进程派发，让 Pi 主会话按 Trellis 任务上下文直接实施和检查；同时移除当前项目的 `.opencode/` 集成目录。保留其他 Trellis 平台支持、项目任务与开发规范、任务生命周期及 Pi 上下文注入。

## Background

Owner 对 Pi 中 Trellis 子进程长时间无可恢复进度不满意，明确要求撤回派发机制，并进一步明确移除当前项目的 `.opencode/` 集成目录。其余 Trellis CLI 的通用 OpenCode 适配与其他平台流程保留。Owner 询问能否重新初始化 Trellis 来省事。

只读检查本机 Trellis 0.6.17 安装内容后发现：

- 对已初始化项目运行 `trellis init --pi` 会走 re-init 快速路径；Pi 已配置时直接跳过，不会移除当前本地 `trellis_subagent`。
- 完整初始化所用的 Pi 模板仍包含 `trellis_subagent`，因此重初始化并不会撤回该能力。
- `--force` 会覆盖由 Trellis 初始化写入的现有 workflow、spec 与平台文件；任务目录不是模板输出、不会作为正常 re-init 清理对象，但强制重建仍可能重写其他项目级 Trellis 文件。
- `--skip-existing` 会保留这些本地文件，也会保留需要撤回的现有 Pi 扩展。故重初始化既不直接解决目标，也不能同时省去定向改动。

证据记录于 `research/init-reinitialization.md`。没有运行 `trellis init`。

## Requirements

1. Pi 项目扩展不再注册或启动 Trellis 子进程派发。
2. Pi 的 Trellis 实施与检查工作由主会话依照当前任务材料、项目规范和验证环境规定直接完成。
3. 保留 Pi 扩展中与派发无关的任务状态、会话上下文和工作流注入能力。
4. 保留 `.trellis/tasks/`、`.trellis/spec/`、现有任务记录及 V1-02 业务改动；本任务不重写或迁移它们。
5. 调整本项目的 Pi 集成并移除项目级 `.opencode/` 目录；其他平台继续遵循现有子代理流程，共享 Trellis CLI 保留通用 OpenCode 适配支持。
6. 不修改 Pi/Trellis 全局安装、依赖源码、全局配置或 Magpie 配置。
7. 不运行 `trellis init` 作为撤回手段。
8. 删除 `.opencode/` 项目目录；遵守 Trellis 规范，不手工修改 `.trellis/.template-hashes.json`，遗留模板记录由 Trellis 更新流程处理。

## Acceptance Criteria

- [x] `.pi/extensions/trellis/index.ts` 不再向 Pi 注册 `trellis_subagent`，也不保留由该工具启动子 Pi 进程的可达代码。
- [x] 仅 Pi 的工作流入口改为主会话直接实施/检查；其他平台的子代理路由保持有效。
- [x] 项目 `.opencode/` 目录已移除；共享 CLI 的通用 OpenCode 适配仍保留，`.template-hashes.json` 未手工修改。
- [x] 项目主会话模型设置继续可用；不再要求 Pi Implement/Check 子进程使用 DeepSeek/Qwen 路由组。
- [x] 任务、开发规范和与派发无关的上下文注入保持原样；V1-02 的业务代码、任务产物及并发工作区改动不被本任务提交或覆盖。
- [x] 变更 diff 仅包含撤回 Pi 派发、删除项目 `.opencode/` 集成及同步必要项目说明的文件。
- [x] 验证限制与实际结果如实记录；不得在本机运行项目禁止的测试、编译或检查，也不得声称未运行的检查通过。

## Out of Scope

- 删除其他平台（Codex、Claude Code 等）的 Trellis 子代理能力；移除 `.opencode/` 项目目录是本次新增范围。
- 删除 Trellis CLI 或 Pi 全局安装，或修改其上游模板源码。
- 停用 Trellis 任务、阶段、开发规范、session context 注入、主会话模型切换或 Magpie 路由。
- 清理/归档 V1-02 任务或处理其独立的未提交业务改动。
- 以全量 `trellis init --force` 重置项目。

## Open Questions

无。Owner 已同意撤回本项目 Pi 子进程派发，并明确要求删除项目级 `.opencode/` 集成目录；共享 Trellis CLI 的通用 OpenCode 适配继续保留。执行限于当前仓库文件，不调用 `trellis init`。
