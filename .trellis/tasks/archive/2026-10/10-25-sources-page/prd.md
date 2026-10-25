# 采集源查看页面

## Goal

在控制台新增一个只读的"采集源"页面，让 Owner 能集中查看系统当前接入的全部采集源（Source），以及每个源的基本信息、启用状态、最近一次采集结果与内容新鲜度。当前 `/api/sources` 后端接口与前端 API client 已存在（概览页仅展示前 8 个源的预览），缺一个完整的专属页面与导航入口。

## Background

- 系统的 Source 定义来自 `config/sources.yaml`，通过 `GET /api/sources` 返回，每条记录包含：`source_id`、`name`、`connector`、`publisher`、`trust_tier`、`enabled`、`configured`、`persisted`、`collection`（状态、成功/跳过/失败计数、最近尝试时间、错误、处理积压）、`freshness`（状态、期望新鲜度、最新披露/采集时间）。
- 响应还带顶层 `configuration_error`（sources.yaml 加载失败时的错误信息）与分页字段（`next_cursor` / `has_more`）。
- 前端 `dashboardApi.sources()` 与 `sourcesResponseSchema` 已就绪；导航与路由使用现有 app-shell + react-router 模式。

## Requirements

1. 新增 `/sources` 路由与侧边导航入口（名称"采集源"），登录态内可见。
2. 页面以表格/列表展示全部采集源（使用现有 `dashboardApi.sources()`，遵循分页字段；当前源数量少，一页 200 足够，但 UI 需正确响应 `has_more`，如显示"加载更多"或提示）。
3. 每个源至少展示：名称与 source_id、connector、publisher、trust_tier、启用状态（enabled/configured/persisted 异常时如实标注，如"未配置"）、采集状态徽章、新鲜度状态、最近采集尝试时间、处理积压（待处理/失败数）。
4. 采集或新鲜度存在错误时，展示可读的错误摘要，不静默吞掉。
5. 顶层 `configuration_error` 存在时在页面顶部显著提示。
6. 空状态（无任何源）与加载/请求失败状态使用现有 async-state 组件。
7. 只读页面：不提供新增、编辑、删除源的任何操作入口。

## Constraints

- 复用现有组件与工具（StatusBadge、PageHeader、async-state 面板、labels/format 工具、zod schema），不引入新的状态管理或请求库。
- 不改动后端 `/api/sources` 契约；若渲染中发现字段缺失，回本任务讨论而不是改接口。
- 遵循 console v3 已确立的灰阶视觉与交互风格。
- 不在页面内做采集触发等授权操作（Tool 边界：Source 只是数据来源）。

## Acceptance Criteria

- [ ] 登录后侧边导航出现"采集源"入口，点击进入 `/sources`，页面展示 `config/sources.yaml` 中全部源（含 enabled: false 的源，并明确标示禁用状态）。
- [ ] 每行显示基本信息（名称、source_id、connector、publisher、trust_tier）与采集状态、新鲜度、最近尝试时间。
- [ ] 最近采集失败或存在 last_attempt_error 的源，页面能看到错误信息；processing_backlog 非 0 时可见待处理/失败计数。
- [ ] `configuration_error` 非空时页面顶部出现明显告警；接口失败时展示错误面板；无源时展示空状态。
- [ ] 前端相关测试（页面渲染的关键状态：正常列表、错误提示、空状态）通过；lint/type-check 通过。
- [ ] 测试服务器上完成验证（本机不运行测试）。
