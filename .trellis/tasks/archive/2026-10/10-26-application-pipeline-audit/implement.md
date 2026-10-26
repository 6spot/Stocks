# 执行计划与结果

状态：官方 Jev 接入和手动链路审查已完成；受控同批闭环通过，后续 Magpie 单独真实 Jev 调用也已通过。真实外部模型/来源联合验收与浏览器检查未完成；详见 `audit.md` 与 `verification/README.md`。

## 0. 专用分类模型

- [x] 核对 TypeSafe 官方 model/state/questions、choice/noul、Bearer 认证及开放抽取能力边界。
- [x] 明确 Jev 分类标签与独立生成模型实体抽取，合并现有结果，保留失败、证据与调用元数据。
- [x] 实现原生 provider/分类路由、独立环境变量与 Compose 透传，同步 data/execution/quickstart 文档。
- [x] 在指定服务器运行协议与持久化边界测试，未使用真实密钥。
- [x] 在该配置形态下运行同批受控完整闭环，并明确 `external_success=false`。
- [x] 通过用户授权的 Magpie 真实发现 Jev 决策模型；当前适配器对 36 个 taxonomy 问题校验成功，实际模型 jev-1.14.0，证据见 research/magpie-live.md。
- [ ] 真实来源、生成与向量服务联合运行仍未验证；单独 Jev 不代替完整真实闭环。

## 1. 链路与契约

- [x] 阅读 data、execution、research、interface 及相关规范和实现。
- [x] 从 API/页面入口追踪 Worker、采集、后台处理、选证和报告读取。
- [x] `audit.md` 给出链路图、入口主体、产物、就绪条件和手动操作步骤；定时调度排除。

## 2. 边界审查

- [x] 审查首次采集到研究的交接、重复触发和失败恢复。
- [x] 核对原件、不可变版本、分类标签、按需 Fact、向量批次及引用关联。
- [x] 核对增量游标、as_of 与历史取材，确认 MP-02 历史文档漏带 Fact。
- [x] 确认 MP-01 API/Worker 自定义配置路径偏差。
- [x] 两个问题均在服务器最小探针复现；本任务仅记录，不扩大到其他业务修复。

## 3. 远程验证

- [x] 使用 `100.120.14.84` 当前任务隔离目录、数据库和对象存储，复用公共 Python 3.12 工具环境。
- [x] 全新库 `alembic upgrade head` 与 `alembic check` exit 0。
- [x] Jev/classification/provider/worker 113 passed；共享路由/embedding 23 passed。
- [x] 手动采集排队 → 原件入 S3 → parse/index/classify/facts → 处理就绪 → 手动研究 → 本批 Chunk/Fact 进入输入 → 保存 Analysis/Report → 正文/引用/原件/语义检索 API，完整受控 smoke exit 0。保存关联 UUID、哈希和响应。
- [x] 完整 smoke 包含活跃 Run 重复触发返回同一 Run；未完成解析、处理依赖、分类失败与重试等另由既有测试覆盖。
- [x] 补充边界测试的 shell 换行错误导致意外全量运行：642 passed、1 warning。后续七个 node ID 未作为独立 pytest 运行；已记录错误，不重复执行。
- [x] Compose 结构检查在远程从 `.env.example` 创建临时 `.env` 后通过；未构建镜像或部署。
- [x] 保存运行结果和限制，未记录实际密钥；原件/分类/报告等证据只含虚构受控数据。
- [ ] 浏览器渲染与点击验收、长期 Worker 生命周期及真实外部服务联合闭环未验证。未修改前端，不默认扩大构建。

## 4. 输出与复核

- [x] `audit.md` 列出严重程度、位置、触发条件、影响、证据与最小修复建议。
- [x] 明确受控通过与真实未验证的边界，给出就绪判断、恢复入口与修复顺序。
- [x] 独立只读 Agent 已复核 Jev 核心，无确认的新缺陷，见 `research/jev-review.md`。
- [x] 最终全量变更与审查结论的独立复核通过，见 `research/final-review.md`；没有确认的新代码缺陷。
- [x] 更新工程 spec：原生协议/显式角色的失败约定，及 SeaweedFS 多 bucket 卷数耗尽诊断。
- [x] 保存受控证据后清理本任务远程两个容器、匿名数据库卷与任务目录，删除后确认不存在。
- [x] 已展示合并后具体两组方案并获用户“行”确认；功能提交 77c86ff，验收记录、归档与日志按确认方案继续，不推送或部署。其他真实外部验收仍未验证；方案由 `commit-plan.md` 指向后续任务的最终分组。

## 子 Agent 与结束边界

本次使用 Pi 原生 implement、research、check Agent，派发均带显式当前任务路径。主 Agent 统一远程验证与资源清理。真实依赖缺失时只停止受阻外部验收，保留已完成的受控审查结论；其他修复另按审查结果确定范围。
