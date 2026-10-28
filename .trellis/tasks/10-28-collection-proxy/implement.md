# 执行计划：采集来源代理支持

## 步骤

1. [ ] `collection/config.py`：新增 `proxy_url` 字段、URL 校验、`resolved_proxy_url()` 与错误码。
2. [ ] `collection/http.py`：`fetch(proxy_url=...)`，代理模式使用 `httpx.Client(proxy=...)`，保留目标校验。
3. [ ] `collection/service.py`：每来源解析一次代理并传给 feed、正文、API 三处 fetch。
4. [ ] `config/sources.yaml`：新增 `reddit_stocks` RSS（T3，`\${COLLECTION_PROXY_URL}`）；`.env.example` 增加变量。
5. [ ] 测试：配置校验/环境缺失/脱敏；`httpx.Client` kwargs；三类请求代理透传；现有直连用例不回归。
6. [ ] 文档：`docs/data.md` 代理契约与 SSRF 取舍；`docs/quickstart.md` 环境变量；PA-4 更新。
7. [ ] 远程：同步代码，定向 pytest；真实 Reddit RSS 经代理采集并核对 Document/原件；重建镜像。
8. [ ] 提交、推送、归档；记录实际验证与未覆盖项。

## 远程验证

- 测试服务器隔离目录或既有 test runtime venv；使用独立测试库，不写部署库做单测。
- 真实采集使用部署实例与 `COLLECTION_PROXY_URL=http://172.20.0.1:7890`（仅测试环境）。
- 目标命令示例：`pytest -q tests/test_collection.py -k 'proxy or source_config'`，再按需扩大到 `test_worker_processing.py`。

## 回滚

单文件功能改动 + 配置/文档；`git revert` 对应提交即可。Reddit 来源可单独设为 `enabled: false`。

## 风险

- 代理解析与本地校验可能不一致：已记录为显式取舍，仅受信代理。
- Reddit RSS 条目正文可能被目标站限制，正文失败时保存 partial 摘要，不伪造全文。
- 代理 URL 含凭据时不得进入日志/错误；测试覆盖脱敏。
