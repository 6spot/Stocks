# 远程验证记录

所有应用测试、初始化、查询和 API 验证只在 `root@100.120.14.84` 的 `/tmp/stocks-v0-taxonomy-research-flow` 执行。本机只读取、编辑、打包及保存去敏验证产物。

## 环境

- Python 3.12，公共工具环境 `/opt/stocks-test-runtime/tool-venvs/stock-tools`，当前项目 editable 安装指向本任务 `source/backend/src`，执行前实际核对。
- 独立容器 `stocks-test-taxonomy-research-flow-postgres` 与 `stocks-test-taxonomy-research-flow-s3`，分别只绑定 loopback `15547` / `18347`。
- SeaweedFS 为当前任务独立数据目录，`-volume.max=32 -master.volumeSizeLimitMB=32`；随机数据库/Owner/会话凭据仅在服务器私有任务目录。
- Magpie 原生决策调用如启用，经本任务临时 loopback SSH 反向转发；网关凭据仅经私有 stdin 传输，不复制上游密钥，不输出认证值。

## 已执行

| 检查 | 结果 |
| --- | --- |
| 全新空库 `alembic upgrade head`、`alembic check` | exit 0，当前模型与初始化一致；无历史迁移验证 |
| `.venv/bin/python -m pytest -q --tb=short tests/test_classification.py tests/test_classification_jev.py tests/test_execution.py::test_config_loads_manual_nvidia_research_with_company_and_background_queries` | exit 0，**128 passed in 3.00s**；已导出本任务 `TEST_DATABASE_URL`，没有跳过数据库阶段测试 |
| 三文档全受控完整 smoke | exit 0，采集/处理/研究/三文档引用及原件字节和哈希一致；`real_jev=false` |
| 三文档真实 Jev 分类完整 smoke | exit 0，**3 次真实 Jev**，其余模型和来源受控，真实 PG/S3；`external_success=false` |
| 实际数据库 Fact 跳过原因核对 | exit 0，只有公司名和纯行业两篇均为 skipped / NOT_APPLICABLE；没有将未适用的 Fact 阶段计成抽取成功 |
| 任务资源清理 | exit 0，本任务两容器、匿名 PG 卷、整个任务目录和私有凭据文件均确认不存在；SSH 临时反向转发已关闭 |

上述回归覆盖：严格候选结构、名称/别名不能成为输出 ID、完整生成式与 Jev 请求的共享候选语义、实体角色不接收候选暗示、语义/全局指令/顺序变动的指纹、原有错误及持久化边界，以及实际 NVIDIA Task 的加载和查询用途。

源码从当前工作区打包传输；没有本地 pytest、导入/编译/AST、类型或格式检查。未默认全量回归。

## 同批证据

- [真实 Jev 运行摘要](acceptance-summary.json)：模型 `jev-1.14.0`，每篇 36 题，各一次 attempt、无 fallback；三次合计 20,890 tokens、4,445ms，费用未知。
- [全受控模式 manifest](taxonomy-flow-20261026170017-7451ac/manifest.json)：独立的三个 Document、一个适用 Fact、Research/Report 与原件引用关联。
- [真实 Jev 模式 manifest](taxonomy-flow-20261026170028-3be571/manifest.json)、[实际分类与双角色痕迹](taxonomy-flow-20261026170028-3be571/classification.json)、[实际保存的报告](taxonomy-flow-20261026170028-3be571/report.md)。同目录还保存实际 research-input、evidence-package、report-detail、citation-proof 及 Jev 请求；较大文件包含逐字符定位。
- 本次使用虚构 Northstar Silicon/NSIL 文章；没有宣称真实 NVIDIA 新闻或投资研究通过。NVIDIA 示例由实际 Task loader 用例单独验证。
- 报告保留 `no_background_selected`：三篇均通过变化窗口归为 `new_information`；行业正文实际入包且被引用。这不等于专门 background 分组或语义相关性得到验证，更不等于新增全标签精确过滤。

## 执行边界与清理

来源 Feed/文章、实体生成、Fact、embedding 数值及研究响应均受控。固定向量只验证管道；不能用总结的合成文本证明真实模型判断质量。真实来源与所有生成/向量模型联合闭环、浏览器、长期 Worker 及部署没有验证。

最初三次脚本/设施失败及修正见 [execution-notes.md](execution-notes.md)；失败均发生在真实 Jev 调用之前，不并入成功计数。FastAPI TestClient 的 `StarletteDeprecationWarning` 已保留，未为压制 warning 扩大依赖改动。

[cleanup.log](cleanup.log) 记录已删除本任务两个容器、匿名 PG 卷与整个 `/tmp/stocks-v0-taxonomy-research-flow`。其下随机数据库/Owner/会话/网关凭据一并删除，临时 SSH Magpie 转发关闭。公共 Python/工具环境保留，未动其他任务资源；原始 manifest 的“保留供父 Agent 清理”反映捕获时状态，以这份后续清理记录为准。

无生产部署或推送；用户已按项目 Phase 3.4 确认具体分组，功能提交为 77c86ff，验收记录、归档与日志按该方案收尾。

