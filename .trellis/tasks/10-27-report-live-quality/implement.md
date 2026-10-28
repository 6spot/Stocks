# 实施计划：真实来源到报告的端到端质量验收

前置确认：MP-01/MP-02 已修复（commits `0432e6a`/`be6764c`，远程回归通过）；首批真实来源已接入（`018f4a3`）；报告口径已配置（`d2c0fd8`）；分类可见性已验收（`b35acea`）。

## 步骤

1. [x] **联合采集**：在部署环境以标准入口（`stocks.collect` CLI 或触发采集 Task）跑一轮真实采集，记录 Run id 与各来源结果。
2. [x] **处理状态**：确认 parse/classify/facts/index 全部到达稳定状态，记录分布。
3. [x] **市场简报**：手动运行 `market_intelligence_brief`，记录 Run/Report/as_of/模型/usage/耗时/缺口。
4. [x] **个股报告**：手动运行 `spacex_company_research`，同上记录。
5. [x] **内容核查（市场）**：逐条抽取关键结论，核对引用、数值/单位/期间、事实/观点/推断标注与缺口陈述。
6. [x] **内容核查（个股）**：同上；专项确认 SpaceX 直接信息与行业背景的区分。
7. [x] **取样专项**：论坛/评论观点样本、转载或冲突、历史 Facts 进入研究输入（MP-02）、迟到资料标注、关键遗漏。
8. [x] **补证边界记录**：本轮未启用 Tavily 补证 → 记「未覆盖」。
9. [x] **汇总清单**：`acceptance.md` 汇总通过/失败/未覆盖/排除，勾 PRD，提交并归档。

## 执行结果

详见同目录 `acceptance.md`：真实采集 `c49fdf96`、市场简报 `e3a6b8fe`/`fe3ea7e6`、
SpaceX `62fa1697`/`d1546495`、Tavily 补证 `fcfc59c4`/`ca8e39e4`。执行中发现并修复两处缺陷
（不可引用 Chunk 泄漏进模型输入、Tool 来源被 enabled 拦住），research 超时 300s→600s。

## 验证环境

- 测试服务器 `100.120.14.84` 部署环境（`/opt/stocks`）为真实运行实例；本轮验收直接使用其标准入口，不建临时替代部署。
- 全部数据库查询经 `docker exec stocks-postgres-1 psql`；API 断言经 Owner 会话 curl；页面核对可复用 headless Chromium。
- 凭据仅从 `/opt/stocks/.env` 现场读取，不写入任何任务记录。

## 回滚

无代码变更；`acceptance.md` 为唯一新增任务文档。
