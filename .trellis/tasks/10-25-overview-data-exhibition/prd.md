# 概览页重构：从系统运维面板改为情报数据展览页

## 定位

概览回答「我积累了哪些情报、是否新鲜、有什么需要我处理」，不是「系统运行状态」。
运维明细（Run 列表、模型用量、处理积压）全部归 Runs 页。

## 后端：扩展 /api/overview（只加字段，不改现有语义）

新增 `library` 聚合块（确定性计数，单次会话内多条聚合查询）：

```jsonc
"library": {
  "documents": 20,            // 当前 Document 总数
  "chunks": 60,               // 当前解析批次的 Chunk 总数
  "facts": 71,
  "reports": 2,
  "sources": 1,               // 有数据的来源数
  "earliest_collected_at": "...", "latest_collected_at": "...",
  "documents_this_week": 20,  // 最近 7 天（UTC 滚动窗口）
  "facts_this_week": 71,
  "by_source": [ { "source_id": "sec_press_releases", "name": "...",
                   "documents": 20, "facts": 71,
                   "last_success_at": "...", "freshness": "fresh|stale|unknown",
                   "expected_freshness": "15m|null" } ],
  "daily": [ { "date": "2026-10-24", "documents": 20, "facts": 71 } ]  // 最近 14 天，缺失日期补 0
}
```

`attention` 块（仅需要处理时非空）：

```jsonc
"attention": [
  { "kind": "source_stale|source_failure|task_failed|processing_backlog|config_error",
    "severity": "warning|error",
    "message": "...", "source_id?": "...", "task_id?": "...", "link": "/sources|/runs?..." }
]
```

规则（确定性、不用模型）：
- `source_failure`：来源最近一次采集失败。
- `source_stale`：有 `expected_freshness` 且距最近成功超过该间隔的 2 倍；无预期新鲜度不报。
- `task_failed`：某 Task 最近一次 Run 为 failed。
- `processing_backlog`：存在 failed 或超过 1 小时仍 pending 的处理阶段。
- `config_error`：sources/tasks 配置读取失败。
全部为空时 `attention` 为 `[]`（前端显示一行「无阻塞」）。

保留现有字段（runs/reports/research/processing/recent_documents）以避免破坏其他调用方，
但**前端概览页不再渲染 runs/usage/processing 明细**。

## 前端：概览页重写

1. **情报存量数字条**（无框一行）：文档 / Fact / 报告 / 来源 四个主数字 + 副行
   「本周新增 文档 N · Fact M」+「时间跨度 YYYY-MM-DD ~ YYYY-MM-DD」。
2. **数据流入**：shadcn chart（新增 recharts 依赖 + `components/ui/chart.tsx` 标准 shadcn 封装）
   - 最近 14 天每日新增文档 + Fact 的双系列柱状/面积图，带 tooltip 与图例。
   - 无数据日期补 0，图例明确「文档 / Fact」。
3. **来源分布 Table**：来源 / 文档 / Fact / 最近成功 / 新鲜度（Badge）。
4. **需要处理（attention）**：仅当非空渲染；每条一行（severity 图标 + message + 跳转链接）。
   为空时一行灰色「无阻塞」。
5. **最新报告 Table**：标题 / 生成时间 / 独立原件 / 缺口数 → 点击进 `/research/<id>`。
6. **最近入库 Table**：标题 / 来源 / 采集时间 / 处理状态 → 点击进情报页详情。

移除：活跃 Run 计数、失败 Run 计数、近期运行列表、模型用量明细、处理阶段计数卡。
（保留一个「查看运行与积压 →」链接到 /runs。）

## 契约与验证

- `docs/interface.md` 的 Overview 描述同步更新（数据展览定位 + attention 语义）。
- 后端测试：`tests/test_console_api.py` 增补 library 计数、daily 补零、attention 规则
  （source_stale 无预期不报、task_failed、config_error）用例。
- 前端测试：概览渲染数字条/来源表/attention；attention 为空时显示无阻塞且无 warning 行。
- 远程 typecheck/build/vitest；部署用 `--no-cache` + 容器内特征 grep（现有流程）。

## 不变

其他页面、API 现有字段、证据语义、reduced-motion。
