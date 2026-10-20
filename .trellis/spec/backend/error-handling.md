# 错误处理规范

## 失败分类

- 无法继续的配置错误应在使用前明确失败。`backend/src/stocks/providers.py` 的 `resolve_env()` 和 `load_models()` 对缺少环境变量或不支持的协议抛 `ValueError`；`backend/src/stocks/s3.py` 对缺少必要 endpoint 抛 `RuntimeError`。
- 外部请求只对调用方定义为可重试的失败重试或回退。`ModelClient.chat()` 对指定 HTTP 状态、连接错误和超时尝试下一个配置目标；其他 HTTP 错误通过 `raise_for_status()` 传播。`backend/tests/test_providers.py` 分别覆盖回退和不回退的边界。
- 捕获异常时保留原始原因。所有候选目标失败时，`ModelClient.chat()` 使用 `raise RuntimeError(...) from last_error`；新增封装也应让诊断路径可追踪。

在 API 边界把需要返回给调用方的失败转换为明确 HTTP 状态和安全的错误内容。不要用广泛的 `except Exception` 把配置错误、权限错误或程序缺陷伪装成可重试故障；也不要在错误响应或日志中泄露凭据。
