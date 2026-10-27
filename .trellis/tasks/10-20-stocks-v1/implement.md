# V1 阶段执行与验收

- [x] 前置交付：[V0-07](../archive/2026-10/10-20-v0-07-evidence-api-acceptance/prd.md) 已归档，V0 阶段亦已归档。
- [x] 按 PRD 任务表完成九个子任务，各自验收并归档；父任务不作为产品实现目标。
- [x] 各子任务已记录相关验证与 AC 结果。
- [x] 在指定服务器执行 arm64 模拟环境部署和 V1 相关测试、真实日常行为检查，记录已核对。
- [x] Owner 已确认真机/物理 ARM/公网 HTTPS 等六项收敛，见 [范围决定](../10-20-stocks-roadmap/final-acceptance-scope.md)，属于范围调整。
- [ ] 补齐仍在范围内的长期调度、cron/时区、应用 429/预算终止与晚提交应用变化流证据；36h uptime 不算完成。
- [x] 环境受限和仅有受控证据的场景分别登记，不用 mock 宣称真实验收完成。

九个实现子任务均已完成。Owner 于 2026-10-27 授权父阶段归档，并保留未验证缺口，不将归档写为全部退出条件通过。2026-10-23 补充的真实调度、游标与认证
结果见 PRD 和 `docs/validation.md`；相关测试 111 passed。晚提交仅检查 SQL 可见性，
三条引用的两 Chunk/一 Document 分布不单独证明取材去重；合并及提交水位另有集成测试覆盖。
Owner 已确认六项范围收敛；当前词表/原生 Jev 的单文档隔离验证已通过，见 [收尾结果](../archive/2026-10/10-27-final-acceptance-closeout/acceptance.md)，不生成新 parse batch。MP-01/MP-02 不在本轮修复范围。统一项见
[PA-3](../10-20-stocks-roadmap/pending-acceptance.md)，V2 记录见
[本轮验收](../10-20-stocks-v2/research/acceptance-2026-10-23.md)。

## 上下文读取注意

`docs/data.md` 当前为 34,267 字节，超过默认单文件注入上限 32,768 字节。自动注入可能截断末尾；派发/开工时须完整读取原文件，尤其末尾检索与来源安全契约。不要把已注入前缀当成全文；无需为此修改领域文档或 Trellis 全局配置。
