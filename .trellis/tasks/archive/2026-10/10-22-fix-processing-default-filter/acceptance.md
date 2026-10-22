# F-05 验收记录

## 当前结论

2026-10-22：积压面板初始筛选已改为 `status=failed`、`stage` 为空，回归断言与归属文档已更新，人工静态复核完成。**运行验证尚未执行，F-05 未通过验收，AC 全部保持未勾选。** 归档只表示代码已交付，不表示测试或构建通过。

## 修改与静态证据

- `frontend/src/features/runs/processing-panel.tsx`：`stage` 初值由 `"failed"` 改为 `""`（现有“全部可用阶段”），`status` 初值由 `""` 改为 `"failed"`。`dashboardApi.processing()` 的 `queryString()` 会省略空字符串，因此首次请求只带 `status=failed&limit=100`，不再发送非法 `stage=failed`。
- 阶段与状态仍是两个独立 `<select>`，选项集合未变（阶段 `parse/classify/facts/index`，状态 `failed/pending/running/completed`）；未新增控件、未改布局、未改分页或重试逻辑。
- 后端 `GET /api/processing` 的阶段/状态校验未放宽：`failed` 仍只属于 `PROCESSING_STAGE_STATUSES`，不属于 `PROCESSING_STAGES`。
- `frontend/src/features/runs/processing-panel.test.tsx`：新增用例断言首次调用为 `{stage: null, status: "failed", limit: 100}`，并覆盖选择 `facts`、切换状态 `pending`、再恢复“全部阶段”时两个字段独立变化。
- `frontend/src/api/client.test.ts`：新增 `dashboardApi.processing()` 的真实查询序列化断言，确认 `status=failed` 存在、`stage` 完全省略、`limit=100`。
- `docs/interface.md`：补充积压面板两个筛选字段的初始语义与 `failed` 只属于状态的边界。

未修改数据库、后端 API 实现或共享客户端序列化逻辑；未新增抽象层。

以上为静态确认，不是测试、类型检查或构建的通过结果。

## AC 与回归场景

全部用例保持未运行状态。

| 验收项 | 覆盖位置与断言 | 运行状态 |
| --- | --- | --- |
| AC-1：首次渲染只发 `status=failed` | 面板用例断言首次 `dashboardApi.processing` 参数为 `stage: null, status: "failed"`；客户端用例断言实际 URL 查询只含 `status` 与 `limit`，不含 `stage` | 未验证 |
| AC-2：阶段与状态筛选独立 | 面板用例依次选择 `facts`、`pending`、恢复全部阶段，逐步断言 `stage`/`status` 各自变化；未把 `failed` 加入阶段选项 | 未验证 |
| AC-3：分页与重试入口不变 | 未修改分页参数（`limit: 100`）与重试流程；既有两条 `accepted=false` 重试用例保留 | 未验证 |

后端参数校验是否接受该请求：静态确认 `stage=None` 与 `status="failed"` 均落在合法集合内；真实登录 API smoke 未执行，因此不作为已验证结论。

## 实际执行与阻塞

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 root@100.120.14.84 'uname -s'
# root@100.120.14.84: Permission denied (publickey,password)  exit 255
```

当前会话仍无可用测试服务器认证。依据仓库 AGENTS.md，前端测试、类型检查、构建与 API smoke 都只能在指定服务器的任务隔离目录执行，因此本轮未运行 `vitest`、`tsc`、构建或接口检查，也未回退本机。未创建远程目录、容器或后台进程。

## 恢复执行

取得本会话可用 SSH 认证后，在远程任务隔离目录的 `frontend/` 中执行：

```bash
npm test -- src/features/runs/processing-panel.test.tsx src/api/client.test.ts
npm run typecheck
```

若需要证明后端接受该参数组合，再按测试环境规范启动当前任务隔离的 API，使用正常 Owner 会话请求 `GET /api/processing?status=failed&limit=100`（不带 `stage`），确认返回 200 且不记录凭据。记录实际命令与结果后再勾选 AC。
