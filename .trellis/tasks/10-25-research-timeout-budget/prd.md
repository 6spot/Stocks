# 调整研究 Agent 的模型超时预算

## 问题

生产部署首次真实研究 Run 失败：

```
run 22937879-8dc0-4e32-ab5e-b966f6a8a558  status=failed  error=MODEL_TIMEOUT
ModelCall  deepseek-v4.1-flash  dur=60259ms  failed  MODEL_TIMEOUT
ModelCall  deepseek-v4.1-flash  dur=60193ms  failed  MODEL_TIMEOUT
```

`config/models.yaml` 的 `agents.research.routing.timeout_seconds` 为 60，两次尝试都在 60 秒
整超时。同一模型的 `classification`（30 秒）与 `fact_extraction`（60 秒）在同一部署中均能
正常完成，说明**不是模型不可用，而是研究调用本身的输出规模超过了 60 秒预算**。

研究 Agent 需要一次产出 summary、多条 findings（每条含证据引用、理由、不确定性）、
open questions 与覆盖缺口，输出显著大于分类或单次抽取。

## 要求

- 只调整研究 Agent 的超时预算，不改变其他 Agent 的超时。
- 不改变重试次数、回退配置、提示词、输出 schema 与证据语义。
- 明确这是**容量调整**，不是放宽安全边界：超时仍然是硬上限，只是上限值按实际输出规模设定。
- 记录依据（实测耗时与失败记录），不写成「模型不兼容」。

## 验收

- `config/models.yaml` 配置为 Magpie 本地代理：
  - provider: `chat` → `http://host.docker.internal:3425/v1`
  - fast/research 模型：`workbuddy-ai/deepseek-v4.1-flash`（无额度时回退 `workbuddy/deepseek-v4.1-flash`）
  - timeout_seconds: 300（research），classification: 60，fact_extraction: 60
- 实测该模型 3.5 秒完成研究级输出，reasoning_tokens=0，strict JSON 验证通过。
- 配置可被现有加载校验接受（`load_models` 无报错）。
- 部署后真实研究 Run 能产出报告（若仍超时，如实记录并保留失败证据）。
- 更新 `docs/execution.md` 或部署文档中对研究调用超时的说明。

## 背景

- 部署：`100.120.14.84:/opt/stocks`
- 数据：20 份真实 SEC 文档，parse/classify/index 全部完成，60 个向量，71 条 Fact。
- 证据选取步骤（`select_evidence`）成功，失败点只在最终 research 调用。
