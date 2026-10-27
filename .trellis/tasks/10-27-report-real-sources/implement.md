# 实施计划：首批真实来源接入

## 步骤

1. [x] `SourceConfig.resolved_query()`：`${ENV_NAME}` 查询参数占位解析，缺失时 `SOURCE_CREDENTIAL_UNAVAILABLE`。
2. [x] `_collect_structured_source`：请求 URL 与注册 URL 分离（`_safe_original_url` 去掉 query）。
3. [x] `source_mapper._parse_time`：完整日期（`2026-09-01`）锚定 00:00 UTC，不再令映射整体失败。
4. [x] 回归测试：占位解析、缺变量失败、注册内容不含密钥（`tests/test_collection.py`）、完整日期映射（`tests/test_facts.py`）。
5. [x] 远程单元测试：`tests/test_collection.py`（56）、`tests/test_facts.py`（53）、`tests/test_repository.py`（8）、`tests/test_execution.py`（119 组合通过）。
6. [x] 真实采集验收（测试服务器隔离目录 `stocks-v04-realsrc-0002`，独立 pg+SeaweedFS，出口经本地代理美国节点）：
   - FRED `observations`（CPIAUCSL）经标准入口入库 1 版；Fact `cpi_index_sa=336.298 index_1982_84_100`，period 2026-09-01，`origin_method=source_mapping`；注册 URL 无 `api_key`；快照 1541 字节哈希一致，正文无密钥。
   - SEC press releases 5 条、美联储 5 条、CNBC Markets 10 条全部 complete 入库。
7. [ ] Reddit r/stocks：测试服务器本地 DNS 对 `www.reddit.com` 返回投毒记录（`31.13.92.37` / AAAA `2001::1`，均非公网可用），标准入口 `UNSAFE_ADDRESS` 拒绝。按 PRD 记录为阻塞，不伪造通过；StockTwits API 未列入首批（403 风险未在本轮验证）。范围决定：讨论来源暂缺，由 owner 决定是否引入 DNS-over-HTTPS 或代理出口后再验。
8. [ ] `config/sources.yaml` 增补启用来源（FRED 序列、CNBC），更新 `docs/data.md`、`docs/quickstart.md` 与任务 PRD 验收记录。

## 验证命令（远程）

```bash
cd /tmp/stocks-v04-realsrc-0002/backend
TEST_DATABASE_URL=... PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_collection.py tests/test_facts.py
set -a; source /opt/stocks/.env; set +a   # 只取 FRED_API_KEY
.venv/bin/python -m stocks.collection --config ../config/sources-realsrc.yaml --source fred_cpiaucsl
.venv/bin/python -m stocks.processing facts --document-id <id> --sources-config ../config/sources-realsrc.yaml
```

## 回滚

单提交回滚即可；无数据迁移。已入库的真实 Document/Fact 保留证据链，不影响回滚后代码。
