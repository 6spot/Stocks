# 验收记录

## 结论：两项真实样本均未取得，保留在 PA-3

本任务按“主动采集真实样本”的授权执行了有界探测，未取得合格样本。**不以受控 fixture、
模板噪声或窄范围观察替代真实样本**，因此两项验收仍为未完成。

详细证据、逐来源结果与排除原因见 [probe-2026-10-24.md](research/probe-2026-10-24.md)。

## 实际执行

| 探测 | 范围 | 结果 |
| --- | --- | --- |
| 库内真实文档线上对比 | `stocks_both` 22 份 `sec_press_releases` | 字节 22/22 不同，归一化正文 22/22 完全一致 |
| 单文档行级 diff | SEC 2026-107 | 唯一差异为尾部防爬 `<script src>` 随机串 |
| 结构化来源 | Apple CIK JSON | 163917 字节、SHA-256 与库内完全一致 |
| 结构化来源 | Treasury `debt_to_penny` | 可达，逐日不同值为不同 `record_date` 记录 |
| Feed 声明 | Fed / SEC 官方 RSS | `<source>` 标签均为 0 |
| 聚合器 | Google News RSS | 有 `<source url>`，但只给 `news.google.com` 聚合跳转 |
| 转载站 | benzinga、streetinsider、globenewswire、investing、marketwatch、barrons、fool、nasdaq、businessinsider、yahoo | 403 / 401 / 404 / 超时，均未取得可解析页面 |
| 区域 Fed | St. Louis / New York / Philadelphia | 可达，canonical 均指向自身域名 |
| 其他 Feed | BLS、Treasury 新闻、White House、CFTC、FDA、Federal Register、Congress.gov、GovInfo | 部分可达但无正文级修订；部分 403/超时 |

## 判定

- **真实来源转载与冲突：未完成。** 现有实现只按显式声明（`<link rel="canonical">`、
  文章 URL、Feed `<source>`）合并，不做跨站正文相似度推断；本次未找到任何“页面显式声明
  指向另一来源同一原件”的真实样本。
- **真实跨报告版本/指标修订与晚到资料：未完成。** 22 份真实 SEC 文档的字节变化全部是
  防爬脚本随机串，正文 680/646/721 等词数逐一相同；若据此声称“真实修订已验证”，即为把
  模板噪声冒充来源更新。

## 需要 Owner 决定或提供

1. 转载样本：指定一个已知会被他站转载、且该站声明 canonical 的来源；或明确是否把
   Google News 聚合跳转解析纳入采集能力（属产品功能扩展，不在本任务范围）。
2. 修订样本：允许在更长观察窗口内持续轮询真实来源，或指定一个已知会改稿/发勘误的来源。

## 环境与清理

- 全部探测在 `100.120.14.84` 执行；未运行清表测试，未向 `stocks_both` 新增数据。
- 为尝试真实采集，临时把 `/tmp/stocks-v2-acceptance-20261023/config/v2-acceptance-sources.yaml`
  改为 SEC RSS；采集结果显示既有条目全部 `skipped`（内容未变），该配置已逐字还原为
  `sec_edgar_submissions`（`http_api`，4 个 mapper 字段），库内 `sources` 表仍只有该来源。
- 未新增容器、未新增依赖、未修改产品代码。
