# F-05 实施计划

- [x] 取得本叶子实施指令，读取父任务、前端 spec 和执行边界，复核后 task.py start。
- [x] 核对现有默认状态、筛选项和 dashboardApi 查询序列化，确认首次请求实际字段。
- [x] 将失败条件放到 status，stage 使用现有全部阶段表示，保持其余交互。
- [x] 更新现有测试的首次请求/筛选断言，覆盖一次真实参数边界；不增加仅复述赋值的测试。用例已编写，未运行。

## 最小验证

前端测试、类型检查、构建与 API smoke 均只允许在 100.120.14.84 的本任务隔离目录运行。本机不运行 npm/pnpm 测试或构建。

当前 frontend/package.json 的 test 为 vitest run，typecheck 为 tsc -b --pretty false。在远程隔离目录的 frontend 中执行 `npm test -- src/features/runs/processing-panel.test.tsx`；必要时执行 `npm run typecheck`。接手时先复核脚本有无变化。真实 API smoke 或已有后端参数测试选择其一，使用正常 Owner 会话，验证 stage 省略且 status=failed 返回成功；不绕过认证或在日志输出凭据。

## 收尾

- [ ] AC-1 至 AC-3 有实际证据；记录运行命令、结果和任何未验证限制。当前仅有静态证据与已编写用例。
- [ ] 质量检查及记录同步；无需结构变更或专门等待其它叶子，按授权独立交付。

## 本轮交回（2026-10-22）

代码、回归断言与归属文档已更新，静态复核完成；SSH 免交互认证返回 `Permission denied (publickey,password)`，exit 255，未执行前端测试、类型检查、构建或 API smoke，未回退本机。验收项保持未勾选，详见 [acceptance.md](acceptance.md)。
