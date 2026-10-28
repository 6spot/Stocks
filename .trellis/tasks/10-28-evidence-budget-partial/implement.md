# 执行计划

1. [x] `research/service.py`：计算 20% 语义保留，增量路径使用较低上限；补覆盖缺口的说明。
2. [x] `collection/service.py`：`_partial_payload` 改为 HTML，调整调用处 content type。
3. [x] 测试：预算保留与 partial HTML；必要时补现有测试期望。
4. [x] 远程定向回归（research/collection 相关文件）。
5. [x] 部署重建；重新采集 Reddit 生成新版本因 429 阻塞，待限流恢复后重试；现有 v1 仍为旧 JSON。
6. [x] 重跑 SK Hynix；记录 Evidence Package、报告与剩余缺口。
7. [x] 更新 PA-4、提交、推送、归档。
