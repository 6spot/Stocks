# 设计：分类与实体依据可见性

## 现状结论（先看现状）

**后端已具备全部所需数据与语义，本任务不改后端：**

- `GET /api/documents/{id}` 的 `_document_payload` 已返回 `classification` JSONB
  （`backend/src/stocks/api.py:2235`），包含：
  - 标签：`category`、`event_type`、`topics`、`related_industries`、`regions`、
    `should_extract`（均为词表 id，存储值）。
  - 实体与依据：`companies`、`symbols`、`reason`，以及
    `reason_or_evidence_ref.entity_evidence[]`——每条 `{kind: company|symbol, value, quote}`，
    由 `normalize_entities()` 强制验证 quote 确实出现在原文且覆盖全部实体
    （`backend/src/stocks/classification/jev.py:131`），不存在无依据的推断实体。
  - 模型身份与边界：`method=decision_model`、`model_ref/model_alias`、
    `requested/reported_model_name`、`taxonomy_version`、`taxonomy_fingerprint`、
    `classified_at`、`attempts[]`（按 `role` 区分 `classification`=Jev 标签判断与
    `classification_entities`=生成模型实体抽取，含各自模型名与成败）、
    `reason_or_evidence_ref.truncated/input_characters`。
- 失败语义已正确：`_persist_failure`（`classification/service.py:372`）只写
  `DocumentProcessingState.status=failed` + `error`，**不删除旧的成功 classification**；
  processing 状态由 `processing_stages` 返回（含 attempts/wait_reason/error）。
- 重处理入口已存在：`POST /api/processing/retry`（Owner 会话 + CSRF），前端
  Runs 页 `processing-panel.tsx` 已在用。

**缺口仅在前端：** `DocumentDetails`（`frontend/src/components/document-details.tsx`）
完全不渲染 classification；处理状态表格无重试操作。

## 方案

### 1. 文档详情新增「分类与实体」区块（前端）

在处理状态与结构化 Fact 之间插入 `<ClassificationCard>`（新组件
`frontend/src/components/classification-card.tsx`），数据全部来自已返回的
`document.classification` 与 `processing_stages` 中的 classify 行：

- **无 classification 时按处理状态区分语义**：
  - classify 状态 `pending/waiting` → 「分类尚未完成，等待后台处理」；
  - `failed` → 显示阶段错误码/消息，并注明「之前成功的分类结果（如有）仍保留在下方/上方」；
    若同时存在旧 classification，两者并列展示（真实语义：失败是处理状态，不抹掉旧结果）；
  - `missing` → 「尚未分类」。
- **有 classification 时展示**：
  - 标签行：`category`、`event_type`（可为空）、`topics`/`related_industries`/`regions`
    背景标签组。背景组带固定说明「背景相关性标签，不表示文中提及公司或代码」
    （对齐词表 instructions 的契约，防误读）。
  - 实体行：`companies` 与 `symbols` 分开两组 Badge；**只展示存储值，不做任何推断**
    （无 symbol 就不显示 symbol 组）。`should_extract` 显示为「已判定可抽取结构化事实 /
    判定无需抽取事实」。
  - 实体依据表：逐条 `entity_evidence` 的 `kind + value + quote`（原文引用，等宽样式）。
    这是「明确实体及其原文依据」的验收核心。
  - `reason`：模型分类理由原文。
  - 输入边界：`truncated=true` 时显示「分类输入已截断（N 字符）」。
  - 模型身份与词表：`method`、按 `role` 分列的两次模型调用（角色名 + reported 模型名 +
    状态），`taxonomy_version`，`classified_at`。固定说明「标签与实体为模型判断，
    不是已核实的投资事实」——不冒充结论。
- 标签值展示词表 id 原文（存储即 id）；不把词表 name/description 复制进前端
  （避免第二份词表副本漂移）。

### 2. 处理状态表格增加受控重试（前端）

`ProcessingStages` 增加可选 `onRetry?: (stage: string) => void` 与
`retryPendingStage?: string | null`：仅 `status === "failed"` 的阶段渲染「重试」按钮，
调用方（DocumentDetails）复用现有 `dashboardApi.retryProcessing(documentId, [stage])`
与 `OWNER_CONFIRMATION_REQUIRED` 确认流（照抄 processing-panel 的处理方式），
成功后刷新文档详情。`running/pending/completed` 不提供按钮——重跑已完成阶段属于
`reprocess` 显式操作，本任务不新增入口。

### 3. 不做的事

- 不改后端 API/模型/分类服务（数据与语义已满足验收）。
- 不建分类版本历史、人工标注、自动重分类；不新增词表前端副本。
- 不在列表页（/api/documents）加分类展示——验收目标是文档详情。

## 数据流

```text
GET /api/documents/{id}
  → document.classification (JSONB, passthrough) + processing_stages[classify]
  → ClassificationCard（含失败/无结果语义）
  → 「重试」(仅 failed) → POST /api/processing/retry {document_id, stages:[classify]}
  → 刷新 document detail resource
```

## 兼容与回滚

纯前端改动 + 前端测试；`classification: z.unknown()` schema 已存在，无需改 zod。
回滚 = 还原组件文件。部署侧沿用现有 web 镜像构建产物更新流程。

## 验证

1. 远程隔离目录：`npm run typecheck` + `npm run test`（新增 classification-card 与
   document-details 重试按钮的行为测试）+ `npm run build`。
2. 部署环境真实页面核对：构建产物部署到 web 容器后，用真实 Document 验证
   - 有实体的文章（Oracle 案例：companies 与 symbols 并列、每条 quote 可读）；
   - 无公司的 FRED 结构化文档（companies/symbols 为空不显示推断，背景标签正常）；
   - 一份 classify 失败或 pending 的文档（状态语义正确）；
   - API 层 curl 断言 classification 字段真实返回。
