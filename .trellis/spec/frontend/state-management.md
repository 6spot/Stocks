# 前端状态管理规范

客户端共享状态统一使用 **Zustand**；组件本地状态继续使用 React。当前只有两个
页面外共享 store：

- `src/auth/session-store.ts`：Owner 会话状态与内存 CSRF token，不持久化。
- `src/state/ui-store.ts`：移动导航开关和最多四条非敏感通知，不持久化。

## 状态归属

- 输入的展开、弹窗开关、选中行等短暂交互状态留在最接近的组件。
- 同一页面多个区域确实共享的状态提升到共同父级；能由已有数据计算的值不另存副本。
- 服务端返回的 Document、Chunk、Fact、Task、Run 和处理状态是权威数据。浏览器不自行推导游标完成、索引完成或权限。
- 跨组件或跨页面确实共享的客户端状态使用 Zustand，例如工作台偏好、共享筛选或证据面板上下文；只为已经出现的需求建立 store，不将全部组件状态全局化。
- 应可分享、刷新或通过浏览器前进/后退恢复的查询条件优先由 URL 承载；URL 与 store 不各自保存一份可独立修改的同类状态。
- 搜索条件、来源、文档版本和证据选择等信息必须保持显式；切换来源或任务上下文时清理不再适用的筛选与预取数据。

## Zustand 使用规则

- 按功能拆分小型 store，显式声明 State 与 Actions；通过命名 action 修改状态，避免组件直接拼接全局更新逻辑。
- 用 selector 只订阅需要的字段；多字段对象选择器需保持引用稳定或使用 Zustand 对应的浅比较能力，避免订阅整个 store 引发无关刷新。
- 能从已有状态计算的值直接计算；面板尺寸、拖拽坐标和 Motion 的动画进度由对应库或局部状态管理，不每帧写入 store。
- 服务端实体与请求状态通过统一数据访问边界获取，Zustand 不承担通用服务端缓存。尚未出现专门缓存需求时不额外引入数据获取库；如果需要缓存，先定义失效、取消、刷新和错误规则。
- 持久化仅按需用于无敏感信息的界面偏好，通过 `partialize` 明确白名单；不持久化认证凭据、报告正文、证据内容、运行状态或请求错误。使用后端提供的认证机制，不用 store 或浏览器存储代替访问控制。
- 退出登录或切换研究上下文时，清理与旧身份、任务或来源关联的状态并取消过期请求；过期响应不得覆盖当前上下文。持久化偏好结构变更时可丢弃并恢复默认值，不为开发期旧结构增加迁移逻辑。

以下是 store 形状示例，实际实现以对应源码为准：

```ts
import { create } from "zustand";

type EvidencePanelState = {
  open: boolean;
  setOpen: (open: boolean) => void;
};

export const useEvidencePanelStore = create<EvidencePanelState>()((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));
```

组件内部用 `const open = useEvidencePanelStore((state) => state.open)` 选择所需字段；只有多处共享时才使用此 store。

## 数据读取与会话

页面使用 `src/hooks/use-resource.ts` 读取服务端数据，依赖变化或刷新时取消旧请求，
并忽略过期响应。该 hook 不缓存跨页面实体，也不推断权限、Run 占用或阶段完成；
服务端响应仍是权威状态。

所有请求使用 `credentials: "include"`。登录响应和 `/api/session` 返回的 CSRF token
只保存在内存，所有 `POST` 自动附加 `X-CSRF-Token`。私有请求返回 `401` 时清空
内存会话并进入登录边界。`SessionGate` 在公共和受保护路由外层统一恢复会话，网络
错误显示可重试状态，不能凭浏览器存储自行恢复身份。退出登录同时请求服务端清理
Cookie 并清空内存状态。

搜索、筛选、选中的 Document/Run/Report 和页面页签由 URL 承载。页面数据、会话
token、证据正文、报告内容和原件均不写入 `localStorage`、`sessionStorage` 或持久化
store。

## 时间与完整性

`published_at`、`source_updated_at`、`collected_at` 和报告 `as_of` 分别保留，不能合并为一个含糊的更新时间。`completeness = partial`、失败错误类别、解析/索引状态和来源故障也不能折叠成一个通用“无数据”状态。

访问控制和服务端执行规则不由前端状态替代。新增数据缓存或持久化字段时，先在本文件记录其失效、清理和白名单边界。
