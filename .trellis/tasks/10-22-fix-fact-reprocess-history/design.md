# F-01 技术设计

## 修复前调用链

backend/src/stocks/extraction/service.py 的文本抽取和 Source Mapper 成功路径在 reprocess_requested 时调用 clear_facts()；backend/src/stocks/facts.py 的 clear_facts() 执行 DELETE。persist_facts() 原本已按 (document_id, fact_key) 复用现存行。api.py 的 Fact 详情与报告比较仍按保存 ID 读取事实。

## 实施方案

优先复用已有 fact_key 和 persist_facts() 的追加/复用能力，移除显式重处理中的破坏性清空，以及被取代的函数、注释和替换预期测试。重处理表示再次执行当前处理器，不能表示删除历史事实。先前已保存的同身份内容保持原值；源内容变化仍使用新 Document，不人为制造同 Document 修订。

本任务不新增 active/superseded 双轨事实库或抽取版本系统。如果重新检查发现必须调整结构，应在实施前把理由和当前初始化方案更新到本设计，不编写历史迁移。

## 事务与下游

事实写入、record_change 与阶段完成继续使用原事务和所有权 fencing。复用 Fact 不追加虚假的新增事件，仅为真正新增事实记录变更。删除旧事实的错误路径必须完全移除，不能只在报告 API 绕过失效 ID。

F-04 将继续修复比较集合和冻结值读取；本任务先保证被保存的 Fact 行不会被重处理删除。

## 关键文件

- backend/src/stocks/facts.py
- backend/src/stocks/extraction/service.py
- backend/src/stocks/api.py（按风险核对读取，不扩大 API 范围）
- backend/tests/test_facts.py、backend/tests/test_report_comparison.py
- docs/data.md，按实际变更更新唯一归属契约及相关 spec。

## 实施落点（2026-10-22）

已按本方案移除两条清空路径及 `clear_facts()`，继续复用原 `persist_facts()`，未修改表结构或报告 API 实现。历史报告回归实际放在 `test_report_comparison.py`，复用其两份报告及原文夹具，核对显式重处理后的旧 Fact 详情、原报告和比较；F-04 的比较集合修复仍独立。

测试已编写但未运行。远程认证受阻，任务保持未验收，详见 [验收记录](acceptance.md)。
