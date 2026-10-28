# 验收记录：真实来源驱动的市场与个股报告闭环

日期：2026-10-28。范围：手动触发的真实来源 → 保存 → 分类 → 检索 → 市场/个股报告；不含定时调度。

## 子任务

| 子任务 | 结果 | 证据 |
| --- | --- | --- |
| MP-01 配置一致性 | 完成并归档 | [verification](../10-27-report-config-consistency/verification.md)，远程 34 passed |
| MP-02 历史 Facts | 完成并归档 | [prd](../10-27-report-historical-facts/prd.md)，回归用例与真实研究 Run |
| 首批真实来源 | 完成并归档，论坛来源阻塞 | [prd](../10-27-report-real-sources/prd.md)，SEC/美联储/CNBC/FRED 真实采集 |
| 报告口径与取材 | 完成并归档 | [prd](../10-27-report-research-scope/prd.md)，`market_intelligence_brief`、`spacex_company_research` |
| 分类依据可见性 | 完成并归档 | [prd](../10-27-report-classification-visibility/prd.md) |
| 真实质量验收 | 完成并归档，含未覆盖项 | [acceptance](../10-27-report-live-quality/acceptance.md) |

真实质量验收的关键结果：采集 Run `c49fdf96` 四来源 0 失败；市场与 SpaceX 两份报告逐条回查原文；真实 Tavily 补证进入报告引用；金额未知保持 null。未覆盖与失败项以子任务验收记录为准，不在此改写为通过。

## 部署镜像重建（2026-10-28）

- 原因：此前修复仅以 `docker cp` 存在于运行容器，容器重建会回退。
- 方式：同步当前 `backend/src`、`frontend/src`、`config`、`compose.yaml` 到 `/opt/stocks`；后端依赖以离线 wheelhouse 构建（`pip wheel` 后 `pip install --no-index`），临时 Dockerfile 构建后恢复原文件；`web` 由 `docker compose build` 重建。
- 结果：`stocks-api`/`stocks-worker` 镜像 ID `03ab4c6bcd67`，`stocks-api-1`、`stocks-worker-1`、`stocks-web-1` 均从新镜像重建并 healthy。
- 校验：容器内 `stocks.worker` 与 `stocks.research.service` 源码标记在位（MP-01/MP-02），`/health` 返回 200，Web 资源包含“分类与实体”文案；容器内加载 7 个来源配置、其中 4 个启用（SEC、美联储、FRED CPI、CNBC）。
- 未重建数据库；`.env` 与持久卷未改动。官方 TypeSafe 配置保持不变。

## 遗留项

论坛/讨论来源、增量窗口续扫、3 份 CNBC facts 超时重试、行情/价格来源，登记在路线图 [PA-4](../../../10-20-stocks-roadmap/pending-acceptance.md)。这些不是本任务的通过项，需后续处理。
