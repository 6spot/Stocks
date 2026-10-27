# 设计：首批真实来源接入

## 结论

复用现有 `rss` 与 `http_api` Connector，新增两处最小适配：

1. **查询参数环境变量占位**：`SourceConfig.resolved_query()` 在发请求前把
   `query` 中形如 `${ENV_NAME}` 的值经 `stocks.providers.resolve_env` 解析为
   环境变量值。配置文件与注册的来源记录只保存占位符；变量缺失时在请求前
   失败（`SOURCE_CREDENTIAL_UNAVAILABLE`），不发送占位符文本。
2. **结构化来源的 provenance 脱敏**：`_collect_structured_source` 注册
   `Document.original_url` 与 Collection 结果时使用去掉 query 的端点地址
   （`_safe_original_url`）。带密钥的查询参数是凭据而非证据，因此请求 URL
   （真实发送）与注册 URL（留痕）分离。

另修复 mapper 时间解析的一个真实阻塞：`_parse_time` 之前先尝试
`datetime.fromisoformat`，对 `2026-09-01` 这类完整日期返回 naive datetime
而提前返回，导致 `MAPPING_TIMESTAMP_INVALID` 使整个 Document 映射失败。
现在完整日期锚定为当日 00:00 UTC（与月/年周期同一原则）。

## 不做的事

- 不引入自定义请求头/Authorization：首批选定来源（SEC、美联储、CNBC、Reddit、FRED）均用 query 参数或无鉴权，PRD 适配决定只覆盖确有需要的能力。
- 不做通用分页框架、POST/CSV 支持：单序列/单 feed 快照足够。
- 不改变 RSS 路径：RSS 来源无 query、无密钥，零改动。

## 数据流

```
sources.yaml (query: api_key: ${FRED_API_KEY})
  → SourceConfig.resolved_query()      # 配置解析时读环境变量
  → _with_query(api_url, resolved)     # 真实请求 URL（含密钥）
  → SafeFetcher.fetch(url)             # 字节入库 S3
  → Document(original_url=_safe_original_url(url))  # 注册 URL 无 query
  → facts stage: map_response()        # /observations/0/value → Fact
```

## 权衡

- `resolve_env` 复用 `providers.py` 的既有实现（模型配置已在用），不新增
  第二套占位语法；错误转成 `ConfigurationError` 以便采集入口输出稳定错误码。
- `original_url` 去掉整个 query（不是只去掉 key 参数）：查询参数整体属于
  请求构造，不参与证据回溯；需要时可用 Document 的 source/时间定位。
- FRED 一次拉 13 条观测（`limit=13`），mapper 只映射最新一条；快照本身保存
  全部字节，后续如需历史序列可扩大 mapper 指针而不重新采集。
