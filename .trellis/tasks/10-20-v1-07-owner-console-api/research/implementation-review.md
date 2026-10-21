# V1-07 实现复核

- 日期：2026-10-21
- 范围：单 Owner 认证、控制台查询、异常 Run 操作与来源/处理状态
- 依据：`docs/interface.md`、`docs/execution.md`、`docs/data.md`、
  `docs/validation.md` 及当前后端实现

## 实际缺口

- `backend/src/stocks/api.py` 未加认证，除报告、Document 和原件外没有
  Overview、Sources、Tasks、Runs、处理积压、Fact 单条详情或检索 API。
- `create_or_get_run()`、`end_run()`、`retry_run()`、`reopen_stage()` 和
  `run_stages_for_documents()` 已落地，控制台无需另建恢复服务。
- Source 配置尚无 `expected_freshness` 字段。不能仅凭采集时间推断来源过期。
- 现有报告读取 API 已按实际引用版本读取；本任务只在最外层补访问控制，不改变
  历史证据语义。

## 认证与会话

单个 Owner 使用环境变量中的密码和独立的签名密钥。API 在登录成功后签发带
`issued_at` / `expires_at` / `nonce` 的短期 HttpOnly、SameSite=Strict Cookie。
密码以恒定时间比较；Cookie 用 HMAC-SHA256 签名，过期或签名不匹配均拒绝。

登录页和 `/health` 公开，其他 HTTP 路由统一由应用级依赖保护，包括原件下载。
Cookie 认证的状态变更还要求 `X-CSRF-Token`，该值与签名会话绑定。错误响应只含
稳定错误码与安全消息，不返回口令、Cookie、签名密钥或内部异常。

`OWNER_PASSWORD`、`OWNER_SESSION_SECRET` 与 `OWNER_SESSION_TTL_SECONDS` 从
环境读取。生产/跨设备使用时应设置长随机值；示例文件只放占位符，不提交真实值。

## API 边界

- 列表使用有界分页。时间序列使用 `(timestamp, UUID)` 游标，并以多取一行判断
  `has_more`，不使用 offset 或无界扫描。
- Document/Fact 继续复用已保存版本与现有查询入口；列表增加游标而不破坏旧参数。
- Overview 汇总最近 Report、Run、Document、采集错误、来源配置快照和四阶段积压。
- Sources 合并 YAML 配置定义与已持久化 Source，分别返回配置状态、最近成功采集
  和更新预期。`expected_freshness` 未配置时状态固定为 `unconfigured`，不推断过期。
- Tasks 返回去敏的持久 Task 快照及活跃/最近 Run；手动触发复用
  `create_or_get_run()`，重复请求返回同一活跃 Run。
- Runs 列表与详情返回固定窗口、检查点摘要、有序步骤和安全模型调用信息。
- 处理积压按 Document 和阶段返回；恢复操作委托给现有
  `run_stages_for_documents(execute=False)`，只重新排队，由 Worker 执行。
- `POST /api/tasks/{task_id}/runs` 触发；`POST /api/runs/{run_id}/retry` 创建重试；
  `POST /api/runs/{run_id}/continue` 触发该 Task 的正常后续 Run，使现有执行逻辑
  接上最新有效未完成检查点；`POST /api/runs/{run_id}/end` 必须显式传入
  `confirm_stopped=true`。

## 不变边界与验证

不新增表、迁移、多租户权限、Source 监控服务或前端。开发库可直接按当前模型重建。
认证测试使用真实 PostgreSQL 与 `TestClient`，覆盖未登录、过期、CSRF、重复触发、
确认结束、来源状态和有界分页。所有运行检查只在测试服务器隔离目录执行。
