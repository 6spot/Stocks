# V0-08 实施与验证计划

## 开工条件

- [x] 用户要求在 V0 下创建审查问题的修复任务。
- [x] 已整理 PRD、技术方案与本计划，明确五项要求及验收映射。
- [x] 用户授权按 V0 顺序实施；已 `task.py start`。
- [x] 复核归档提交与 `get_latest_document()` 唯一调用点；在 V0-06 已合并的分支上实施。
- [x] 测试服务器可用；沿用既有 pgvector 容器，只新建本任务数据库与临时角色，凭据未入仓库或日志。

## 执行顺序

1. [x] R1：统一最近内容指针与读取行为，补 `tests/test_repository.py` 的 A → B → A 和完整/部分/恢复场景；用 PostgreSQL 验证 ID、元数据、原字节和历史引用。
2. [x] R2/R3：修正部分内容重试与链接格式异常边界；重定向畸形 Location 实测由 httpx 拒绝并沿用既有 REQUEST_FAILED 有界错误，未新增不可达分支，在 `tests/test_collection.py` 覆盖未知时间、混合合法/畸形条目及单项隔离。
3. [x] R4：保留声明编码，补 HTTP 获取到入库再到现有解析器的回归场景；只在需要时调整 `tests/test_processing.py`，不复制已有纯解析测试。
4. [x] R5：修正 V0-01/V0-02 的归档说明，追加修复关联；同步 `docs/data.md`、必要工程规范和验收场景。
5. [x] 逐项记录实际验证，检查代码/文档契约和上下文清单，完成质量检查后交付。V0-07 最终验收须等待本任务完成。

## 最小必要验证

所有命令只允许在 `100.120.14.84` 的 `/tmp/stocks-v0-08-document-collection-fixes-<run>` 中执行，具体环境、传输、复用与清理遵守 `.trellis/spec/backend/testing-guidelines.md`。本机只读写和打包；不在本机运行测试、编译、格式/类型检查、数据库或外部请求验证。

- 远程隔离 PostgreSQL 中指定 `TEST_DATABASE_URL`，运行 `tests/test_repository.py`；新测试必须核对当前状态与历史原件，不仅检查 mock 调用。
- 运行 `tests/test_collection.py`，以固定响应验证 R2/R3/R4，保留既有安全边界覆盖。必要命令（远程 backend 目录）：

  ```bash
  .venv/bin/python -m pytest -q tests/test_repository.py tests/test_collection.py
  ```

- 若修改解析器或相关数据流，再按新增用例用 `-k` 选择 `tests/test_processing.py` 的编码与定位场景；未修改且已有覆盖不机械重跑全部解析测试。
- 隔离测试数据库须使用当前初始化结构。若修改模型/初始化，从全新空库执行 `alembic upgrade head` 和 `alembic check`；不测旧库升级。
- 不默认运行全量测试、不重复执行原任务全部真实 SEC/模型验收；如果本次固定响应/数据库测试暴露真实外部边界风险，再说明原因并扩大验证。
- 将实际命令、退出码、通过/失败/跳过及限制写入本任务 `acceptance.md`；SSH 不可用时停止运行验证，保持未验收。

## 收尾

- [x] AC-1 至 AC-5 均有远程证据，实际命令与退出码写入 acceptance.md。
- [x] `docs/data.md` 与 `docs/quickstart.md` 已同步；归档记录只修正最终状态说明。
- [x] V0-07 依赖已指向本任务；其验收须按采集状态指针读取最近内容。
- [x] 仅删除本任务的数据库、临时角色、远程目录与归档包；沿用容器属早期任务，保持不动。

本文件记录的验证已实际在测试服务器执行，结果与限制见 acceptance.md。另发现 V0-03 解析器逐字符换行的既有缺陷，未在本任务修改。
