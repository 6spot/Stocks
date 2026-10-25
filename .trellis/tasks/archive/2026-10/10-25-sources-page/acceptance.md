# 验收记录

## 结论：采集源查看页面完成，远程验证通过

## 交付内容

- 新增 `frontend/src/features/sources/sources-page.tsx`：只读的采集源页面。
  - 表格列：来源（name + source_id）、Connector、发布者、信任级别（`trustTierLabel`：T1·官方原始 / T2·原始发布者 / T3·二手媒体 / T4·未核实）、状态（已启用/已禁用/未配置/无历史数据徽章组合）、采集状态（StatusBadge + 行内错误小字）、新鲜度（徽章 title 含最新披露/最近采集时间）、最近尝试时间、积压（待处理/失败）。
  - 顶层 `configuration_error` 渲染 ErrorPanel 告警；`has_more` 显示截断提示；Loading/Error（带重试）/Empty 三态复用现有 async-state 组件；无任何修改或触发采集的入口。
- `frontend/src/features/sources/sources-page.test.tsx`：6 个用例（正常列表含禁用源/行内错误/积压/信任级别、配置告警、空状态、has_more、请求失败），mock 方式与 intelligence-page 测试一致，fixture 附 schema safeParse 自检。
- `frontend/src/app/router.tsx`：`/sources` lazy 路由；`frontend/src/app/app-shell.tsx`：导航"采集源"（Rss 图标）；`frontend/src/lib/labels.ts`：新增 `trustTierLabel`。

## 设计取舍

- 不改后端 `/api/sources` 契约，前端复用 `dashboardApi.sources()` 与既有 schema。
- 页面只读：Source 的变更仍走 `config/sources.yaml`，不在 UI 提供增删改或采集触发（Tool/授权边界）。
- 启用状态用单列徽章组合而非多列，避免 9 列表格过宽；错误信息以行内小字 + title 全文展示。

## 验证（测试服务器 100.120.14.84，隔离目录 /tmp/stocks-sources-page-20261025，已清理）

| 检查 | 结果 |
| --- | --- |
| `npm run typecheck`（tsc -b） | 退出码 0 |
| `npx vitest run src/features/sources/sources-page.test.tsx` | 1 文件 6 passed |
| `npx vitest run`（全量前端） | 11 文件 40 passed |
| `npm run build`（vite build） | 退出码 0 |

## 限制

- 真实浏览器视口下的视觉验收未做；9 列表格在窄屏可能偏挤，如需移动端单列化按其他页面模式后续补充。
- `enabled=null`、`configuration_error` 仅 code 无 message 等分支已由测试 fixture 覆盖，未在真实接口逐一核对。
