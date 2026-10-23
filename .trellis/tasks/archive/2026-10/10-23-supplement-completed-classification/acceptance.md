# 验收记录

- 修复：已完成解析/分类仅跳过自身，后续 Fact 仍通过正常领取路径处理；已持久化的 Fact `skipped/NOT_APPLICABLE` 不误记缺口，依赖跳过、等待和失败仍保留缺口。
- 审查：trellis-check 子代理发现不适用跳过边界，已修正并新增三种跳过原因回归。主会话复核未改变阶段所有权、原件或旧结果。
- 验证位置：100.120.14.84:/tmp/stocks-review-fixes-20261023，专用数据库 stocks_review_fixes。
- 命令：PYTHONPATH=backend/src python -m pytest -q --tb=short tests/test_research_supplement.py tests/test_research.py（使用持久 Python 3.12 环境和专用数据库变量）。
- 最终结果：27 passed in 1.62s，退出码 0；日志 facts-tests.log。
- 说明：受控阶段状态回归 + 现有入库/补证研究数据库测试，不是新增真实外部补证验收。
