# Taxonomy flow smoke（由父 Agent 远程执行）

新增脚本 `research/taxonomy_flow_smoke.py` 改编自前一任务已远程通过的 `manual_pipeline_smoke.py`。本批仅编写及阅读检查，**未运行本地测试、AST、导入、编译，也未执行 SSH、远程命令或模型调用**。`task.py current --source` 返回 none；按派发指定的现有任务路径保存研究产物，没有修改任务状态或产品代码。

只在 `100.120.14.84` 的 `/tmp/stocks-v0-taxonomy-research-flow` checkout 根目录执行。父 Agent 负责当前 schema 初始化、依赖、导出私有 runtime 环境、测试资源和清理。需要 `TEST_DATABASE_URL` 指向 `127.0.0.1:15547`，`S3_ENDPOINT_URL` 指向 `127.0.0.1:18347`，以及已有的 `S3_ACCESS_KEY` / `S3_SECRET_KEY`。数据库必须没有 queued/running Run 或未完成 Document 队列；脚本不清库、不建表。

```bash
cd /tmp/stocks-v0-taxonomy-research-flow
PYTHONPATH=backend/src .venv/bin/python \
  .trellis/tasks/10-27-taxonomy-research-flow/research/taxonomy_flow_smoke.py \
  --runtime-dir /tmp/stocks-v0-taxonomy-research-flow
```

可选真实 Jev：父 Agent 在该 runtime 根目录放置私有、非软链、权限仅 Owner 可读写的 `gateway-auth.json`，结构包含 `api_key`；不要输出文件内容。增加 `--real-jev`：

```bash
PYTHONPATH=backend/src .venv/bin/python \
  .trellis/tasks/10-27-taxonomy-research-flow/research/taxonomy_flow_smoke.py \
  --runtime-dir /tmp/stocks-v0-taxonomy-research-flow --real-jev
```

脚本始终请求 `typesafe/jev-latest`。真实模式只把当前产品生成的 Jev `/systemone` 请求转发至固定的 `http://127.0.0.1:13425/v1/systemone`，关闭代理环境继承和重定向，每次最多 45 秒、总计最多三次，不重试、不替换答案，实际响应字节交给当前严格 adapter。其余实体、Fact、embedding、research 响应仍明确受控；来源只用虚构 public HTTPS 地址和 SafeFetcher 的显式允许 resolver。无真实证券主数据、来源或外部 Tools。

同一采集 Run 保存三个虚构 HTML Document：Northstar Silicon + 明示 NSIL 的数值财报；同公司仅名称、无代码和数值的定性评论；无公司和代码的半导体行业背景。受控 Jev 对三篇均返回 `should_extract=true`，后两篇抽取返回空 Facts；真实模式允许它们按实际分类跳过或完成抽取，数值篇必须抽取出 42 million USD。逐文档检查 version=1、raw SHA256、S3 原件、parse batch/chunks、四阶段、规范标签、精确实体、模型/词表/实体原文引用留痕、vector 行和 Fact。真实模式保留真实标签组合，仍要求规范 ID、预期 company/industry 主分类，以及明确的 semiconductors/US 关联；偏离会失败，不修正模型答案。

研究复用当前 `saved_evidence_brief`/`research_writer` 的真实文章 schema 和根配置，显式分开公司/行业查询；确定性向量仅验证检索管道。三篇都必须出现在研究输入、冻结 evidence package 和最终报告引用中，数值 Fact 必须进入冻结包。逐个报告引用打开 evidence API 和原件/下载 API，核对同版本内容及哈希。中文总结区分公司与行业证据、事实和推断，并明确合成数据局限。

每次输出到 runtime 的 `artifacts/taxonomy-flow-<unique>/`：`manifest.json`、生成的无密钥 YAML、每篇 `jev-request-*.json`、`classification.json`、`research-input.json`、`controlled-research-response.json`、`evidence-package.json`、`report-detail.json`、`citation-proof.json`。manifest 单独记录 `real_jev` 和 `real_jev_call_count`，即使真实模式成功也始终为 `external_success=false`。失败仅输出阶段、错误类型/安全断言代码；保留本批 bucket、数据库记录和产物给父 Agent。父 Agent 可用外层 `timeout 300s` 约束整批时间；脚本内文档调度最多 40 次，真实请求最多 3 次。

剩余限制：尚未执行，父 Agent 需要在最终产品补丁同步后有界运行；API 使用 TestClient、Worker 使用真实单轮入口，未启动浏览器或独立服务进程；受控向量不证明真实语义排序，受控实体/抽取/总结不证明模型质量。真实模式仅验证 Jev 角色，不能视为真实全部外部闭环。每次会创建一个唯一测试 bucket，重复执行的容量和清理由父 Agent 管理。
