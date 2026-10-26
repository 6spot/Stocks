# 类型与状态规范

使用 TypeScript 表达组件 Props、API 响应和可空字段。网络响应在入口处处理未知值、错误状态和时间格式，组件内部使用已确定的类型；不要用 `any` 或未经核对的强制断言掩盖接口不一致。

`src/api/client.ts` 把响应先作为 `unknown` 读取，再用 `src/api/schemas.ts` 中的 Zod
Schema 解码。成功响应不符合当前 Schema 时显示兼容性错误，组件不接触未验证 JSON。
`fetch` 只存在于 API 边界，测试可以 mock 该模块，不能绕过运行时解码。

短暂交互状态留在使用它的组件附近；多个组件需要共享时才提升状态。远端数据以 API 返回为准，避免在多个组件中维护相互独立的副本；派生值优先由已有数据计算。选择缓存或全局状态库时先证明跨页面共享或同步需求，不为单个页面引入额外依赖。

修改跨 API 与 UI 的字段时，同时核对服务端响应、前端类型、空值和错误路径；相关契约的唯一归属仍在 `docs/` 对应领域文档。

从后端取得的日期和时间为 ISO 8601 字符串时，只在 API 解码边界转换为 UI 所需表示；`published_at`、`source_updated_at`、`collected_at` 和报告 `as_of` 不得合并成一个含糊的“更新时间”。Document 版本、来源标识、片段位置和可空字段应保留为显式类型，避免字符串拼接后丢失追溯信息。

响应中的 `completeness = partial` 必须继续作为部分内容显示，并保留安全的错误类别；不能因为 UI 成功渲染就把它视为完整证据。

## 关联数据的入口校验与稳定编号

### Scope / Trigger

API 响应同时含实体列表和关联 ID，或多个视图共享编号时适用。业务字段与研究约束唯一归属在 [研究设计](../../../docs/research.md#analysis-与-report)，不要在组件内另设宽松协议。

### Signatures

`reportDetailSchema` 解码 `GET /api/reports/{id}`；服务端在 `validate_analysis_content(output, allowed_refs)` 校验同一文章关联，`render_report(..., evidence=...)` 生成导出。

### Contracts

Zod 入口同时检查字段形状和关联完整性，组件只消费已通过的结果。编号从冻结输入按 `(document_id, chunk_id)` 首次出现顺序派生，正文、弹窗、清单与导出使用相同规则；开合、筛选和重复输入不能改变编号。

### Validation & Error Matrix

| 输入 | 处理 |
| --- | --- |
| 非空等字段约束或章节关联不满足领域协议 | API 解码失败，显示既有兼容性错误；禁止静默忽略未知关联或覆盖重复 ID |
| 同一合法引用在冻结输入中重复出现 | 同一编号，清单不增加第二行 |
| 合法空发现文章、发现跨章复用 | 接受，保持领域协议允许的边界 |

### Good / Base / Bad Cases

Good：`[A, A, B]` 在所有视图中为 `A=E1, B=E2`。Base：空证据时呈现受限文章。Bad：用 `Map` 覆盖重复发现 ID 后继续显示部分内容。

### Tests Required

契约变更覆盖解码拒绝路径与合法空值；稳定编号用重复输入核对章节、发现、弹窗、清单与导出。相关测试为 `src/api/report-analysis.test.ts`、`research-page.test.tsx` 和后端 `test_article_output.py`，仅在指定远程环境执行。

### Wrong vs Correct

Wrong：`index + 1` 直接编号原始输入数组，另一个视图却先去重。Correct：先按引用身份去重，再派生统一编号；例如 `[A, A, B] → [A, B] → [E1, E2]`。
