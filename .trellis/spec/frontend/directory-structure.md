# 前端目录结构规范

`frontend/` 是唯一 Web 客户端，源码根目录为 `frontend/src/`。模块按实际职责组织：

```text
src/api/                   fetch 边界、Zod 解码与 Dashboard API
src/auth/                  Owner 会话内存状态
src/app/                   路由、会话门禁、壳层与公共页面
src/components/ui/         shadcn/ui Nova + Base UI 本地原语
src/components/            跨页面业务展示与交互
src/features/              overview/intelligence/tasks/research/runs/login
src/hooks/                 可复用数据读取生命周期
src/lib/                   格式化、错误和展示 helper
src/state/                 非敏感客户端共享状态
src/test/                  Vitest 全局测试初始化
```

配置位于 `frontend/vite.config.ts`、`vitest.config.ts`、`tsconfig*.json` 和
`components.json`。页面测试与被测功能同目录，API 边界测试位于 `src/api/`。

## 已确认的边界

- 页面、功能组件、复用 UI 原语和 API 访问代码应由真实职责分隔，页面只组合功能，不把所有状态和请求堆在单个页面文件中。
- shadcn/ui 生成的本地原语与业务组件分开；业务代码不在调用点复制通用交互和样式。
- API 请求、运行时响应校验和时间转换集中在网络边界，组件不各自维护不一致的响应形状。
- 浏览器路由、认证、搜索筛选和证据读取应保持薄边界；Task、游标、处理状态和权限判断以服务端为准。
- TanStack Table 与 Recharts 只在实际页面使用；不为尚不存在的表格或图表创建共享封装。

## 组织规则

新增页面放入 `src/features/<feature>/`，路由只负责页面级按需加载与访问边界。
组件不直接调用 `fetch`；网络请求经 `src/api/`，响应先解码再传给页面。只有至少两个
实际调用点需要同一行为时才提取共享组件或 hook，不预建 `components/common`、
`hooks/utils` 等空壳。测试服务器浏览器脚本和 fixture 保留在临时验证目录，不混入
生产源码或运行依赖。
