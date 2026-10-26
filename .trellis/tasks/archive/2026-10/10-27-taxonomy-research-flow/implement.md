# 执行计划

用户已明确授权创建任务后直接实施；按当前设计自查后激活，无需再次询问。

- [x] 调查现有词表/模型提示/指纹和 Scope/query，保存 research/research.md。
- [x] 持久化 PRD 与设计，明确全局候选、任务范围、语义提示与证券实体证据的边界。
- [x] 激活任务；实现严格 id/name/description/aliases、共享语义渲染与完整 fingerprint。
- [x] 更新 Jev 和生成式分类提示；保留现有协议/实体失败与留痕。
- [x] 更新默认词表、手动股票 Task、唯一归属文档与分阶段流程。
- [x] 更新必要 fixture/测试；未在本地运行测试、编译或 AST。
- [x] 在指定服务器本任务目录运行最小相关测试：128 passed；同批三文档全受控和真实 Jev 两种完整模式均通过，引用及原件哈希核对成功。
- [x] 改变候选语义后真实 Jev 三次调用成功，均返回 jev-1.14.0；其他来源/生成/向量响应受控，external_success=false。
- [x] 完成两轮独立复核：quickstart Web 字段说明与验收 README 待办时序已修正；无确认的新代码缺陷。spec、验收记录、两种完整运行证据与资源清理均已完成。提交与归档未提前执行。
- [x] 已展示具体两组方案并取得用户“行”确认；功能提交 77c86ff 已完成，验收记录、任务归档与 journal 按已确认顺序收尾；不部署、不推送。

## 验证命令与范围

实际验证目录为 root@100.120.14.84 的 `/tmp/stocks-v0-taxonomy-research-flow/source`，服务、数据库和私有凭据隔离在当前任务根目录。已运行 backend/tests/test_classification.py、test_classification_jev.py 与 test_execution.py 的 NVIDIA 配置用例；没有默认全量。CLI/模型样例、数据库与 API smoke 也都在服务器执行。详见 acceptance.md 与 verification/README.md。

前一任务 Jev 协议和受控完整 smoke 结果只作为已有证据，本任务改提示后运行相关回归；前一任务 MP-01/MP-02 不在本任务修复范围。
