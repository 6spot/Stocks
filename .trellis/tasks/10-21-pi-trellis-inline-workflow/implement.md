# 实施计划

## 开工范围

- 任务：`.trellis/tasks/10-21-pi-trellis-inline-workflow`
- 保留现有 V1-02 所有未提交业务改动与并发任务文件，任何提交都不应带入它们。
- Owner 已授权撤回项目 Pi 子进程派发，并同意仅限 Pi；执行由当前主会话完成，不派发新的 Trellis 子进程。
- 不运行 `trellis init`，不修改全局安装、全局配置或其他平台。

## 步骤

1. [x] 定位扩展中 dispatch-only 状态、spawn/JSON event loop、进度卡、快捷键及工具注册，删除这些路径；保留 task/session context、workflow context 和 project-root/session 隔离逻辑。
2. [x] 把 Pi start/continue 入口、共享 start/continue 中的 Pi 专属说明以及 workflow Phase 1.3 / Phase 2 的 Pi 路由改为主会话 inline；其他平台仍按 sub-agent 流程执行。
3. [x] 移除 Implement/Check agent 文件上此前追加的 DeepSeek/Qwen 路由组 frontmatter，不改 agent 正文职责与 Pi 主会话组配置。
4. [x] 删除 `.opencode/` 项目目录；遵循 Trellis 规范不手工修改 `.trellis/.template-hashes.json`，保留共享 Trellis CLI 的 OpenCode 通用适配。
5. [x] 更新 `.trellis/model-routing.md` 与本任务 acceptance 记录，说明 Pi inline、OpenCode 项目集成移除范围及 re-init 结论。
6. [x] 审阅完整 diff 和项目未提交状态；确认没有改动或 stage V1-02 业务路径、任务及并发文件，也未修改模板哈希清单。
7. [x] 本次未运行运行验证；在 acceptance 中记录运行检查和 Pi live reload 均未验证。

## 完成条件

- Pi 扩展不注册/启动 Trellis 子进程，不留失效快捷键提示；Trellis context/workflow 注入仍在。
- Pi 主会话 inline 路由指引一致；非 Pi 的子代理行为未被改写。
- 任务与开发规范保留，V1-02 未提交工作未纳入。
- 验收记录准确说明实际检查与未能执行的验证。
