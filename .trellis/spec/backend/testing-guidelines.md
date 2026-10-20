# 后端测试与远程验证环境

## Scope / Trigger

- Trigger: 后端行为、数据库结构、Compose 配置或外部集成需要实际验证，而本机没有 Python 3.12、Docker 或可用的 pgvector 数据库。
- 目标机器：`100.120.14.84`。通过 SSH 连接，用户名为 `root`，密码由执行者从用户会话取得。
- 密码、模型密钥、S3 密钥和数据库内容不得写入仓库、命令日志、测试输出或 Trellis 记录。

## Signatures

- 连接：`ssh root@100.120.14.84`
- 工作目录：远程 `/tmp/stocks-test-<task-slug>`，每次任务独立，避免修改其他测试。
- 传输：

```bash
rsync -az --delete \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='._*' \
  ./root@100.120.14.84:/tmp/stocks-test-<task-slug>/
```

macOS 的 `._*` 是 AppleDouble 元数据文件；Docker 会把它们当成普通文件，Alembic
还可能把 `._*.py` 当作迁移加载，导致 `source code string cannot contain null
bytes`。使用 tar 传输时同样排除 `._*`。

- 后端依赖与测试：

```bash
cd /tmp/stocks-test-<task-slug>/backend
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q tests/<相关文件>.py
```

- 隔离 PostgreSQL/pgvector：

```bash
docker run --rm -d \
  --name stocks-test-<task-slug>-postgres \
  -e POSTGRES_USER=stocks \
  -e POSTGRES_PASSWORD=<random-test-value> \
  -e POSTGRES_DB=stocks \
  -p 127.0.0.1:55432:5432 \
  pgvector/pgvector:0.8.7-pg17-bookworm
```

- 数据库初始化：

```bash
cd /tmp/stocks-test-<task-slug>/backend
DATABASE_URL='postgresql+psycopg://stocks:<random-test-value>@127.0.0.1:55432/stocks' \
  .venv/bin/alembic upgrade head
DATABASE_URL='postgresql+psycopg://stocks:<random-test-value>@127.0.0.1:55432/stocks' \
  .venv/bin/alembic check
```

## Contracts

- 远程执行只使用测试数据库中生成的凭证；不要复用生产或开发数据库。
- 执行前确认目标容器名未被占用，执行后停止并删除容器及远程测试目录。
- 容器必须绑定 `127.0.0.1`，不得对公网开放数据库端口。
- 每次验证记录实际命令、退出码、通过/失败/跳过结果及未运行的检查。
- 数据库结构验证必须从全新空库执行 `alembic upgrade head`，再执行 `alembic check`；不得验证旧库升级。
- 项目代码与依赖从当前工作区传输，远程结果只作为当前版本的验证，不得把远端修改同步回本地。

## Validation & Error Matrix

| 条件 | 预期处理 |
| --- | --- |
| SSH 不可连接 | 停止远程验证，明确记录环境不可用 |
| 远程无 Python 3.12 | 安装发行版 Python 3.12；不得用 Python 3.9 替代项目测试 |
| 远程无 Docker | 安装 Docker Engine，确认守护进程可执行 |
| 镜像不存在 | 拉取 `pgvector/pgvector:0.8.7-pg17-bookworm` |
| 测试容器名冲突 | 更换本任务唯一容器名，不删除不明容器 |
| 数据库未就绪 | 有界轮询健康状态；超时后记录失败 |
| 测试失败 | 保留退出码和最小必要输出，修复后只重跑受影响用例 |
| 清理失败 | 报告保留的容器/目录名称，不自动删除其他资源 |

## Good / Base / Bad Cases

- Good：独立远程目录、随机测试密码、隔离容器、相关 pytest 文件、全新数据库初始化全部实际执行。
- Base：纯文档任务不启动远程资源；只记录未运行测试的原因。
- Bad：在 `main` 主目录直接运行测试、使用生产数据库、在仓库中记录 root 密码、用 Python 3.9 的结果冒充 Python 3.12 验证。

## Tests Required

- 单元行为：断言输入输出的实际行为或外部边界，而不只检查 Mock 被调用。
- 数据库结构：空库 `upgrade head` 成功，`alembic check` 无差异。
- API/Smoke：只在接口发生变化时启动对应服务并请求实际端点。
- Mock 依赖：外部模型、S3 和网络请求默认使用 `httpx.MockTransport` 或 `unittest.mock`，单元测试不依赖真实凭据。
- 必要范围：先跑受影响文件和 `-k` 用例；只有明确回归风险才扩大到全量。

## Wrong vs Correct

### Wrong

```bash
# 由本地不存在的运行时或错误 Python 版本产生虚假“未验证”结论
cd backend && python -m pytest -q
```

### Correct

```bash
ssh root@100.120.14.84
cd /tmp/stocks-test-<task-slug>/backend
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q tests/<相关文件>.py
```

执行完毕后清理：

```bash
docker rm -f stocks-test-<task-slug>-postgres
rm -rf /tmp/stocks-test-<task-slug>
```
