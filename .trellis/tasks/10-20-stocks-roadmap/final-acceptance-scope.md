# 最终验收范围决定（Owner，2026-10-27）

本文件记录 V1/V2/路线图父任务收尾的授权范围。**范围收敛不是验收通过，归档也不表示所有真实运行场景已通过。** 最终运行结果由收尾任务补录；本文件不替代运行证据。

## 六项范围收敛

| 项 | Owner 决定 | 证据边界 |
| --- | --- | --- |
| 公网/HTTPS 发布 | 不作为本轮验收；应用保持 HTTP，由反向代理或 CDN 终止 TLS | 不宣称已验收公网 HTTPS；实际发布仍遵守访问控制与安全 cookie 配置要求 |
| 真机视觉与跨设备登录 | 跳过手机/平板硬件验收 | 已有桌面多视口与认证证据不能写成真机通过 |
| 物理 ARM | 不要求硬件验收，保留 qemu arm64 证据 | 2026-10-23 模拟构建、运行及新库初始化记录不是 ARM 硬件证据 |
| 返回费用的模型端点 | 不再要求接通返回货币费用的真实端点 | 供应商未返回费用时保持 null/未知，不记为 0，不自行估价；token 预算继续有效 |
| 第三方 S3 | 自建 SeaweedFS 已满足当前使用，不再要求额外供应商 | S3 兼容接口证据只代表 SeaweedFS |
| SEC 全量 companyfacts/完整 JSON 研究链路 | 不作为交付前提；companyconcept 单指标按需获取是后续可选方案 | 既有证据来自 submissions JSON 的采集和四字段映射，不能改写为 companyconcept 或 companyfacts 已实测；解析超出 5,000 值上限的失败如实保留 |

2026-10-24 已批准移出的真实转载/冲突成对样例、真实跨报告版本/指标修订与晚到样例继续不在验收范围；对应受控能力与测试保留，不记为真实样例通过。

## 已有真实证据

- Tavily 真实远程 MCP 已独立完成并归档，见 [Tavily 验证记录](../archive/2026-10/10-27-v2-real-mcp-tavily/verification/README.md)：生产 `McpClient` 的 initialize/list/call 成功，4.184 秒。它证明真实客户端接通，不证明 Tool 结果入库、取材、报告引用的完整研究 Run；429 探测也不证明应用持续调度下的限流/预算终止。
- 真实外网采集与 magpie chat/embedding 闭环已有 2026-10-23 证据。测试代理路径不改变生产模型官方端点配置。
- Jev 原生适配器的独立 Magpie 调用已有 [先前记录](../archive/2026-10/10-26-application-pipeline-audit/research/magpie-live.md)，不能外推为生产 41 篇文档或完整研究联合闭环均使用原生 Jev。

## 单文档验收结果与保留缺口

- `method: decision_model` 不是 Jev 身份证明。本轮隔离 Magpie 单文档重处理已成功，actual `jev-1.14.0`，3.429 秒，新词表指纹 `023566a6…`；详见 [收尾验收记录](../archive/2026-10/10-27-final-acceptance-closeout/acceptance.md)，不代表部署库全部文档已经重处理。
- 分类为当前 JSONB 状态：成功重处理覆盖当前 classification，失败保留前次成功 classification；不会创建新的 parse batch。旧 parse/Chunk/报告证据保留的契约，不能写成旧 classification 自动另存新批次。
- 容器 36 小时 uptime 只能说明容器运行时长。长期采集/研究调度、cron/时区真实触发、应用在 429 与预算耗尽下的有界终止仍缺相应运行证据，未被上述六项决定移出范围。
- 完整真实 Tavily 补证 Run 仍未验证；V1 晚提交只验证 SQL 可见性，未证明经应用变化流进入后续证据包。保留受控测试与真实证据的区别。
- [既有审查](../archive/2026-10/10-26-application-pipeline-audit/audit.md) 的 MP-01（自定义 API/Worker 配置路径不一致）、MP-02（历史语义文档漏带已有 Fact）已于 2026-10-28 修复并归档，不再属于未修复范围；见[配置一致性验收](../archive/2026-10/10-27-report-config-consistency/verification.md)与[历史 Facts 修复](../archive/2026-10/10-27-report-historical-facts/prd.md)。

## 归档口径

Owner 已授权核对后归档满足条件的阶段父任务；上述长期运行/完整补证等条件尚未关闭，因此本轮不归档三个父任务。最终单文档结果见收尾验收记录；文件链接保留当前活动路径，后续实际归档时再调整。
