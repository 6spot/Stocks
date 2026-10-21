# V1-08 技术方案

## 现状与缺口

V1-07 已提供受保护的 Overview、Sources、Tasks、Runs、Processing、Document/Fact、
Search、Report、Evidence 和原件接口。当前仓库没有前端工程，Owner 只能通过带 Cookie
的 `curl` 读取。本任务新增 Web 客户端，消费既有 API，不复制认证、调度、处理状态或
证据版本判断。

## 边界与数据流

- `frontend/` 是唯一 Web 客户端。Vite 开发服务器把 `/api` 代理到 API；生产由
  V1-09 的 Nginx 同源提供静态文件并转发 API，因此浏览器请求始终
  `credentials: "include"`，不引入 CORS 或跨域凭据。
- 服务端会话 Cookie 由浏览器管理。登录响应和 `/api/session` 返回的 CSRF token
  仅保存在内存；所有变更请求附 `X-CSRF-Token`。出现 `401` 时立即清除内存会话，
  页面回到登录态。任何 token、Cookie、原件或证据正文都不写入 `localStorage`。
- API 边界先解析 `unknown` 响应，再生成前端类型；统一提取
  `{detail: {code,message} | string}`，将网络失败、认证失效、冲突、校验失败、
  能力不可用和普通服务错误映射为可读状态。组件不直接调用 `fetch`。
- 可刷新、可分享的搜索条件和详情选择由 URL 承载；Zustand 仅管理移动导航与
  非敏感通知。页面数据不复制到全局 store，不从客户端推断后端权限、处理完成或
  证据版本。

## 页面与交互

- `/login`：Owner 密码登录；成功后进入 Overview。独立读取会话恢复状态。
- `/` Overview：采集来源状态、后处理积压、近期 Run、近期报告和最新 Document；
  区分无内容、来源故障、部分采集、索引未完成和披露时间未知。
- `/intelligence`：语义检索为默认模式，支持来源与发布时间/采集时间过滤；精确检索
  接受股票代码/指标编号；结果只展示已保存片段与处理状态。选择结果后读取 Document
  详情，展示当前 Chunk、处理阶段、Fact、版本与受保护原件入口。片段上下文通过
  `chunk_id + radius` 读取，不使用全文检索。
- `/tasks`：只读任务定义、最近 Run、活跃 Run 和手动触发；服务端返回已有活跃 Run
  时不重复触发，界面强调该 Run。
- `/research`：报告列表与统一阅读器。正文按纯文本渲染；引用先打开实际保存的
  Chunk/Fact 证据，显示来源、Document 版本、定位、时间、单位、完整性和缺口，再
  提供原件查看/下载。旧报告始终使用其实际引用版本，同时另列最新采集指针。
- `/runs`：Run 列表与详情，显示固定窗口、步骤、失败定位、模型耗时/用量和安全
  错误；按服务端能力显示 retry/continue/end。结束运行必须先在确认对话框勾选
  “旧进程已停止”。处理积压页签定位 Document 阶段并排队重试；仍处于 running 的
  阶段同样要求停机确认。
- 桌面使用侧栏、列表/详情双区；手机使用可键盘操作的导航对话框、单列详情。证据
  正文使用可换行长文本，HTML 原件只通过受保护 `text/plain`/下载入口打开，不注入
  HTML。

## 技术与代码组织

- React 19、TypeScript、Vite 7、Tailwind CSS 4、shadcn/ui `base-nova` 本地原语、
  Base UI、Lucide、Motion、Zustand。
- `src/api/`：HTTP 请求、运行时解码和 Dashboard API。
- `src/auth/`：会话恢复与登录边界。
- `src/app/`：路由、壳层和错误边界。
- `src/features/{overview,intelligence,tasks,research,runs}/`：页面和功能组件。
- `src/components/ui/`：从 shadcn Nova/Base UI 派生的本地原语。
- `src/lib/`：安全文本/时间/定位/错误展示等无业务状态 helper。
- 不引入 TanStack Table、Recharts、resizable panels 或 dnd-kit：V1-08 的实际页面
  没有多列排序、来源图表或用户拖拽需求，避免无效依赖。

## 失败、安全与可访问性

- 网络失败和接口失败保留重试；空结果只在成功响应后展示。`coverage`、处理阶段和
  `completeness` 各自表达，不用一个“无数据”替代。
- React 只插入文本节点；有来源 URL 才提供经后端安全投影的外链。原件使用 API
  相对路径并在新标签查看/下载；不 fetch 后拼接 HTML。
- 所有图标按钮有 `aria-label`，导航与页签使用语义组件，对话框使用 Base UI 焦点
  管理，焦点环可见，错误与成功通知使用 `aria-live`。Motion 遵循
  `prefers-reduced-motion`，只在页面与导航层使用短过渡。
- 表单接受密码管理器，不预填密码；错误不显示服务端内部异常。浏览器不会把接口
  返回当系统指令或扩大操作权限。

## 验证与回退

- 前端类型检查、生产构建、最小行为测试全部在测试服务器隔离目录执行。行为测试
  覆盖 CSRF/401 边界、报告证据先于原件、Run 结束确认和处理阶段重试请求。
- 在桌面与手机 viewport 使用浏览器检查登录、导航、搜索/详情、报告证据和恢复
  交互；没有真实 Provider/S3/目标 ARM 时，使用既有 fixture/mock 并记录外部限制。
- 回退前端不改变数据库、API 或已存报告。前端失败不能修改证据；所有恢复动作仍由
  服务端原子规则约束。V1-09 负责 Nginx、Compose 与目标环境。
