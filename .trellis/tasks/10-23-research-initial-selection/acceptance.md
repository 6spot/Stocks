# 验收记录

- 修复：`research-page.tsx` 不再用 URL 报告 ID 反查列表项来判定初始选择；无 `report` 参数时从列表（最新在前）取最新 `run_status=succeeded` 的报告，桌面（与 `xl` 双栏一致的 `min-width: 80rem`）用 `replace` 打开；小屏保留「选择一份报告」提示与可用的「打开最新报告」按钮；显式 `report` 参数始终优先，包括不在当前列表分页中的报告。
- 空列表、加载中、读取失败均不产生默认选择、不写 URL、不显示按钮；手动选择与浏览器前进/后退保持有效。
- 测试：`frontend/src/features/research/research-page.test.tsx` 新增初始选择套件（桌面 replace、跳过失败 Run、小屏按钮、显式参数优先、空/加载/失败、前进后退）。
- 验证位置：`100.120.14.84:/tmp/stocks-review-fixes-20261023/frontend`，复用已验证的 `node_modules`（符号链接，未安装依赖）。
- 命令与结果（最终一轮）：
  - `./node_modules/.bin/vitest run src/features/research/research-page.test.tsx --no-file-parallelism --maxWorkers=1 --no-cache` → **10 passed，退出码 0**（`report-vitest.log`）。
  - `npm run typecheck`（`tsc -b --pretty false`）→ 退出码 0（`report-typecheck.log`）。
  - `npm run build`（typecheck + `vite build`）→ 退出码 0，`built in 4.94s`（`report-build.log`）。
- 回归有效性：实现方此前用 `HEAD` 版本源码运行，恰有两个新行为用例失败；修正后 10/10 通过。
- 审查：`trellis-check` 子代理未发现阻断问题；采纳其建议把断点改为与 Tailwind `xl` 一致的 `min-width: 80rem`，并同步更新测试桩与文档。
- 限制：jsdom 中以 `window.matchMedia` 桩验证断点，未做真实浏览器/Playwright 视口验收；未运行全量前端测试。
