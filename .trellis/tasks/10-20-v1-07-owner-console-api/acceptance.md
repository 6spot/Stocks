# V1-07 验收记录

- 验收日期：2026-10-22（Asia/Singapore）
- 测试环境：`root@100.120.14.84`，隔离目录
  `/tmp/stocks-v1-07-owner-console-api-20261022`
- Python 3.12.15；`.venv` 指向持久环境
  `/opt/stocks-test-runtime/tool-venvs/stock-tools`
- 数据库：隔离 PostgreSQL/pgvector 容器 `stocks-test-v1-05-postgres`，端口仅绑定
  `127.0.0.1:55472`，数据库 `stocks_v1_07_console_20261022`
- 本机只用于读取、编辑、打包和传输；未在本机运行测试、数据库、Alembic 或 API。
  SSH 与数据库密码只用于临时命令/一次性远端文件，未写入仓库或本记录。

## 实际验证

全新 `public` schema 上执行 `alembic upgrade head` 成功，随后 `alembic check` 输出
`No new upgrade operations detected.`。数据库实际索引核对为：

```text
CREATE INDEX ix_task_runs_scope_sources ON public.task_runs USING gin (scope jsonb_path_ops)
```

| 范围 | 结果 |
| --- | --- |
| `tests/test_console_api.py tests/test_report_api.py` | **22 passed**，exit 0 |
| `tests/test_console_api.py tests/test_report_api.py tests/test_facts.py -k "not slow"` | **55 passed**，exit 0 |
| `tests/test_collection.py -k expected_freshness` | **9 passed**，exit 0 |
| `tests/test_repository.py -k latest_document_follows` | **1 passed**，exit 0 |
| `tests/test_facts.py -k fact_api` | **1 passed**，exit 0 |

唯一警告是第三方 `starlette.testclient` 对当前 httpx 组合的弃用提示；没有 skip 被
记作通过。仓库未配置 ruff/mypy 命令，本任务没有可声称通过的 lint/type 检查。

## PRD 验收条件

| AC | 结论 | 证据 |
| --- | --- | --- |
| AC-1：未认证不能读取报告/原件或操作，Owner 可读取，过期会失效 | 通过 | 匿名报告与原件 `401`；登录后读取成功；损坏 Cookie 与过期会话分别返回稳定 `401`；登录失败窗口限流；登出也要求 CSRF。 |
| AC-2：无更新、Connector 失败与积压可区分，分页有界 | 通过 | Overview/Sources 用例区分 `success`、`no_update`、`partial`、`source_failure` 与后处理积压；每个 Source 独立选最新 Collection Run；Document/Run/Processing/Fact 游标跨页用例通过。 |
| AC-3：重复触发返回同 Run，重试/续扫定位真实记录，结束异常 Run 需确认旧执行停止 | 通过 | 已有活跃 Run 即使 YAML 损坏也返回 `created=false`；失败/已结束 Run retry 保留来源窗口；continue 校验来源 Run；未确认结束返回 `OWNER_CONFIRMATION_REQUIRED`，确认后才取消并允许恢复。 |

## 独立检查后的修正

- 恢复原件描述中的受保护相对 `endpoint`，同时保持不暴露 S3/Provider 地址。
- 将来源新鲜度改为按 `source_updated_at`（缺失时 `published_at`）计算；新增有内容
  但披露时间未知的状态，并单独返回采集时间。
- 将每个 Source 的最新采集 Run 改为按页面 Source 的关联 `LIMIT 1` 查询，新增
  `scope` 的 `jsonb_path_ops` GIN 索引，不再展开全部历史 Run scope。
- 登出强制执行与会话绑定的 CSRF 校验。
- Run/步骤输出、范围、扫描进度、证据包查询统一使用递归安全投影，直接覆盖普通
  `url` 字段中的 query/fragment 脱敏。
- quickstart 的报告列表示例改为 `items` 分页结构，并给私有读取命令补 Cookie。

## 未完成 / 外部限制

- 未对真实 Provider、真实 S3 或跨设备浏览器做验收；这些留到 V1-09 由 Owner 在
  目标环境执行，不把 mock 测试写成外部集成已接通。
- 未验证目标 ARM 环境或公网入口；当前测试服务器为 x86_64。
- 登录失败限流状态保存在单个 API 进程内；当前 Compose 单实例假设成立，多实例部署
  不在本任务范围。
- 受保护 Web 前端属于 V1-08，本任务只交付 API 与可直接检查的文档/命令。

## 清理

本轮验收结束后停止并自动删除了隔离 PostgreSQL 容器
`stocks-test-v1-05-postgres`，远端代码目录
`/tmp/stocks-v1-07-owner-console-api-20261022` 与一次性密码文件也已删除；测试账号
随容器销毁。未删除其他任务或归属不明的资源。
