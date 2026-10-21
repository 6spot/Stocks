# 延后的验收项（跨任务登记）

路线图父任务持有的待补验收清单。这里登记的项**不是遗漏**，而是明确延后到统一验收轮次；
任何阶段收尾不得把下列项当作已通过。

## PA-1：V0 真实端到端闭环（V0-07 AC-1）

- **状态**：延后，Owner 决定放到最后统一验收（2026-10-21）。
- **未执行的原因**：测试服务器 `100.120.14.84` 与本工作区都没有模型 Provider 凭据。
  `config/models.yaml` 通过 `${MODEL_API_BASE_URL}`、`${MODEL_API_KEY}`、`${MODEL_NAME}`、
  `${EMBEDDING_MODEL_NAME}` 取凭据，缺失时 `providers.resolve_env()` 直接失败。
  缺 Embedding 就没有真实 pgvector 索引，缺 chat 模型就没有真实 Research Agent。
- **已实测排除的其他阻塞**：对象存储可写可读（本任务自建 seaweedfs）、pgvector 可用、
  SEC Feed 返回 `HTTP 200`、API 只绑 `127.0.0.1`、只读证据 API 与版本/失败边界均已验证。
- **补验收时要做的事**：
  1. 提供一组 OpenAI 兼容凭据，需同时支持 `/chat/completions` 与 `/embeddings`，
     且 embedding 维度与当前固定向量空间一致（`embedding_space.py` 为 1536）；
     或改用自建模型并同步修改向量维度与初始化建表定义（开发阶段直接改当前结构，不写迁移）。
  2. 把 `config/sources.yaml` 的 `owner@example.com` 换成 Owner 认可的真实联系邮箱
     （SEC 对缺少联系身份的自动客户端返回 403）。该邮箱会随请求头发给 SEC，需 Owner 先确认。
  3. 按 `docs/quickstart.md` 顺序跑：真实采集 → 解析 → 索引 → 手动研究 Task →
     `/api/reports` 读取该报告并打开其引用的原件；记录 `run_id`、脱敏计数与实际响应。
  4. 核对 AC-2 在真实数据上同样成立：新版本入库后旧报告仍指向旧片段与旧原件。
- **已归档证据**：`.trellis/tasks/archive/2026-10/10-20-v0-07-evidence-api-acceptance/`
  的 `acceptance.md` 与 `research/verification-2026-10-20.md`。

## PA-2：V0-03 解析器缺陷（正文直接位于 article/main/body 时逐字符换行）

- **状态**：2026-10-22 已修复并验证，不再是待验收项。
- **修复**：`_walk_evidence()` 统一走 `_walk_block()`，并按新增的 `CONTAINER_ELEMENTS`
  （`article`/`body`/`html`/`main`）把容器自身的连续文本与行内元素合并成同一个证据片段。
- **验证**：测试服务器 `100.120.14.84` 隔离目录中 `tests/test_processing.py` 38 passed、
  解析/采集/处理重试/仓库/冲突相关 117 passed、后端全量 475 passed；另取 2 份真实 Fed
  与 2 份真实 SEC 新闻正文复核：修复前 Fed 两页分别出现 18/17 个单字符片段
  （`S\nk\ni\np...`），修复后 0 个单字符片段、来源范围回查 0 处不一致。
- **回归**：`tests/test_processing.py::test_text_directly_inside_container_stays_one_evidence_run`
  与 `::test_inline_text_directly_inside_container_keeps_order_and_ranges`；契约同步到
  `docs/data.md` 与 `.trellis/spec/backend/testing-guidelines.md`。
- **出处**：`.trellis/tasks/archive/2026-10/10-20-v0-08-document-collection-fixes/acceptance.md`
  及 `.trellis/spec/backend/testing-guidelines.md` 的"固定 HTTP 响应"条目。

## 复用说明

补验收轮次开始前，先读本文件、`docs/validation.md` 的「待最终验收」清单与各阶段父任务
`notes`，再决定是否需要新建任务；不要在 V0/V1 子任务里另起一套"真实闭环已验收"的表述。
