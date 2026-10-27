# 执行计划与结果

- [x] 1. 在测试服务器确认旧/新词表指纹差异；新代码已于前一轮经 Owner 批准重建 API/Worker，正式官方 Jev 配置不变。
- [x] 2. 自行读取现有 Magpie 网关配置，仅将网关调用凭据送入服务器私有任务目录，建立 loopback SSH 转发；未导出上游 TypeSafe 密钥。
- [x] 3. 将实际测试部署数据库快照恢复到本任务独立 PostgreSQL，只对一篇真实 SEC 文档执行 classify reprocess。原生 Jev 和实体生成均真实调用，3.429 秒通过。
- [x] 4. 核对 protocol=typesafe、reported_model_name=jev-1.14.0、新词表版本/指纹与阶段 completed。分类前后单独导出，Document 版本/raw hash/解析批次/Chunk 文本与身份未变；没有声称分类自动新建历史批次。
- [x] 5. 同步六项范围收敛、父任务条件与统一未验证项，修正先前模型身份/SEC 数据/uptime 说法。
- [x] 6. 独立复核已完成，证据一致；发现的未来时、归档路径和尾句已按复核意见修正。条件未满足的三个父任务保留活动，不归档。
- [x] 7. 工作结果已提交并推送 `52d5ae8`；未纳入用户自行编辑的 AGENTS.md。收尾任务归档与会话日志随后完成。

## 实际验证入口

在 `100.120.14.84:/tmp/stocks-v0-final-acceptance-closeout`，使用已部署的新 stocks-api 镜像运行 `research/reprocess_live.py`。脚本调用标准 `run_stages_for_documents(..., reprocess=True)`，数据库为独立 loopback 15549 的临时快照；不再对 `/opt/stocks` 正式配置指向的库重复验证。不在本机计算应用指纹或运行应用检查。

## 边界和剩余项

此前直连官方重处理返回 451，旧分类正确保留，不能把 attempt/CLI JSON 输出当作成功。当前 Magpie 成功只证明现存真实文档的分类重处理与实体生成，未新增采集、Fact、向量或研究运行。

三个父任务仍有持续调度/cron/时区、真实应用限流/预算终止、Tavily 完整补证 Run、晚提交资料经变化流入包等证据缺口。Owner 已同意的六项移出范围不能顺带取消这些要求。详见 acceptance.md。
