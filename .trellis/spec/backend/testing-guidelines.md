# 后端测试与远程验证环境

## 强制环境边界

**所有模块的测试与验证，包括单元测试、代码编译/类型/格式检查、数据库初始化与查询、
API smoke、外部集成、Compose 构建和部署验收，都必须在测试服务器
`100.120.14.84` 的隔离任务目录中执行。** 测试服务器是唯一的测试与验证环境。

本机只用于读取、编辑和打包当前工作区代码。禁止在本机运行 pytest、数据库、
pgvector、Docker、Alembic、API 服务或任何需要运行时/外部依赖的验证；即使本机
碰巧安装了相同工具或服务，也不得复用，更不得把本机结果记作通过。禁止以本机测试
替代远程测试，禁止本机与测试服务器并行验证。

## Scope / Trigger

- Trigger: 后端行为、数据库结构、Compose 配置或外部集成需要实际验证。所有此类验证
  必须使用远程测试服务器，不检查也不使用本机同类环境。
- 目标机器：`100.120.14.84`。通过 SSH 连接，用户名为 `root`，密码由执行者从用户会话取得。
- 密码、模型密钥、S3 密钥和数据库内容不得写入仓库、命令日志、测试输出或 Trellis 记录。

## Persistent Runtime

目标机已持久化以下运行环境；同一目标机上的后续任务直接复用，不再重复下载 Python、
`uv` 或安装完整依赖：

- Python 3.12：`/opt/stocks-test-runtime/bin/python3.12`，同机也为 `python3.12`。
- `uv`：`/opt/stocks-test-runtime/bin/uv`，同机也为 `uv`。
- 通用工具与项目依赖：`/opt/stocks-test-runtime/tool-venvs/stock-tools`。安装当前
  工作区依赖后执行 `uv sync`，只保留 `backend/pyproject.toml` 声明的必要包，避免把
  调试依赖或旧包留在通用环境。

先检查是否已有本任务或当前验证轮次可复用的测试容器；能沿用旧环境时直接复用，
不重复创建。需要新环境容器时，先确认并清理本任务遗留的旧容器，再创建新容器，
不要累积同用途资源。每个任务仍使用独立远程代码目录、测试数据库和对象存储。该目录的
  `.venv` 指向持久工具环境，以验证当前工作区代码；不要删除公共工具环境或让它承载
测试凭据。若环境损坏或项目要求的 Python 版本变化，修复或新增受版本命名的持久
环境，并在这里更新实际路径。只有首次配置持久环境时才需要自行创建 `.venv` 和
完整安装依赖。

## Signatures

- 连接：`ssh root@100.120.14.84`
- 工作目录：远程 `/tmp/stocks-v0-XX-<task-slug>-<run>`，每次任务独立，避免修改
  其他测试。
- 传输：

```bash
tar -czf /tmp/stocks-test-<task-slug>.tgz \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.env' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='._*' \
  backend config docs
scp /tmp/stocks-test-<task-slug>.tgz root@100.120.14.84:/tmp/
```

macOS 的 `._*` 是 AppleDouble 元数据文件；Docker 会把它们当成普通文件，Alembic
还可能把 `._*.py` 当作迁移加载，导致 `source code string cannot contain null
bytes`。使用 tar 传输时同样排除 `._*`。

- 后端依赖与测试：

```bash
cd /tmp/stocks-v0-XX-<task-slug>-<run>/backend
ln -s /opt/stocks-test-runtime/tool-venvs/stock-tools .venv
.venv/bin/python -c 'import pytest, stocks'
.venv/bin/python -m pytest -q tests/<相关文件>.py
```

该持久环境不包含 `pip` 模块；安装或更新项目包时使用 `/opt/stocks-test-runtime/bin/uv pip`，
不要尝试 `python -m pip`。持久环境首次缺少当前项目依赖时，先在独立临时目录副本运行
`uv sync --python /opt/stocks-test-runtime/tool-venvs/stock-tools/bin/python --extra dev`，
再以 `/opt/stocks-test-runtime/bin/uv pip install --python
/opt/stocks-test-runtime/tool-venvs/stock-tools/bin/python --no-deps
--force-reinstall -e .` 将当前工作区包安装为可编辑包。不要在测试机器全局
安装项目包；公共环境的项目包来源会被后续项目替换，验证前必须核对
`.venv/bin/python -c 'import stocks; print(stocks.__file__)'` 指向 `backend/src`。

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
- pgvector 检索会执行 `LOAD 'vector'`；数据库测试角色必须具备加载该扩展的权限。
  如复用已有 pgvector 容器，只为本任务新建的独立测试角色授予所需超级用户权限；
  不要修改其他任务的既有角色，也不要为此把数据库暴露到公网。
- 唯一的测试与验证入口是 `root@100.120.14.84` 上按任务隔离的远程目录。不得在
  Owner 的 mac、`main` 工作区或任何非测试服务器上执行测试、数据库或服务命令。
- 仓库不配置在 GitHub Runner 或任何非指定机器上执行测试、构建或服务检查的 CI；
  推送只保存代码和文档，验证结果必须来自测试服务器的隔离环境。
- 执行前检查是否有可沿用的测试容器；无需创建时沿用现有环境。若需创建新容器，
  先清理本任务遗留的旧容器，确认名称不属于其他任务后启动新容器。
- 执行后停止并删除本次创建的容器及远程测试目录；测试失败的诊断需要保留现场时，
  明确记录容器与目录名称及保留原因。
- 容器必须绑定 `127.0.0.1`，不得对公网开放数据库端口。
- 每次验证记录实际命令、退出码、通过/失败/跳过结果及未运行的检查。
- 数据库结构验证必须从全新空库执行 `alembic upgrade head`，再执行 `alembic check`；不得验证旧库升级。
- 项目代码与依赖从当前工作区传输，远程结果只作为当前版本的验证，不得把远端修改同步回本地。
- 若持久环境不可用，先修复已在本文记录的环境；首次建立持久环境时才用 `uv` 安装
  隔离的 Python 3.12。不得复用 Python 3.13 环境冒充项目验证。传输归档后删除远程
  `._*` 文件，再启动 Alembic。
- 真实来源与对象存储验收使用本任务独立的数据库、bucket/前缀和临时凭据。数据库查询只输出脱敏字段；对象哈希核对后清理容器、bucket 与远程目录。

## Validation & Error Matrix

| 条件 | 预期处理 |
| --- | --- |
| SSH 不可连接 | 停止验证并保留未验证状态；不得回退到本机，明确记录环境不可用 |
| 远程无 Docker | 安装 Docker Engine，确认守护进程可执行 |
| 镜像不存在 | 拉取 `pgvector/pgvector:0.8.7-pg17-bookworm` |
| 测试容器名冲突 | 确认归属；本任务遗留容器先清理，其他任务或归属不明容器不删除并更换名称 |
| 数据库未就绪 | 有界轮询健康状态；超时后记录失败 |
| 测试失败 | 保留退出码和最小必要输出，修复后只重跑受影响用例 |
| 清理失败 | 报告保留的容器/目录名称，不自动删除其他资源 |

## Good / Base / Bad Cases

- Good：在测试服务器上复用一个已核验的持久 Python 3.12/`uv` 环境，同时使用独立远程代码目录、
  随机测试密码、隔离容器、相关 pytest 文件和全新数据库初始化。
- Base：纯文档任务可说明无需运行测试，但不运行验证时不要启动远程资源。
- Bad：在本机执行 pytest、数据库或服务；在 `main` 主目录直接运行测试、使用生产数据库、
  在仓库中记录 root 密码、用 Python 3.9 的结果冒充 Python 3.12 验证。

## Tests Required

- 单元行为：断言输入输出的实际行为或外部边界，而不只检查 Mock 被调用。
- 数据库结构：空库 `upgrade head` 成功，`alembic check` 无差异。
- API/Smoke：只在接口发生变化时启动对应服务并请求实际端点。
- Mock 依赖：外部模型、S3 和网络请求默认使用 `httpx.MockTransport` 或 `unittest.mock`，单元测试不依赖真实凭据。
- 真实采集：至少验证一次正文获取、对象字节与数据库哈希一致、来源时间/完整性字段和重复运行幂等；真实失败需区分来源访问政策、网络问题和代码缺陷。
- 解析批次：验证不可变批次的 `completed | failed | unsupported`、最新尝试与当前成功
  指针、批次内 Chunk 顺序，以及 Document 内容哈希与批次关联。
- 原文定位：固定 HTML 应覆盖 `article` / `main`、段落、列表、表格行与单元格、
  数字和单位、噪声标签、原始字符范围及元素路径；纯文本应覆盖严格解码后的段落
  字符范围。定位必须能回到传输的原始字节或其严格解码文本，不能只断言 Chunk 非空。
  嵌套行内元素、前导空白和 HTML 实体均应可回查；`&amp;` 的字符范围应覆盖
  完整实体原文，同时解码后恰为 `&`。普通连续文本可合并来源范围。表格既有
  `locator.separators` 标记的系统分隔符，也有可回到单元格的 `source_ranges`，
  应分别断言。纯文本切片应使用 `source_ranges` 校验实际字符，而非只比较
  元素级 `char_start` / `char_end`；只有逐字符映射成立时才比较逐字符范围。
- 使用 `html.parser.HTMLParser` 映射原文时，以 `convert_charrefs=False` 接收
  `handle_data`、`handle_entityref`、`handle_charref`，并用事件起点逐个记录字符
  范围。实体回调也可能收到无分号或未知的 `&name`；无法解码时应把对应原文当作
  普通字符保留，不能因此判定整篇 HTML 不可解析。不要对已解码文本猜测实体宽度。
- 分块先产生满足上限的基础 span，再在预算内加入 overlap；重叠起点碰到词或受保护
  数字/单位时向可切边界挪动，最终仍须 `len(chunk.text) <= chunk_size`。验证重叠应
  比较相邻 Chunk 的 `source_ranges` 交集，而不是要求某段可见字符串原样复制，因为
  系统插入的表格分隔符和语义块换行不来自原文。
- 重复与重处理：普通调用应复用相同处理器版本和配置指纹的批次；`--reprocess`
  必须创建新批次。重处理失败不得替换当前成功批次，旧 Chunk 的 ID、文本和定位
  必须保持可读。不要通过删除旧 Chunk 或原地更新来完成“重处理”。
- 解析边界：显式覆盖不支持 MIME、无效声明编码、无可见正文、内容哈希不一致和
  完成状态无 Chunk 的拒绝路径；失败不能写成空成功。HTML 固定样例应包含 void
  标签（如 `<meta />`）和嵌套语义块，避免解析树在真实页面结构上失衡。
- 真实解析：真实来源阶段验收不能由固定响应替代，也不能由固定响应测试替代真实
  来源验收。至少读取一份已保存的真实 HTML，核对原件字节哈希、Chunk 数、数字/单位、
  HTML 路径和字符范围、普通重复复用与显式重处理后的历史 Chunk；记录脱敏样例和
  当天使用的来源入口，不记录正文、完整敏感 URL、凭据或数据库密码。
- 向量索引与检索：使用真实 PostgreSQL/pgvector 验证当前解析批次、模型/维度隔离、
  来源与时间过滤、索引失败保留 Chunk、旧批次向量不进入检索，以及过滤后的 HNSW
  召回。Provider 返回、回退和维度拒绝可用 `httpx.MockTransport` 覆盖，不能代替
  真实数据库行为。
- 测试数据顺序：关系型 PostgreSQL 测试先提交 `Source`，再提交依赖它的
  `Document`；同一 Session 中未 flush 的父子对象可能按错误顺序插入并触发外键错误。
- 必要范围：先跑受影响文件和 `-k` 用例；只有明确回归风险才扩大到全量。

## Wrong vs Correct

### Wrong

```bash
# 禁止：在本机或主工作区运行测试
cd backend && python -m pytest -q
```

### Correct

```bash
ssh root@100.120.14.84
cd /tmp/stocks-v0-XX-<task-slug>-<run>/backend
ln -s /opt/stocks-test-runtime/tool-venvs/stock-tools .venv
.venv/bin/python -m pytest -q tests/<相关文件>.py
```

执行完毕后清理：

```bash
docker rm -f stocks-test-v0-03-<task-slug>-postgres
rm -rf /tmp/stocks-v0-03-<task-slug>-<run>
```

以上仅为清理示例：只删除本次创建的容器和目录，先确认名称确属本次任务。
