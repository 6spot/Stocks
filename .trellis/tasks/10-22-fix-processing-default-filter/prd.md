# F-05：修正积压面板初始筛选参数

## 问题与目标

处理面板将 stage 初值设成 failed，status 为空；failed 属于状态，后端 stage 只允许 parse/classify/facts/index，首次请求被拒绝。目标是首次打开即正常显示失败处理条目，并保留合法的阶段筛选与重试操作。

来源：[审查 F-05](../10-20-stocks-roadmap/research/implementation-design-review.md)。父任务：[修复计划](../10-22-implementation-design-fixes/prd.md)。P2，无依赖，可独立交付。

## 范围与约束

仅修正面板默认状态与相关参数传递，复用现有 API。默认筛选为 status=failed、stage 不指定（全部阶段）；不得将 failed 加入后端合法阶段掩盖错误，不增加新控件或改动页面布局。

## 验收条件

- [ ] AC-1：首次渲染发送 status=failed，stage 不发送/为空而不会发送 failed；真实服务端参数校验接受该请求，面板正常显示数据或空态。
- [ ] AC-2：用户选择 parse/classify/facts/index 或全部阶段时，请求字段正确；状态筛选独立工作，不混用两个字段。
- [ ] AC-3：原有分页与重试入口仍按现有契约工作；请求失败保留原有错误反馈。

## 状态

当前仅规划，未运行前端测试、构建或接口 smoke。设计依据为 docs/interface.md 的 Runs/积压入口。
