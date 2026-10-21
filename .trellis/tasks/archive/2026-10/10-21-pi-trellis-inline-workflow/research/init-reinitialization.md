# Trellis 重新初始化与 Pi 子进程派发撤回调查

检查依据：本机只读阅读 Trellis 0.6.17 已安装包及当前项目文件；没有运行初始化、更新或卸载命令，也没有修改全局包。

## 当前 Pi 文件集

Trellis `dist/configurators/pi.js` 的 `collectPiTemplates()` 生成：

- `.pi/extensions/trellis/index.ts`
- `.pi/settings.json`
- `.pi/prompts/trellis-*.md`
- `.pi/agents/*.md`
- `.agents/skills/*.md`

当前安装模板 `dist/templates/pi/extensions/trellis/index.ts.txt` 在 1869 行注册 `trellis_subagent`，含 `single`、`parallel`、`chain` 派发。因此覆盖本地扩展为当前版本的官方模板仍会保留子进程机制。

## `trellis init` 已初始化项目路径

`dist/commands/init.js` 的 `init()` 对已有 `.trellis/`、非空 `tasks/`、无 `--force` / `--skip-existing` 且未指定模板的目录走 re-init 快速路径。显式 `--pi` 进入 `handleReinit()` 的添加平台逻辑；`getConfiguredPlatforms()` 若发现 Pi 已由模板 hash 管理，则打印 already configured 并跳过。此路径不能用于覆盖/重置现有 Pi 扩展。

交互式 full re-initialize 才会进入完整流程，但模板 `writeFile` 的冲突模式有风险：

- 默认 `ask` 会对不同内容逐文件询问。
- `--skip-existing` / 非交互 `--yes` 保留所有既有文件，故目标扩展不变。
- `--force` 以最新 Trellis 模板覆盖冲突文件，包括 `.trellis/workflow.md`、`.trellis/config.yaml`、已有 `.trellis/spec/**` 与 `.pi/**` 中受管理文件。

`createWorkflowStructure()` 使用 `ensureDir` / `writeFile` 创建结构，不会递归删除 `.trellis/tasks/` 或 `.trellis/spec/`。但 `--force` 可能覆盖 spec 文件内容。任务 skeleton 在首装/空任务目录场景生成且目录已存在时幂等返回；它不是安全恢复现有项目自定义内容的承诺。

## 结论

重新初始化不是撤回子进程派发的捷径：安全的 re-init 快速路径会跳过现有 Pi；full re-init 的官方模板又保留该能力；强制模式还会覆盖要保留的 workflow/spec。最小可控方案是在本项目中定向移除 Pi 的 `trellis_subagent` runner 与 UI，并同步 Pi-only 工作流说明；任务、规范、上下文注入和其他平台保持不变。

## 当前项目证据

- 当前 `.pi/settings.json` 只注册 `./extensions/trellis/index.ts` 和 prompts，并配置项目默认 Magpie/OpenAI 组。
- 本项目扩展既处理会话/工作流上下文，也注册子进程工具；不能删除整个扩展，否则会连带移除与派发无关的上下文能力。
- `.pi/agents/trellis-implement.md`、`trellis-check.md` 中的 DeepSeek/Qwen `model` 绑定由此前项目改动加入，其他 agent 职责内容是 Trellis 的常规模板。
- `.trellis/workflow.md` 与 shared skills 是多平台共享文件，需给 Pi 加 inline 路由，同时避免改变其他平台的 sub-agent 流程。
- V1-02 业务变更由并发任务维护，不属于本任务覆盖范围。

## 依据位置

- `dist/commands/init.js`: `handleReinit()`、`init()`、re-init fast path、`createWorkflowStructure()` / platform configure 调用。
- `dist/utils/file-writer.js`: `ask`、`skip`、`force` 写入策略。
- `dist/configurators/workflow.js`: `createWorkflowStructure()` 仅确保 task/spec 目录并写入模板，不清理任务目录。
- `dist/configurators/pi.js`: `collectPiTemplates()`。
- `dist/templates/pi/extensions/trellis/index.ts.txt`: 当前官方扩展模板仍注册 `trellis_subagent`。

所有上述文件都只读检查；用户要求不修改任何全局安装源码。
