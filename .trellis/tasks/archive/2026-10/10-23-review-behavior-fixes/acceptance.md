# 验收记录

## 交付

| 子任务 | 修复 | 提交 |
| --- | --- | --- |
| 补证复用分类后继续 Fact | `backend/src/stocks/research/supplement.py` | `a3c9507` |
| SSE 完整响应即完成 | `backend/src/stocks/tools/client.py` | `07b2f0b` |
| 报告页初始选择 | `frontend/src/features/research/research-page.tsx` | `683f23a` |

各子任务目录（`.trellis/tasks/archive/2026-10/10-23-*`）保存了各自的验收细节；本文件只汇总跨子任务的结论与限制。

## 验证环境

- 测试服务器：`100.120.14.84`，任务目录 `/tmp/stocks-review-fixes-20261023`。
- 后端：持久 Python 3.12 环境 `/opt/stocks-test-runtime/tool-venvs/stock-tools`，专用数据库 `stocks_review_fixes`（复用现有 PostgreSQL 容器，端口仅绑定回环地址）。
- 前端：复用已验证的 `node_modules`（符号链接，未安装新依赖）。
- 未对 `stocks_both`、`stocks_v2_acceptance` 运行清表测试；保留的真实验收数据未被改动。

## 结果

| 检查 | 结果 |
| --- | --- |
| `tests/test_research_supplement.py` + `tests/test_research.py` | **28 passed in 1.77s**，退出码 0 |
| `tests/test_tools.py` | **69 passed in 0.65s**，退出码 0；修改文件 `py_compile` 与空白检查通过 |
| `vitest run src/features/research/research-page.test.tsx` | **10 passed**，退出码 0 |
| `npm run typecheck` | 退出码 0，无诊断 |
| `npm run build` | 退出码 0，`built in 4.94s` |

回归有效性验证：

- 补证：真实入库/领取路径用例在基线 `supplement.py` 下失败（`assert 'pending' == 'completed'`），修复后通过。
- SSE：受控流断言真实 `httpx` 流的读取次数、尾部字节与连接关闭，而非只验证 mock 调用。
- 报告页：实现方以 `HEAD` 版源码运行，恰有两个新行为用例失败；修复后 10/10。

## 未覆盖 / 限制

- 受控 `httpx` 流不是真实外部 MCP 服务与授权验收；既有真实 MCP 缺口不变。
- jsdom 以 `window.matchMedia` 桩验证断点，未做真实浏览器视口验收。
- 未运行全量测试；仅按最小相关范围验证。
- 未新增真实外部补证、真实 Provider 计费样本或物理设备/部署验收；V1/V2 阶段退出条件与 [延期清单](../10-20-stocks-roadmap/pending-acceptance.md) PA-3 保持原状。
- 三项修复不改变证据、报告或运行数据的既有语义，也不关闭任何外部/设备验收缺口。
