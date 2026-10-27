# MP-01 执行记录

## 授权与计划

用户已授权“现在我们开始实现修复配置一致性这个任务”，沿用既有任务，不重复创建或索取实现授权。

- [x] 读取实际配置入口、加载规范，远程记录修复前路径偏差。
- [x] Worker/CLI 默认值统一使用 Settings，显式参数优先；移除 Compose 固定参数。
- [x] 补充 API 入队到 Worker 执行的受控持久化回归、默认/显式覆盖/配置失败覆盖，同步执行契约与启动说明。
- [x] 在远程本任务隔离目录执行最小相关测试及 Compose 配置检查；定向复审并保存证据。

## 验证与资源

唯一验证服务器 root@100.120.14.84；任务目录 /tmp/stocks-mp01-config-consistency。读取并复用 /opt/stocks-test-runtime/tool-venvs/stock-tools，设置本任务 PYTHONPATH 或重新指向本任务可编辑包。仅使用本任务 postgres 容器和数据库。先定向测试新配置回归与原 Worker CLI 边界；按受影响 CLI 扩大到相关文件/用例，不运行全量。每个 SSH 批次限制在 60 秒内，长命令保存日志并后台执行后读取。

远程结果：修复前探针按预期失败，修复后通过；新库初始化与 Alembic check 通过；受控 API → Worker → Report 持久化链路通过；四组定向测试共 34 passed；Compose 路径/挂载/command 检查通过；独立只读审查无发现。完整命令、证据与限制见 [verification.md](verification.md)。本机未执行应用测试或编译，产物不含真实密钥。

已移除本任务 PostgreSQL/S3 容器并删除私有 runtime.env，远程目录和脱敏证据保留。用户随后明确授权归档、提交并推送；按本任务范围完成 Git 收尾，不带入其他规划或 AGENTS.md 改动。
