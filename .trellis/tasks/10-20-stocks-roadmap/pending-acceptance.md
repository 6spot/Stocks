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

- **状态**：已知未修，V0-08 验收时发现并按当时的改动范围记录而未处理。
- **对统一验收的影响**：真实 HTML 页面若命中该结构，Chunk 边界与定位质量会下降，
  进而影响真实闭环的可读证据质量。建议在 PA-1 之前或同时修复。
- **出处**：`.trellis/tasks/archive/2026-10/10-20-v0-08-document-collection-fixes/acceptance.md`
  及 `.trellis/spec/backend/testing-guidelines.md` 的"固定 HTTP 响应"条目。

## 复用说明

补验收轮次开始前，先读本文件与 V0 父任务 `notes`，再决定是否需要新建任务；
不要在 V0/V1 子任务里另起一套"真实闭环已验收"的表述。
