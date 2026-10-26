# 验证记录（2026-10-26）

## 环境与范围

全部执行验证均在 `root@100.120.14.84` 的本任务隔离目录 `/tmp/stocks-v0-research-article-20261026`；本机仅阅读、编辑、打包和接收截图。使用本任务 PostgreSQL 容器 `stocks-test-research-article-postgres`（仅绑定回环端口 56446）及新库 `stocks_article_verify`。未修改日常部署或既有运行库。

## 实际验证

| 检查 | 命令（在远程任务目录执行） | 结果 |
| --- | --- | --- |
| 全新库初始化 | `cd backend && .venv/bin/alembic upgrade head` | exit 0，应用当前 0001 初始化定义 |
| 模型/初始化一致性 | `.venv/bin/alembic check` | exit 0，No new upgrade operations detected |
| 相关后端回归 | `pytest -q tests/test_research.py tests/test_execution.py tests/test_report_api.py tests/test_console_api.py tests/test_coverage_usage_api.py tests/test_evidence_conflicts.py tests/test_incremental_evidence.py tests/test_report_comparison.py tests/test_scheduler.py` | 首轮 exit 1：173 通过、3 失败；均为测试中的旧文案或未定义变量 |
| 上述失败定向重跑 | `.venv/bin/python -m pytest -q tests/test_execution.py tests/test_evidence_conflicts.py -k "retry_keeps_the_ledger or report_states_independent or report_says_unknown"` | exit 0：3 通过、75 deselected；相关后端回归累计 176 用例通过 |
| 文章协议与导出 | `.venv/bin/python -m pytest -q tests/test_article_output.py` | 最终 exit 0：17 通过；含非法关联、引用授权、空证据、顶层引用及重复输入导出编号 |
| 前端原有套件 | `npm test -- --run` | 首轮 exit 1：56 通过、4 失败；修正引用按钮定位后研究页 21 用例通过 |
| 最终前端定向检查 | `npm test -- src/features/research/research-page.test.tsx src/api/report-analysis.test.ts` | exit 0：31 通过；含新增严格关联校验与重复输入跨界面编号 |
| 类型与生产构建 | `npm run build`（包含 `npm run typecheck` / `tsc -b`） | 最终 exit 0，Vite 构建成功；依赖 Zod 有不影响构建的注释提示 |
| Chromium 预览 | 任务目录 `node preview.cjs` | 最终 exit 0：1440×1100 与 390×844；资料默认不挂载、发现可展开、引用可用键盘打开、关闭后焦点返回、移动端无横向溢出、无浏览器错误 |

验证时仅从任务隔离环境载入临时测试连接配置，未记录凭据。报告 API、执行持久化与证据版本读取由上表相关 API/执行测试覆盖；浏览器使用明确标注的中文示例 fixture。

## 诊断与审查

- 独立只读审查发现重复冻结输入 `[A,A,B]` 导致正文/清单/导出编号不一致；已统一为按引用身份首次出现去重，补充前后端回归，B 一致为 E2。
- 同次审查发现前端只验证字段类型而遗漏文章关联约束；已补齐非空文本、章节数量、唯一发现 ID、关联存在/章内不重复和发现被采用等校验。保留合法空发现文章、跨章复用与发现内重复引用。
- 首轮浏览器脚本沿用旧选择器，随后修正为当前文案，并等待弹窗关闭与焦点恢复后断言；最终执行通过。
- 曾尝试 `npm run lint` 和 `.venv/bin/ruff check src tests`，分别因未配置脚本（exit 1）及未安装/声明 ruff（exit 127）未执行；不计为通过，也未新增检查依赖。按仓库现有的行为测试、TypeScript 检查与构建验收。
- 没有运行全量后端测试；按本次影响范围选择文件及定向重跑。

## 验证边界

本轮未调用真实研究模型生成新报告，未验证新提示词的实际文章质量或外部模型可用性。截图是阅读与证据交互样例，不能作为真实研究结论或生成质量证明。没有直接升级、部署或重写既有报告；结构变化需在之后独立安排的开发环境重建中采用。

最终复核及资源清理结果由实施计划补记。
