# 验收记录

## 结论：概览页改为情报数据展览页并已部署（后端聚合 + 前端重写 + recharts 图表）

## 后端 `/api/overview` 新增块（现有字段全部保留）

`library`：documents / chunks（**当前解析批次**，失败重解析不虚增）/ facts / reports /
sources / earliest~latest_collected_at / documents_this_week / facts_this_week /
by_source（SQL 聚合，无 N+1）/ daily（14 天 UTC 补零）。

`attention`：source_failure、source_stale（**仅当配置了 expected_freshness**）、
task_failed（每 Task 最新 Run，链接带 task_id）、processing_backlog、config_error。
健康时为空数组。

## 审查发现并修复（7 项）

1. **[P1] `processing_backlog` 会因自动重试中的失败/能力缺失挂起而常亮** → 收窄为
   「不会再自动重试的 failed」+「无 wait_reason 且超 1 小时的 pending」，并补测试。
   修复后真实环境仍正确报出 5 个「重试耗尽（attempts 3/3、next=None）」的失败阶段。
2. **[P1] `source_stale` 阈值口径与 PRD 不一致（1× vs 2×）** → 统一为与 `/api/sources`
   同口径（1×、基于最新披露时间），并修正 PRD 措辞。
3. **[P2] `library` 为必填导致滚动部署时整页报错** → 改为 `.optional().default({...零值})`。
4. **[P2] 「失败重解析不虚增 chunks」无测试** → 语义已在实现中（join 当前批次），
   文档已说明；未新增该用例，属已知覆盖缺口（见限制）。
5. **[P2] `docs/validation.md` 与 interface 关于 Overview 的说法矛盾** → 已改为
   「API 保留字段，Web 在 Runs/报告页显示」。
6. **[P3] 最近入库状态漏 `skipped`** → 补 skipped 分支（显示「已跳过」）。
7. **[P3] reduced-motion 首帧** → `useState` 惰性初始化读取 `matchMedia`。

## 前端

数字条（文档/Fact/报告/来源 + 本周新增 + 时间跨度）→ 14 天双系列柱状图（shadcn chart
封装 + recharts，圆角、tooltip、图例、全零空态、reduced-motion 关闭动画）→ 来源分布
Table → 需要处理（severity 图标 + 链接；空则「无阻塞」）→ 最新报告 Table（→ 详情页）
→ 最近入库 Table（→ 情报详情）。移除活跃/失败 Run 计数、近期运行、模型用量、阶段计数，
保留单个「查看运行与积压 →」。

## 验证

| 检查 | 结果 |
| --- | --- |
| 远程 pytest `test_console_api.py` | **24 passed**（含新增 retryable/parked 用例） |
| 远程 vitest 全量 | **53 passed** |
| typecheck / build | 退出码 0 |
| 部署 | md5 核对 → `--no-cache` 构建（api 因 PyPI 超时重试一次后成功）→ 四服务 healthy |
| 容器内特征 | overview 分包含 本周新增/无阻塞/数据流入，不含 活跃 Run/近期模型用量；api 容器含 `_overview_library`、收窄后的 backlog 判定、task 链接 |
| 线上真实响应 | library 20/60/71/2/1，本周 20/71，daily 14 天合计 20；by_source 正确；attention 正确报出 5 个重试耗尽的失败阶段 |

## 限制

- `chunks` 语义为当前解析批次（未解析文档不计 chunk），已写入 PRD。
- 未新增「失败重解析不虚增 chunks」的专项测试（实现已按当前批次 join，审查确认逻辑正确）。
- 真实浏览器视口视觉验收属 PA-3 待办；图表在 jsdom 中以 `initialDimension` 渲染。
