# 实施计划：分类与实体依据可见性

前置：design.md 已确定「后端不动、纯前端展示 + 受控重试」。

## 步骤

1. [x] `frontend/src/components/classification-card.tsx`：新组件，展示分类标签、
   公司/代码实体、实体原文依据（entity_evidence quotes）、reason、输入截断、
   双模型调用身份、词表版本；无结果/失败/未处理三种语义；固定免责说明。
2. [x] `frontend/src/components/processing-stages.tsx`：failed 阶段渲染可选重试按钮
   （`onRetry`/`retryPendingStage` props，默认不渲染保持既有页面行为）。
3. [x] `frontend/src/components/document-details.tsx`：插入 ClassificationCard；
   实现 classify 失败重试调用（复用 `dashboardApi.retryProcessing` +
   `OWNER_CONFIRMATION_REQUIRED` 确认流 + 成功后刷新详情）。
4. [x] 前端测试：新增 `classification-card.test.tsx` 6 用例（有实体+依据表、
   无实体不推断、从未存储时失败态、失败保留旧结果、等待态、非投资事实说明）。
5. [x] 远程隔离验证：`npm run typecheck` ✓；`npm run test` 76/76（连续两轮）✓；
   `npm run build` ✓。
6. [x] 部署环境真实页面核对（headless Chromium + API curl）：
   - SEC 骗局公告：4 公司实体 + 逐条原文引用 + symbols 为空不推断；
   - FRED CPI 文档：无公司/代码，背景标签正常；
   - CNBC Oracle 文章：「分类输入已截断（6000 字符）」；
   - 失败态：classify 置 failed 后卡片显示错误并保留旧结果，重试按钮可用；
   - 重试闭环：UI 点击重试 → 已重新排队 → Worker 重新执行 → completed。
7. [ ] 提交并归档任务。

## 执行记录

- mac tar 产生的 `._*` AppleDouble 文件会被 vitest 误当测试文件：解包后
  `find . -name "._*" -delete` 清理。
- research-page.test.tsx 在 jsdom 下有 1 个环境敏感用例偶发失败（本机与远程均见，
  重跑即过；与本任务改动无关，改动 stash 前后失败集合相同）。
- 验证用 playwright-core 仅安装在远程隔离目录（--no-save），仓库 package.json /
  lockfile 未引入任何新依赖。

## 验证命令（远程测试服务器隔离目录）

```bash
cd /tmp/stocks-v04-classif-0001/frontend
npm ci --no-audit --no-fund --ignore-scripts
npm run typecheck && npm run test && npm run build
# 部署核对（构建产物 docker cp 到 web 容器后）
node classif-check.cjs   # headless Chromium：登录 → 文档详情 → 分类卡片断言
```

## 回滚

单提交还原 `frontend/src/components/` 三个文件 + 新增测试文件；无数据/后端变更。
