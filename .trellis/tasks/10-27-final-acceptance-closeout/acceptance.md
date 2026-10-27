# 最终验收收尾：阶段结果与剩余问题

## 本轮已完成

无需 Owner 再提供 Magpie 信息。网关地址和调用凭据从本机现有 Pi provider 配置读取，API 返回 `typesafe/jev-latest` 为 decision 模型且提供 `/v1/systemone`。网关凭据只送入测试服务器私有文件，不导出上游 TypeSafe 密钥，不写入仓库。

在 `100.120.14.84` 的任务隔离目录，把测试部署数据库快照恢复至临时 PostgreSQL，以新部署的应用镜像对一篇已保存的真实 SEC 文档执行标准 `run_stages_for_documents(..., reprocess=True)`：

| 项 | 实际结果 |
| --- | --- |
| Document | `08e03a1f-5282-48d2-a692-eb65c5db80cb`，SEC/AMF 监管合作备忘录公告 |
| 原始版本与 SHA-256 | v1 / `290efe3a1ef7f3f8e44385380456d68802e3709d3990bb862959739dd5834183` |
| 原生分类 | protocol `typesafe`，requested `typesafe/jev-latest`，actual **jev-1.14.0** |
| 模型调用 | Jev 原生分类 + 独立实体生成，均为真实 Magpie 转发；无受控模型答案 |
| 分类结果 | policy，实体 companies/symbols 均为空 |
| 词表 | `2026-10-21` → **2026-10-27** |
| 新指纹 | `023566a6ea4a236ae169774bcdff02334cfaf5e96746dfdd27d5e14a7af3c61e`，与当前词表计算值一致 |
| 持久状态 | classify **completed**，reprocess_requested=false |
| 耗时/用量 | 总耗时 **3.429 秒**；Jev 1 次 attempt、1,523ms、6,856 tokens；两角色合计 **7,979 tokens**；费用未知 |
| 原件与解析 | Document 版本/content hash/snapshot key、parse batch、Chunk ID 和逐 Chunk 文本哈希未变 |

实际证据见 [acceptance-summary.json](verification/acceptance-summary.json)、[分类前](verification/classification-before.json)、[分类后](verification/classification-after.json) 和 [持久阶段结果](verification/reprocess-execution.json)。这次运行没有新采集、Fact、embedding 或研究报告；不能说成全部真实依赖的重新闭环。

## 部署事实与失败路径

前一轮 Owner 批准更新测试服务器 `/opt/stocks` 的 API/Worker。当前运行镜像含新词表和 Jev 接入，API healthy；Web 沿用既有镜像，未重新构建。构建曾因容器内依赖下载阻塞和把解包目录误当 wheel 而失败，最后用实际 `.whl` 离线安装构建成功。临时 Dockerfile 改动已恢复仓库原版，临时 wheels 目录清理；回滚备份保留在服务器且权限收紧至 0600。见 [部署回执](verification/deployment-receipt.json)。

前一轮在测试部署库直接重处理时，官方端点实际返回 **451**；那次处理失败，旧成功分类保留。之前把有 attempt/CLI 输出说成成功有误，以持久状态和安全错误为准。本轮改在隔离快照中经 Magpie 验证；**未修改 `/opt/stocks/.env` 的官方分类地址、密钥或模型名**。正式生产服务器是否受地区限制不由本测试推断，按 Owner 说明保持官方地址。

## 六项范围收敛已记录

应用 HTTP/代理或 CDN 终止 TLS、跳过手机真机、不要求物理 ARM、不要求返回货币费用的真实端点、SeaweedFS 足够、SEC 全量 companyfacts/完整 JSON 不作为交付条件。它们是 Owner 的范围决定，不是新增通过证据。此前真实转载/冲突和成对修订等样例的 10-24 范围决定继续保留。

## 为什么本轮不归档三个父任务

复核现有阶段原始条件后，六项决定之外仍有如下未关闭项。它们不需要 Owner 再提供 Magpie 密钥，但不能用不同证据替代：

| 任务 | 尚缺证据/处置 |
| --- | --- |
| V1 日常运行 | 持续采集/研究调度和 cron/时区真实触发记录；晚提交资料实际经应用变化流进入后续 Evidence Package 的记录 |
| V2 研究深化 | 应用持续运行下真实限流/预算耗尽的有界终止；真实 Tavily 结果入库→取材→报告引用的完整补证 Run |
| 路线图 | 以上阶段条件未全部关闭，仍应保留统一问题登记入口 |

**36 小时容器 uptime 仅证明运行时长；单次 Tavily 客户端成功仅证明工具可调用。** 二者不能分别替代持续调度或完整补证 Run。Owner 原始要求是“能归档就归档，否则把问题写出”，因此三个父任务与本收尾任务保留活动状态，阶段退出条件未勾成全部通过。

MP-01（自定义配置路径不一致）、MP-02（历史语义文档漏带已有 Fact）仍在前一审查记录，不在本轮修复范围，不能借归档消去。

## 正确的分类留痕边界

`method=decision_model` 是通用方法标记，不能独自证明 Jev。原生身份须核对 protocol、model_calls 与 actual model。

当前模型只有 Document.classification JSONB，没有自动分类历史表。成功重处理替换当前 JSONB，失败保留上次成功 JSONB；不会新建 parse batch。本轮另存 before/after 作为验收证据，未宣称应用自动保存全部分类历史。
