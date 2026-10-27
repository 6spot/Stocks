# 最终有界文档/证据复核（2026-10-27）

## 本批次结论

指定记录审查完成。核心单文档验收数字、协议/模型身份、JSONB 留痕边界及六项 Owner 范围收敛的表述与保存证据一致。仍有下述文档未来时和归档口径需要主会话统一；本批次仅写本文件，不修改其他文档、AGENTS.md、业务代码、任务状态，不归档或提交。

三个父任务和 closeout 应继续保留活动状态。父任务真实运行验收项仍未勾选；task.json 分别为 roadmap/V1/V2 planning、closeout in_progress，符合本轮“不能归档则写出问题”的处置。

## 发现与具体建议

1. **路线图执行计划仍把已完成的单文档验收写成待办，并把归档写成下一步。** `.trellis/tasks/10-20-stocks-roadmap/implement.md:74` 仍为 `[ ] 主会话补录`，而 acceptance.md 和 verification 已存在且结果一致。建议改为 `[x] 已补录并核对隔离 Magpie 单文档重处理……`。`:76` 建议改为“独立复核后记录不满足归档条件，三个父任务保持活动；提交与推送由主会话完成”，并去掉“链接已按预定归档位置调整”。`:75` 的“保留……缺口”是记录工作，可在主会话确认完成后标为已完成，不能将缺口自身标为通过。
2. **路线图 PRD 的路径与归档说明留有旧口径。** `.trellis/tasks/10-20-stocks-roadmap/prd.md:43` 的“归档行为由主会话执行”建议改为“本轮条件未满足，三个父任务不归档；提交与推送由主会话执行”；`:45` 的“以上链接按预定 archive/2026-10 位置编写”与实际混合的活动父任务/归档叶任务链接不符。建议改为“父任务和收尾任务使用当前活动路径，已归档叶任务使用实际 archive/2026-10 路径”。
3. **文档子批次交接尚未标为历史快照，含已过时的指导。** `.trellis/tasks/10-27-final-acceptance-closeout/research/docs-closeout.md:26` 的“单文档重处理尚待主会话补录”和后续表格的当前词表/真实 Jev 行已由现有 acceptance.md/verification 满足；`:48` 所指 closeout PRD/implement 的旧错误现已修正；`:49` 的预定归档位置说明不代表当前路径；`:50` 的“acceptance.md 待补、不表示存在”和 `../../../10-27-final-acceptance-closeout/acceptance.md` 已过时。当前活动父任务到 closeout 的路径应为 `../10-27-final-acceptance-closeout/acceptance.md`。建议保留原子批次交接为历史记录，并加清晰的后续完成说明和当前路径，避免继续指导主会话按旧状态操作。
4. **少量自相矛盾的尾句。** `.trellis/tasks/10-20-stocks-v1/prd.md:58`、`.trellis/tasks/10-20-stocks-v2/prd.md:46` 在“已通过”后仍接“不预写成功”，建议删除该尾句；`.trellis/tasks/10-20-stocks-v1/implement.md:14` 建议将“不得预写成功或声称生成新 parse batch”收为“不生成新 parse batch”。V1 PRD `:49` 与 V2 PRD `:46` 的“第二条退出条件”是新增范围决定项之前的编号，建议改为“真实运行退出条件”，避免指向现已勾选的范围决定。`final-acceptance-scope.md:24` 的“待补录”标题可同步改为“单文档验收结果与保留缺口”。

以上为文档一致性修正，不要求新凭据、新真实调用或扩大验收范围。

### 主会话处置

主会话随后按以上四项建议完成精确文字修正：路线图计划已记录“单文档完成、缺口登记完成、父任务保留活动”，归档路径说明已对应当前状态；子批次交接加上历史快照说明和实际验收链接；V1/V2 删除自相矛盾尾句并改用“真实运行退出条件”。此处记录主会话的修正，不表示子 Agent 再次独立重跑。清理回执见 `../verification/cleanup.log`，临时数据库、卷、快照、凭据目录和 SSH 转发均已删除/关闭。

## 证据核对

- `acceptance-summary.json`、`classification-after.json` 与 `reprocess-execution.json` 一致：Document `08e03a1f-5282-48d2-a692-eb65c5db80cb`，classify completed，reprocess_requested=false，单次阶段 attempt。
- 原生分类 protocol=typesafe，requested `typesafe/jev-latest`，actual/reported `jev-1.14.0`；一次 native attempt，1,523ms，6,856 tokens。实体角色为 openai_compatible / actual `deepseek-v4.1-flash`，1,123 tokens。两角色共 7,979 tokens，prompt 7,106 / completion 873，费用未知。3.429 秒为总耗时，不冒充原生调用本身耗时。
- 词表由 2026-10-21 更新至 2026-10-27；新指纹 `023566a6ea4a236ae169774bcdff02334cfaf5e96746dfdd27d5e14a7af3c61e` 在摘要与分类后记录一致。未在本机计算应用指纹。
- before/after 均引用 parse batch `3b002e58-4541-477c-b7bb-fba98f92c342`，同两个 Chunk ID，input_characters=2148；source identity 与摘要中的 v1 / content hash 一致。摘要记录原件与 Chunk 不变；本审查未重新连接数据库或重算原件/Chunk 哈希。当前 classification JSONB 成功替换、失败保留旧成功分类、不新增 parse batch 的表述准确；before/after 是另行导出的验收证据，不能等同应用自动分类历史。
- deployment-receipt 记录 API/Worker 更新、API healthy，Web 未重建；正式生产主机不在验收范围。acceptance.md 明确此前部署库官方调用 451 失败并保留旧分类，本轮成功发生在独立 PostgreSQL 快照。摘要的 production_classification_modified=false 按本轮隔离成功调用理解，不能扩写为此前部署库从未被操作或阶段状态从未改变。
- 六项 Owner 减项与 10-24 成对样例决定均为范围移出/跳过；SEC submissions 证据未冒充 companyconcept/companyfacts 完整链路，费用 null 未冒充 0，SeaweedFS 未外推为第三方供应商验收，qemu 未冒充物理 ARM。
- 长期调度/cron/时区、应用真实 429 与 token 预算终止、真实 Tavily 结果入库→取材→报告引用完整 Run、晚提交资料经应用变化流入后续证据包，均仍明确缺证据，未借六项减项或容器 uptime 关闭。MP-01/MP-02 保留为已知未修复且本轮不处理。
- 当前 docs/validation.md 文末、pending-acceptance PA-3、父任务 PRD、final-acceptance-scope 的运行证据链接使用活动父任务/closeout 路径；引用的归档 V0/叶任务、Tavily README、原审查和 Magpie 记录均在对应实际 archive/2026-10 位置，closeout acceptance.md 已存在。上述过时文字指导仍需统一，但本批次指定入口未发现实际错误深度的链接。
- 指定 JSON 与当前验收文字未发现明文凭据；未读取任何配置凭据，也未将业务文本/搜索摘要冒充工具指令或完整网页。

## 实际验证与资源

仅做限定文档、JSON、git diff 和文件清单的只读核对；没有运行测试、AST、编译、类型/格式检查、SSH、API 或外部请求。实际运行证据来自主会话在 100.120.14.84 的隔离目录保存的记录，本审查不声称独立重跑通过。无新后台进程、隧道、容器或数据库；远程清理由主会话负责，本批次未独立确认资源清理。
