# 后端质量与验证

## 代码审查

- 遵循 Python 3.12、`backend/pyproject.toml` 中的依赖约束和现有模块边界。先复用现有配置、会话和外部访问入口，再决定是否新增抽象。
- 改动公开接口或数据契约时同步修改唯一归属文档、调用方和必要测试；只改工程惯例时修改本目录的规范。无关代码不顺手重构或格式化。
- 凭据只从环境变量或安全配置取得；外部返回值按数据处理，不能扩张操作权限。

## 测试只做必要范围

**不默认全量测试。** 先识别本次改动影响的行为和关键边界，运行能验证它们的最小测试文件或 `-k` 用例。相关检查通过后即可停止；只在影响范围较广、出现相关失败或存在明确回归风险时扩大范围，并说明原因。不要无故重复运行已通过的检查。

简单可逆改动、纯文档修改无需机械新增或运行代码测试。新增测试应验证实际行为或外部边界，不写只复述实现、重复已有覆盖或没有收益的测试。现有参考：`backend/tests/test_s3.py` 用 Mock 验证存储调用，`backend/tests/test_providers.py` 用 `httpx.MockTransport` 验证 HTTP 回退。

**所有测试与验证必须通过 SSH 在当前任务的隔离远程目录执行，测试服务器唯一入口为
`root@100.120.14.84`。** 本机不运行 pytest、数据库、Alembic、API、Docker 或任何
运行时检查；SSH 不可用时保持“未验证”，不得回退到本机。完整流程见
[测试环境](testing-guidelines.md)。

```bash
ssh root@100.120.14.84
cd /tmp/stocks-v0-XX-<task-slug>-<run>/backend
ln -s /opt/stocks-test-runtime/tool-venvs/stock-tools .venv
.venv/bin/python -m pytest -q tests/<相关文件>.py
# 必要时再加 -k <用例筛选>；不得在本机执行
```

上面是远程命令格式，不是每次都要运行的清单。数据库结构、API 或部署变更按受影响
范围在测试服务器追加新库初始化、接口或 Compose 检查。仓库不在 GitHub Runner 或
其他非指定机器上运行测试。交付时只报告测试服务器上的实际执行结果及尚未验证的部分。

## 验收身份与完成判定

真实模型验收核对持久阶段 `status`、`model_calls[].protocol`、请求/实际模型与配置指纹，不能以 `method=decision_model`、有 attempt 或 CLI 输出推断成功。HTTP 451 的失败仍须保留失败状态和前次成功分类；容器 uptime 也不能替代实际调度触发、应用限流和预算终止记录。分类的当前 JSONB 与解析批次是不同契约，重分类不创建新解析批次；需要前后对照时在隔离验收中显式导出 before/after。参见 `stocks.classification.service` 与 `tests/test_classification_jev.py` 的持久化断言，产品定义仍归领域文档。

## 阶段复用回归

补证和后台处理共用阶段领取。回归应同时覆盖新执行、已完成而不可再次领取、显式不适用跳过、依赖跳过和真实等待/失败；不能只以新 Document 的顺序成功路径证明复用正确。`run_stage_now()` 返回 `None` 时读取持久状态，已完成只跳过当前阶段；`skipped` 需要检查其明确原因，不能将依赖未满足当作成功。参见 `tests/test_research_supplement.py` 的阶段复用测试，业务语义以 [研究设计](../../../docs/research.md) 和 [数据设计](../../../docs/data.md) 为准。

## 外部采集与真实来源

外部采集改动至少验证配置边界、URL/DNS/重定向/超时/响应大小、内容类型、Feed 解析安全、单条失败隔离、相同字节复用与变化字节版本化。测试默认使用 `httpx.MockTransport` 和注入的对象存储；真实联网只用于阶段验收，不能替代固定响应的安全边界测试。

真实来源验收必须核对数据库与对象存储：正文字节长度和 SHA-256 一致，`published_at`、`collected_at`、publisher、trust tier、完整性和错误字段符合实际取得内容；同一 Feed 第二次运行不得制造重复版本。记录日期、来源入口、计数、首条脱敏样例和实际命令，不记录响应正文、凭据或完整查询 URL。

声明联系身份是真实来源访问契约的一部分。SEC 对缺少联系邮箱的自动客户端返回 `HTTP_STATUS` 403；配置 `user_agent` 必须采用 `Client/Version (contact@example.com)` 形式，并在真实运行前替换示例邮箱。遇到真实来源状态变化时，先区分反爬/访问政策、网络边界和代码缺陷，再决定修复。

来源级代理只作用于显式配置 `proxy_url` 的 Source，不得静默改变其他来源的直连路径。代理模式下目标地址仍先经同一代理的 DoH 解析并校验；但最终连接由代理解析，不能宣称固定 IP 防重绑定。代理值、userinfo 和真实环境变量不得进入 Document、日志、错误详情或测试快照；缺失/非法代理只使该来源失败。真实代理验收需核对来源、`partial`/`complete`、S3 字节与哈希，并记录目标站点对正文的访问限制（例如 Reddit HTML 403），不能把 Feed 摘要写成完整正文。

## 配置语义与模型输入

### Scope / Trigger

配置中的候选说明或提示会影响模型判断时，同时核对模型实际输入、配置指纹及已完成结果的复用边界。产品词表契约归 [数据设计](../../../docs/data.md)，不在工程规范另建字段来源。

### Signatures

`Label.candidate_payload()` / `candidate_text()` 与 `Taxonomy.candidate_payload()` 提供统一候选语义；`classification.service.taxonomy_fingerprint(taxonomy)` 标识整套分类语义。新增协议消费同一来源，不各自维护候选词表。

### Contracts

指纹包含版本、全局指令与完整有序候选语义；稳定 JSON 仅排序对象键，保留会影响模型输入的数组顺序。不包含凭据，不只 hash ID 或手工版本号。配置变更影响后续/显式重处理，不能悄悄重写已保存结果或报告证据。

### 结构化输出

OpenAI-compatible 网关可能接受 `response_format` 却忽略其中的 schema。`ModelClient.research_chat()` 在保留 `response_format` 的同时把 schema 重复写入 system prompt；调用方仍需按 schema 校验内容。测试保持 user message 在索引 1，避免证据输入被额外消息挤位。

### Validation & Error Matrix

| 条件 | 检查 |
| --- | --- |
| ID 不变，仅名称/说明/别名/全局指令变更 | 指纹必须变化，实际请求包含新语义 |
| 名称/别名被模型作为标签输出 | 沿用规范 ID 校验并失败，不做隐式别名转换 |
| 完整生成式与原生分类协议并存 | 两条协议都消费共享语义；开放实体角色仍不接收股票候选暗示 |
| 等价配置重复读取 | 指纹稳定，复用行为不依赖随机字典顺序 |

### Good / Base / Bad Cases

Good：测试真实请求包含候选名称/含义，并验证语义变化后的指纹。Base：别名为空也保留规范 ID 与必要定义。Bad：只在 YAML 加词，没有把它传入某条模型路径，或只 hash version/IDs 使提示变更不可追踪。

### Tests Required

参考 `tests/test_classification.py` 与 `tests/test_classification_jev.py` 的请求、输出拒绝及指纹断言。新配置示例经实际 Task loader 验证；检索意图和原文提及的区别通过保存输入/引用验收，不能只靠 query 名称断言。

### Wrong vs Correct

Wrong：以 `taxonomy.version + category_ids` 标识分类语义，认为描述不会影响模型。
Correct：共享完整候选 payload，再对实际语义与全局指令稳定序列化计算指纹；需要更新旧结果时使用显式重处理。

