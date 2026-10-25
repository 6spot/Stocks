# 验收记录

## 结论：列表页 + 详情页导航完成并已部署（含 --no-cache 构建与容器内特征核验）

## 实现

| 路由 | 内容 |
| --- | --- |
| `/research` | PageHeader + 全宽 Table（报告标题/任务/状态/生成时间）；行点击 → 详情页；无选中逻辑、无自动打开、无右栏 |
| `/research/:reportId` | ← 返回列表；标题+状态+生成时间一行；**单一信任行** `独立原件 N · 引用 M 条`（替代原 meta dl 与重复信任行）；**一行内联警告**（截断+缺口合并，仅非空；无 Alert 块）；正文（report-prose，75ch）；Tabs（证据包/来源与指标/研究用量，懒挂载）；对照模式 `?compare=` |
| `/research?report=x` | 客户端重定向到 `/research/x`（replace），旧深链接不断 |

`docs/interface.md` 同步更新列表与详情路由契约、对照模式入口；原「初始选择/80rem 自动打开」契约删除。

## 部署事故与纠正（本轮第三次）

前两次是「漏同步源码」「layer 缓存冒充新构建」。本轮在派发前 Owner 报告采集源菜单缺失
（代码已提交但镜像为旧构建产物），根因是 Docker layer 缓存未感知 package.json 变更。
纠正与流程固化：

1. 部署一律 `docker compose build --no-cache web`。
2. 验收必须 **grep 容器内 bundle 的新特征字符串**（返回列表/独立原件/采集源），
   文件指纹一致不等于内容是新的。
3. 本轮容器内核验：research 分包含全部新特征，sources 分包（采集源菜单）在，
   `/research/<真实报告 id>` 返回 200，health 200，容器 healthy。

## 审查结论

七项核查（路由/详情首屏/对照/列表/文档/测试/孤儿引用）零缺陷；删除的 7 个旧测试
只覆盖已移除的自动打开语义；45 个测试全部行为断言。

## 验证

| 检查 | 结果 |
| --- | --- |
| 远程 vitest（11 文件 45）/ typecheck / build | 全部退出码 0 |
| md5 核对（本地=测试目录=/opt/stocks） | research-page / router 一致 |
| `build --no-cache` + 容器强制重建 | healthy，health 200 |
| 容器内特征 | 返回列表 / 独立原件 / 对照其他报告 / 证据检索已截断 / 采集源 全部命中 |

## 限制

真机浏览器视觉验收仍属 PA-3 待办。
