# 后端目录与模块

## 放置规则

Python 包在 `backend/src/stocks/`，测试在 `backend/tests/`，数据库初始化脚本在 `backend/alembic/`。`backend/pyproject.toml` 管理包、依赖和 pytest 配置。

现有文件展示按职责划分的方式：`api.py` 放 HTTP 边界，`db.py` 放会话工厂，`models.py` 放 ORM 映射，`settings.py` 放环境配置；`s3.py` 与 `providers.py` 分别封装一个外部系统的调用。`repository.py` 统一管理不可变 Document 版本与当前采集状态；`collection/` 是当前真实来源采集的完整边界：`config.py` 校验来源配置，`feed.py` 只做 Feed 发现，`http.py` 执行受限网络获取，`service.py` 编排逐条处理与 Repository 入库，`__main__.py` 只负责手动 CLI。新代码先寻找相应职责的现有模块；职责变大时再按边界拆分，不预建空的 service、repository 或 util 层。

## 边界

- 路由负责解析、验证请求并组织响应；持久化和外部调用复用独立函数或模块，避免在路由里堆积流程。
- 外部系统访问集中在对应适配模块；调用者不复制认证、超时、请求或响应处理。
- 配置从 `settings.py` 或专门的配置读取函数进入代码，不在调用函数里散落环境变量读取。
- 测试按受测模块命名，例如 `backend/tests/test_s3.py` 和 `backend/tests/test_providers.py`。外部 HTTP 可参照 `httpx.MockTransport`，对象存储可参照 `unittest.mock.Mock`，避免测试依赖真实服务。
- 测试代码可以在工作区编辑，但任何测试、编译、类型检查、数据库或服务验证都必须在
  测试服务器的任务隔离目录执行；本机不运行。环境与清理规则见
  [测试环境](testing-guidelines.md)。
- 采集结果按单条隔离：Feed 发现、文章获取和入库失败只影响当前条目，记录稳定错误后继续处理其余条目；配置错误则在发出任何请求前终止。

仅在职责边界或重复代码确有需要时新增抽象；命名跟随现有 Python 模块的小写下划线风格。
