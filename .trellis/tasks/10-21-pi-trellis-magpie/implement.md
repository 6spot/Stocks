# 实施与验收计划

## 授权与当前进度

- [x] 用户批准任务创建与规划，随后批准最终方案实施。
- [x] 通过原生 task.py start 进入 in_progress。
- [x] 用户进一步明确：只指定其创建的 Qwen / Deepseek / OpenAi 路由组，具体模型由 Magpie 管理。
- [x] 读取实际组 ID、Pi 导出清单和成员家族。
- [x] Planner 项目默认组为 OpenAi，Implement 为 Deepseek，Check 为 Qwen，删除单模型候选 TODO。
- [x] 原生派发入口要求显式完整组引用和 registry 精确存在，其他 Agent 行为不变。
- [x] 同步配置索引、设计、需求和环境调查。
- [x] 回溯用户指定会话，确认 OpenAi 主会话执行了 SSH 而没有派发子 Agent；修正共享技能平台提示，补充执行操作应委派的指引。
- [x] 补齐原生事件处理、公开进度 / 失败 / 重试 / 静默卡片、阻塞报告和最终失败判定；不干预并发业务任务。
- [x] 准备无真实模型 / 凭据的 JSON 事件与子进程替身回归；本轮再次 SSH 认证失败，保持未运行。
- [ ] 在指定服务器运行反馈回归（包括计时和 chain）及必要编译 / 类型检查。
- [ ] 指定服务器上的离线派发 / 设置加载验收。
- [ ] 条件允许时最小真实请求及 Magpie 转发对照。

用户后续明确要求“归档提交”，本次按此授权提交并归档当前配置版本；以下未执行验收保持未勾选，不代表测试通过。不推送、合并或开始其他业务任务。

## 实施边界

本任务由当前主会话处理。改动为 `.pi/settings.json`、两个 Agent frontmatter、现有扩展检查与工具提示、共享 start / continue 技能、Pi continue prompt、配置索引和本任务材料。不编辑 Magpie / 全局 Pi / 其他 CLI、不重建组，不改原生任务生命周期、Agent 正文或工具。

普通派发省略工具级 model，使 frontmatter 组生效；明确覆盖时也只允许已注册的完整 Magpie 组。扩展检查解析后实际传给子 Pi 的引用，不管理具体模型。

## 验证环境

所有运行验证仅在 `root@100.120.14.84` 的当前任务隔离目录。只打包必要代码、Trellis runtime、任务和引用的规范；排除 .git、.env、认证、会话和 `._*`。不需要数据库 / Docker，不自动新增全局 npm 依赖。

此前 SSH 尝试：

```sh
ssh -o BatchMode=yes -o ConnectTimeout=8 root@100.120.14.84 'command -v node; command -v pi; command -v magpie'
```

退出码 255，`Permission denied (publickey,password)`，远端命令未执行。当前组绑定调整未重试相同访问，也未本机回退验证。恢复访问后先检查远端已有 Node / Pi 能力，再创建本任务独立目录；不复用其他任务环境或复制真实认证。

## 最小离线验收

用远端临时 harness 加载实际扩展，捕获工具，提供 registry fixture 和无网络子进程替身；不新增仓库运行依赖。

| 场景 | 断言 |
| --- | --- |
| Implement / Check 无组，主会话有模型 | 抛出错误，子进程数为 0 |
| 具体模型引用、模糊名、非 Magpie 组、空组 ID | 拒绝，不 fuzzy/default 选择 |
| registry 不存在 / 组未注册 | 拒绝，不创建子进程 |
| 有效 frontmatter 组 | 子进程收到对应完整组 --model |
| 有效组 override / thinking 后缀 | 保持原生优先级及后缀语义，精确查找组 |
| single / parallel / chain | 均通过共同检查，所有子进程使用正确组 |
| Research | 原生继承行为不变 |
| Agent 上下文与工具 | 正文、工具与原生 JSONL / PRD / design / implement 加载完整 |
| 项目主会话默认值 | 新会话使用 magpie + group/openai；已有会话可自主 /model 切换 |
| 进度与工具失败 | 文本 / tool update / stderr 触发刷新，折叠卡片显示失败摘要和工具耗时，不展示私有 thinking |
| 重试与结束 | agent_end 不报成功；retry / compaction 可见；零退出码的模型 error / aborted 为失败 |
| 阻塞报告与错误优先级 | BLOCKED 报告作为失败返回并停止 chain，退出错误不被较早进度文字遮蔽 |
| 静默与清理 | 无事件时计时刷新并显示静默，进程结束 / 取消后清理 timer |

在远端完整隔离副本可以运行 `task.py validate`；只有远端已有适当 Pi Provider 配置时才运行 `pi --offline --list-models magpie`。fixture 结果不能冒充本机真实 registry。记录命令、退出码和观察结果；未运行则明确标记。

## 子进程反馈回归

已准备 `verification/progress.test.mjs`：在远端的本任务隔离副本根目录运行：

```sh
node --experimental-transform-types --test .trellis/tasks/10-21-pi-trellis-magpie/verification/progress.test.mjs
```

需 Node 22.7 或更新版本支持类型转换；不安装新依赖，不访问真实模型。用例在本任务 verification 目录创建临时 fixture 并清理；加载实际扩展的临时副本、模拟 JSON 子进程，检查公开进度、UTF-8 分片、工具错误、retry / compaction、零退出码失败、BLOCKED 停止 chain、stderr / 静默刷新、取消和 timer 清理。记录实际结果；当前未执行，不将测试文件存在记作通过。

本轮实际 SSH 探测 `command -v node` 再次退出 255（BatchMode 未提供可用认证方式），未在远端创建资源；本机没有运行语法 / AST / 类型 / 格式检查或测试。该结果不说明服务器网络不可达；本配置会话没有可用的临时密码凭据，也未执行密码认证验证。

## 最小真实验收

仅在测试服务器已有可访问的 Magpie 与授权认证时执行，先说明 token / 订阅消耗。每个角色组先做一次简短、禁止改文件的调用，核对 Pi 的组引用及 Magpie requested / sent / served 最终模型。不得固定具体型号作为验收预期，型号由组管理。

不因远端不能访问本机 loopback 而公开网关、复制凭据或改回本机测试。没有条件则保持未验证。

## 静态交付与保留

审阅完整 diff：配置、派发提示及任务材料，组 ID 有实际来源，不含敏感值，不保留要求用户选择单模型的 TODO。检查缺省拒绝、只接受组引用、主会话可切换及任务规划 / 执行分离。

集成契约归属 `.trellis/model-routing.md`，不重复写入业务规范。Trellis update 先 dry-run，保留或 .new 合并这些本地修改，更新后复核组并 reload。回滚只撤销本任务新增块。

静态复核与运行测试分别记录在 `acceptance.md`；没有运行测试时不宣称验收全通过。
