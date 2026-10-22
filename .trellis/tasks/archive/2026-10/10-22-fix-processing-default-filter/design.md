# F-05 技术设计

## 修改点

frontend/src/features/runs/processing-panel.tsx 的 stage 初值设为空/现有“全部阶段”表示，status 初值设为 failed。通过 frontend/src/api/dashboard.ts 与 client.ts 的现有查询序列化将空 stage 省略。开工时检查组件允许值和 API 参数定义，不预设需要更改共享客户端。

后端 backend/src/stocks/api.py 的 stage/status 合法值边界保持严格，不能接受 failed stage。优先用现有状态类型约束此处非法组合；只在已有类型可直接复用时收紧，不为单个默认值新建抽象层。

## 实施落点（2026-10-22）

只改面板两个初值：`stage` 为空、`status` 为 `failed`；现有 `queryString()` 省略空 stage，后端校验未放宽。未新增抽象或控件。回归补充在 `processing-panel.test.tsx`（首次参数与切换）和 `client.test.ts`（真实查询序列化），`docs/interface.md` 记录筛选语义。测试与类型检查尚未运行，远程认证受阻，详见 [验收记录](acceptance.md)。

## 验证设计

在现有 processing-panel.test.tsx 中核对首次请求和后续筛选传参，调整已有覆盖即可，不为单行赋值新增机械测试。若现有 mock 绕过实际序列化，应在受影响的现有 API 测试中补足一处有意义的参数契约验证，或在远程运行一次登录后的真实 API smoke。只用宽松 mock 返回成功不能证明后端接受参数。

不更改业务数据模型；预期无需领域契约改动。若 docs/interface.md 已正确表述阶段/状态，不为补丁重复写第二套规范。
