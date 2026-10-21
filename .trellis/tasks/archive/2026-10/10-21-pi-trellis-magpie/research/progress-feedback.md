# 子进程进度与阻塞反馈诊断

本轮用户反馈：子进程遇到问题不反馈，长时间无法判断在做什么。继续处理已授权的 pi-trellis-magpie 配置任务，不启动或修改并发的 V1-01 业务任务。

## 只读证据

Pi 会话 `01a15c24-fd56-7747-9cbd-bc09b7f0e7e9` 的派发 / 返回记录：

| 返回记录 | 角色组 | 时长约 | 助手轮数 | 记录状态 |
| --- | --- | --- | --- | --- |
| 第 69 行 | group/deepseek | 28.9 分钟 | 227 | succeeded |
| 第 77 行 | group/qwen | 96.6 分钟 | 77 | cancelled |
| 第 94 行 | group/qwen | 91.8 分钟 | 159 | succeeded |

这些记录证明角色组已被实际派发；它们不是本轮反馈补丁的运行验收，也不证明 Magpie 最终 served 的具体模型。两个长 Qwen 调用都记录了大量工具操作，不能仅凭时长判定整个过程卡死或一直重试。

只提取角色、状态、时长、工具状态和验证相关信号，未输出原始 prompts、命令中的认证或账户信息。未重放、取消或接管该会话。

## 代码中的缺口

`.pi/extensions/trellis/index.ts`：

1. `progressKey` 没有文本 / stderr / 最后事件时间；流式文本即使更新了 RunState，也可能被部分更新去重过滤。
2. `stderr` 只写内存，没有触发 emit；tool_execution_end 只保留成功 / 失败，没有失败摘要；错误默认仅在展开视图出现。
3. 没有定时刷新或静默时间。工具没有新事件时，卡片不会主动说明多久未收到消息。
4. 未处理 retry / compaction / tool_execution_update。agent_end 被直接标为成功，但 Pi 1.3 的此事件仅结束低层运行，仍可能自动重试。
5. CLI 退出 0 一律返回非失败；模型 error、abort 或缺少最终报告都可能被误报。finalize 优先选择较早的助手文本，会用进度话语盖住最终退出错误。
6. 子 Pi 使用 JSON headless 模式；主会话在等待 trellis_subagent 的 Promise，没有实时对话通道。子 Agent 在里面提问并继续等待，主会话不能靠该问题拿回控制权。原 prompt 缺少明确的阻塞退出要求。

依据为当前安装 Pi 1.3 的 `docs/json.md`、`message-types.md` 与生命周期 / compaction 文档；没有改用 RPC 或额外 channel 编排。

## 修复边界

继续使用同一事件解析、RunState、onUpdate 卡片和返回链路：

- 折叠视图显示公开进度、工具耗时和错误摘要；parallel 的其他失败子调用也显示原因。
- 文本、工具部分更新和 stderr 刷新活动时间；每 5 秒刷新时间，60 秒无事件显示静默。计时器不伪造子进程事件，退出 / 取消后清理；不因为静默自动杀进程。
- 显示模型 retry / compaction；agent_end 不提前成功。错误 / 取消 / 截断或缺少最终报告、非零退出和明确 BLOCKED 报告均按失败返回，错误原因优先。原 chain 失败即停止的机制继续复用。
- buildPrompt 要求子 Agent 在阶段变化或工具失败时公开说明；缺少凭据、权限、环境或用户决策而无法继续时，以第一行 `BLOCKED: <原因>` 给出已完成 / 未完成 / 所需条件，然后结束调用。主会话收到后报告，不在条件未变化时反复派发。
- 不展示 thinking 原文或完整工具结果；新增诊断摘要限制长度、遮蔽常见凭据字段。原生 Agent 正文 / 工具、模型角色组与任务生命周期保持原有配置。

阻塞退出依靠模型遵守提示，再对显式报告做确定性失败判定；这不是所有外部阻塞的自动检测器。静默提醒也不判断进程是否死锁。已经运行的子进程不会热加载磁盘改动；当前调用结束或由用户取消后 reload，后续派发才应用新逻辑。

## 验证状态

已准备 `../verification/progress.test.mjs`，通过临时副本导出私有帮助函数，并用无网络 Node 子进程模拟实际 JSON 流，覆盖公开进度、失败卡片、retry / compaction、零退出码模型失败、显式 BLOCKED 停止 chain、stderr 实时更新、静默刷新、取消和 timer 清理。

只允许在指定测试服务器的本任务隔离目录运行，Node 需支持 `--experimental-transform-types`。替身不调用 Pi / Magpie，不需要真实凭据和 npm 依赖。

本轮实际尝试 `ssh -o BatchMode=yes -o ConnectTimeout=8 root@100.120.14.84 'command -v node'`，退出 255，认证失败，远端命令未执行。没有远端目录或资源残留。测试、语法 / 类型 / 格式检查、扩展重载与实际卡片渲染均未运行；没有本机回退或真实模型测试消耗。
