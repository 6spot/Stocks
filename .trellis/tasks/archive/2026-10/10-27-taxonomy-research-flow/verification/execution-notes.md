# 执行说明

## 服务器、命令与成功记录

所有应用运行只在 `root@100.120.14.84:/tmp/stocks-v0-taxonomy-research-flow`。源码由当前工作区传输到 `source/`，可编辑安装指向 `source/backend/src`，Python 3.12 工具环境复用既有公共目录。每个命令保留非零退出码，未默认全量回归。

先在 `source/backend` 执行 `alembic upgrade head` 与 `alembic check`，均 exit 0；然后运行 README 的明确三个 pytest 目标，**128 passed in 3.00s，exit 0**。

三文档脚本运行前在远程导出本任务私有 `runtime.env`（不记录值），以及独立 loopback S3 endpoint 和仅供无认证隔离 SeaweedFS 使用的 fixture access/secret 值；S3 bucket 由脚本建立。

实际 smoke 命令（从本任务 `source/` 运行）：

```bash
backend/.venv/bin/python .trellis/tasks/10-27-taxonomy-research-flow/research/taxonomy_flow_smoke.py \
  --runtime-dir /tmp/stocks-v0-taxonomy-research-flow
backend/.venv/bin/python .trellis/tasks/10-27-taxonomy-research-flow/research/taxonomy_flow_smoke.py \
  --runtime-dir /tmp/stocks-v0-taxonomy-research-flow --real-jev
```

- 全受控模式：exit 0，`taxonomy-flow-20261026170017-7451ac`，真实 Jev 调用 0。
- 真实 Jev 分类模式：exit 0，`taxonomy-flow-20261026170028-3be571`，真实 Jev 调用 3，其余来源/模型仍受控。模型返回 `jev-1.14.0`，每篇 36 个问题，一次 attempt，无 fallback。
- 后续通过当前任务数据库实际读取 Fact 状态，两个 `should_extract=false` 文档均为 `skip_reason=NOT_APPLICABLE`，断言通过，结果保存 acceptance-summary.json。
- 对保存的 Evidence Package 做有限元数据读取：三组 `role` 都是 `new_information`；原始报告的 `no_background_selected` 保留，不能把行业正文已入包误写成单独 background 分组已入选。

## 三次初始失败

这些是设施/脚本缺陷，不是本次应用实现的通过证据，也未计为成功：

| 尝试 | 实际错误 / 退出码 | 原因与修正 | 真实 Jev 调用 |
| --- | --- | --- | --- |
| 初次 | `NoCredentialsError`，exit 1 | 隔离 S3 fixture 没有导出 access/secret；补充测试用值，并让脚本在 preflight 明确检查 | 0 |
| 第二次 | `API_POST_/api/owner/login_401`，exit 1 | 脚本用了不存在的登录路径；改为实际 `/api/session/login`，并从 session 响应设置 `X-CSRF-Token`，沿用正常认证 | 0 |
| 第三次 | `COLLECTION_FAILED`，exit 1 | 数据库确认采集 Run 为 `COLLECTION_CONFIG_UNAVAILABLE`。脚本 Source fixture 使用错误 id/url/trust tier 及未支持字段；按真实 SourceConfig 改为 source_id/feed_url/publisher/T1/max_redirects，并移除虚构字段 | 0 |

原始日志分别为 `taxonomy-flow.log`、`taxonomy-flow-run2.log`、`taxonomy-flow-run3.log`。修正只涉及本任务研究脚本与测试环境；应用源代码保持已通过 128 项检查的版本。之后先用全受控模式验证脚本契约，再运行真实 Jev 模式，没有重复消耗真实 Jev 来调脚本。

所有 smoke 日志保留 FastAPI TestClient 的 `StarletteDeprecationWarning`；没有将警告写成失败，也未扩大范围更新依赖。

## 安全与清理

真实 Jev 仅由服务器经本任务 loopback SSH reverse forwarding 访问用户已授权的 Magpie。原生请求没有改写答案；网关授权头不进入 HTTP artifact，网关凭据存放于服务器私有文件，未复制上游 key。保存的 models.yaml 仅含固定受控占位 key。

成功结果、状态、原件/引用证明及必要失败日志已复制到仓库任务目录；远程源码、私有凭据、S3 数据、本任务两个容器与匿名 PostgreSQL 卷已清理，临时 SSH 反向转发关闭。公共工具环境未删除，其他任务资源未修改。详见 cleanup.log。
