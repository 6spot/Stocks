# Magpie 真实 Jev 验证

日期：2026-10-26（服务器 UTC）。用户明确授权使用已配置的 Magpie 代理验证 Jev。

## 发现与方法

本机仅只读获取 Magpie 配置：存在 `typesafe` provider，其原生 `decide` 指向官方 `https://api.typesafe.ai/v1`，已配置凭据；未打印或复制上游 TypeSafe 密钥。通过临时 SSH 反向转发，让测试服务器 loopback `13425` 访问 Owner 本机的 Magpie 网关。网关调用凭据只经 SSH 私有 stdin 传入任务内权限受限文件。

普通 `GET /v1/models` 不返回决策模型；携带 `X-Magpie-Deciders: 1` 后，真实返回 `typesafe/jev-latest` 与 `typesafe/jev-preview`，`kind=decision`，`native_endpoints=["/v1/systemone"]`。静态文档不能替代该实际发现结果。

## 实际调用

所有执行位于 `100.120.14.84` 的 `/tmp/stocks-v0-application-pipeline-audit-jev-live`。传输当前 `backend/src` 与 `config`，以 `PYTHONPATH` 指向当前源代码，复用 Python 3.12 持久工具环境，执行 `research/jev_live_probe.py`。

- 请求：当前 taxonomy 生成的 **36 个真实问题**，含 choice 与 noul，`model=typesafe/jev-latest`。
- 原文：虚构 Acme Holdings（ACME）半导体公司收入样例，不是真实市场证据。
- 调用：当前 `ModelClient.typesafe_decision`，没有 Mock transport；调用次数上限 1，没有回退。
- 输出：真实响应经当前 `normalize_answers` 严格校验，exit 0。
- 实际响应模型：**`jev-1.14.0`**。
- 耗时：约 **1.833 秒**；input 3303、output 805、total **4108 tokens**。供应商费用未提供，保持未知。
- 标签：`category=company`、`event_type=earnings`、`should_extract=true`、`related_industries=[semiconductors, hardware]`、`regions=[US]`；未选择 topics。

此次证明：现有原生请求编码、问题 ID、答案字段、完整 choice 概率、当前概率精度容差、usage.input_tokens/output_tokens 和报告模型身份，对这次官方实际响应兼容。结果只证明真实供应商和当前适配器的调用，不证明所有输入、模型版本或未来响应。

证据：`verification/decision-models.json`、`live-adapter-result.json`、`real-answers.json`。

## 仍未覆盖

未在本次真实探针调用开放实体生成、Fact 模型、研究总结模型或 embedding，也未访问真实来源、创建数据库、渲染浏览器或部署服务。前面的完整同批闭环仍属于受控来源/模型验收，其 manifest 的 `external_success=false` 保持原样，不能把两次独立验证拼成真实全部外部服务闭环。

## 清理

保存脱敏结果后，已删除远程 live 任务目录与临时网关凭据，命令 exit 0；已通过 SSH control socket 发送退出命令，关闭临时反向转发。本次没有启动新数据库或 S3 容器，没有修改 Magpie 全局配置或上游凭据。
