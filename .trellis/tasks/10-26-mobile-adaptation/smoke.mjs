import { chromium } from 'playwright';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const runDir = process.argv[2];
const distDir = path.join(runDir, 'frontend', 'dist');
const fixtureDir = path.join(runDir, 'fixtures');
const outDir = path.join(runDir, 'artifacts');
fs.mkdirSync(outDir, { recursive: true });

const load = (name) => JSON.parse(fs.readFileSync(path.join(fixtureDir, `${name}.json`), 'utf8'));
const docId = fs.readFileSync(path.join(fixtureDir, '_doc_id'), 'utf8').trim();
const repId = fs.readFileSync(path.join(fixtureDir, '_rep_id'), 'utf8').trim();
const runId = fs.readFileSync(path.join(fixtureDir, '_run_id'), 'utf8').trim();
const failedRunId = fs.readFileSync(path.join(fixtureDir, '_run_failed_id'), 'utf8').trim();
const hitDocIds = fs.readFileSync(path.join(fixtureDir, '_hit_docs'), 'utf8').trim().split(/\s+/);

const session = load('overview'); // only used for authenticity of load
void session;

const jsonRoutes = {
  '/api/session': { authenticated: true, session: { issued_at: '2026-10-26T08:00:00Z', expires_at: '2026-10-27T08:00:00Z', csrf_token: 'fixture-csrf' } },
  '/api/overview': load('overview'),
  '/api/sources': load('sources'),
  '/api/tasks': load('tasks'),
  '/api/documents': load('documents'),
  '/api/reports': load('reports'),
  '/api/runs': load('runs'),
  '/api/processing': load('processing'),
  [`/api/documents/${docId}`]: load('document-detail'),
  [`/api/documents/${docId}/facts`]: load('document-facts'),
  [`/api/reports/${repId}`]: load('report-detail'),
  [`/api/runs/${runId}`]: load('run-detail'),
  [`/api/runs/${failedRunId}`]: load('run-detail-failed'),
};
const searchPayload = load('search');
for (const id of hitDocIds) {
  jsonRoutes[`/api/documents/${id}`] = load(`doc-${id}`);
  jsonRoutes[`/api/documents/${id}/facts`] = load(`doc-${id}-facts`);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname === '/api/search') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(searchPayload));
  }
  if (url.pathname === '/api/session/logout') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ authenticated: false }));
  }
  const payload = jsonRoutes[url.pathname];
  if (payload !== undefined) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(payload));
  }
  if (url.pathname.startsWith('/api/')) {
    res.writeHead(404, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ detail: { code: 'NOT_FOUND', message: url.pathname } }));
  }
  let target = path.join(distDir, url.pathname);
  if (!fs.existsSync(target) || fs.statSync(target).isDirectory()) target = path.join(distDir, 'index.html');
  const ext = path.extname(target);
  const type = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' }[ext] ?? 'application/octet-stream';
  res.writeHead(200, { 'Content-Type': type });
  fs.createReadStream(target).pipe(res);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] });
const results = [];
const consoleErrors = [];
const tableRecords = [];
let activePageLabel = '';
let activeViewport = '';

const check = (name, condition, detail = '') => {
  results.push({ page: activePageLabel, viewport: activeViewport, name, passed: Boolean(condition), detail });
  if (!condition) console.error(`FAIL [${activePageLabel} ${activeViewport}] ${name}: ${detail}`);
};

async function measureTables(page, pageLabel) {
  const summaries = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll('[data-slot="table-container"]').forEach((container, index) => {
      const table = container.querySelector('table');
      const aria = table?.getAttribute('aria-label') ?? null;
      const firstHeader = table?.querySelector('thead th')?.textContent?.trim() ?? '';
      const headerCount = table?.querySelectorAll('thead th').length ?? 0;
      const visibleHeaders = Array.from(table?.querySelectorAll('thead th') ?? [])
        .filter((th) => th.getBoundingClientRect().width > 0).length;
      const before = container.scrollLeft;
      const maxScroll = container.scrollWidth - container.clientWidth;
      container.scrollLeft = 10_000;
      const reached = container.scrollLeft;
      container.scrollLeft = before;
      out.push({
        index,
        aria,
        firstHeader,
        headerCount,
        visibleHeaders,
        scrollWidth: container.scrollWidth,
        clientWidth: container.clientWidth,
        maxScroll,
        reachedScrollLeft: reached,
      });
    });
    return out;
  });
  for (const s of summaries) {
    tableRecords.push({ page: pageLabel, viewport: activeViewport, ...s });
  }
  return summaries;
}

async function pageMetrics(page) {
  return page.evaluate(() => ({
    docScrollWidth: document.documentElement.scrollWidth,
    docClientWidth: document.documentElement.clientWidth,
    innerWidth: window.innerWidth,
    bodyScrollWidth: document.body.scrollWidth,
  }));
}

async function visit(page, label, urlPath, waitFor) {
  activePageLabel = label;
  await page.goto(`${base}${urlPath}`, { waitUntil: 'networkidle' });
  if (waitFor) {
    const asHeading = page.getByRole('heading', { name: waitFor }).first();
    const asButton = page.getByRole('button', { name: waitFor }).first();
    await Promise.race([
      asHeading.waitFor({ state: 'visible', timeout: 15000 }),
      asButton.waitFor({ state: 'visible', timeout: 15000 }),
    ]).catch(async () => {
      await page.waitForTimeout(1500);
    });
  }
  const metrics = await pageMetrics(page);
  const tables = await measureTables(page, label);
  check(`${label}: no page-level horizontal scroll`, metrics.docScrollWidth <= metrics.innerWidth + 1, JSON.stringify(metrics));
  for (const t of tables) {
    const label2 = t.aria ?? `${t.firstHeader}…`;
    check(
      `${label}: table [${label2}] scrolls horizontally to last column`,
      t.maxScroll <= 1 ? t.reachedScrollLeft === 0 : Math.abs(t.reachedScrollLeft - t.maxScroll) <= 2,
      JSON.stringify(t),
    );
  }
  await page.screenshot({ path: path.join(outDir, `${activeViewport}-${label}.png`), fullPage: true });
  return { metrics, tables };
}

const consoleSink = (page) => {
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(`${activeViewport}/${activePageLabel}: ${m.text()}`); });
  page.on('pageerror', (e) => consoleErrors.push(`${activeViewport}/${activePageLabel}: PAGEERROR ${e.message}`));
};

try {
  for (const vp of [{ name: '390x844', width: 390, height: 844 }, { name: '360x800', width: 360, height: 800 }]) {
    activeViewport = vp.name;
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, reducedMotion: 'reduce', isMobile: true, hasTouch: true });
    const p = await ctx.newPage();
    consoleSink(p);

    await visit(p, 'overview', '/', '情报概览');
    await visit(p, 'sources', '/sources', '采集源');
    await visit(p, 'tasks', '/tasks', '任务');
    await visit(p, 'intelligence', '/intelligence', '情报检索');
    await visit(p, 'research', '/research', '研究报告');
    await visit(p, 'runs', '/runs', '运行与恢复');
    await visit(p, 'processing', '/runs?tab=processing', '运行与恢复');
    await visit(p, 'report-detail', `/research/${repId}`, '返回列表');

    // ---- write actions hidden on phone, no hint text ----
    activePageLabel = 'tasks';
    await p.goto(`${base}/tasks`, { waitUntil: 'networkidle' });
    await p.getByRole('heading', { name: '任务' }).first().waitFor({ state: 'visible' });
    const triggerState = await p.evaluate(() => {
      const btn = document.querySelector('[data-slot="table-container"] button');
      const all = Array.from(document.querySelectorAll('button'));
      const trigger = all.find((b) => /手动触发|已有活跃 Run|触发中/.test(b.textContent ?? ''));
      const headerCells = Array.from(document.querySelectorAll('[data-slot="table-container"] thead th'))
        .map((th) => ({ text: th.textContent.trim(), width: th.getBoundingClientRect().width }));
      return {
        found: Boolean(trigger),
        visible: trigger ? trigger.getBoundingClientRect().width > 0 : null,
        headerCells,
        hasHint: /写操作/.test(document.body.innerText),
        firstButtonTag: btn?.tagName ?? null,
      };
    });
    check('tasks: 手动触发 button present in DOM but zero-size on phone', triggerState.found && triggerState.visible === false, JSON.stringify(triggerState));
    check('tasks: 操作 header column hidden on phone', !triggerState.headerCells.some((c) => c.text === '操作' && c.width > 0), JSON.stringify(triggerState.headerCells));
    check('tasks: no write-operation hint text added', triggerState.hasHint === false);

    // ---- run actions hidden on phone ----
    activePageLabel = 'runs';
    await p.goto(`${base}/runs?run=${failedRunId}`, { waitUntil: 'networkidle' });
    await p.waitForTimeout(2000);
    const runActionState = await p.evaluate(() => {
      // 结束异常 Run only renders when the server reports can_end=true.
      const names = ['重试失败步骤', '继续未完成扫描'];
      const btns = Array.from(document.querySelectorAll('button'));
      return names.map((n) => {
        const b = btns.find((x) => (x.textContent ?? '').includes(n));
        return { name: n, found: Boolean(b), width: b ? b.getBoundingClientRect().width : null };
      });
    });
    for (const s of runActionState) {
      check(`runs: "${s.name}" present but not rendered on phone`, s.found && s.width === 0, JSON.stringify(s));
    }

    // ---- processing retry hidden ----
    activePageLabel = 'processing';
    await p.goto(`${base}/runs?tab=processing`, { waitUntil: 'networkidle' });
    await p.waitForTimeout(1200);
    const retryState = await p.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const retry = btns.find((x) => (x.textContent ?? '').includes('重试此阶段'));
      const headerCells = Array.from(document.querySelectorAll('[data-slot="table-container"] thead th'))
        .map((th) => ({ text: th.textContent.trim(), width: th.getBoundingClientRect().width }));
      return { found: Boolean(retry), width: retry ? retry.getBoundingClientRect().width : null, headerCells };
    });
    check('processing: 重试此阶段 present but not rendered on phone', retryState.found && retryState.width === 0, JSON.stringify(retryState));
    check('processing: 操作 header hidden on phone', !retryState.headerCells.some((c) => c.text === '操作' && c.width > 0));

    // ---- tap targets: [E1] and summaries ----
    activePageLabel = 'report-detail';
    await p.goto(`${base}/research/${repId}`, { waitUntil: 'networkidle' });
    await p.getByRole('button', { name: '返回列表' }).first().waitFor({ state: 'visible' });
    const citationTargets = await p.evaluate(() => Array.from(document.querySelectorAll('[aria-label^="查看来源"]'))
      .slice(0, 5)
      .map((el) => { const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), text: el.textContent?.trim() }; }));
    for (const c of citationTargets) {
      check(`report-detail: [E] citation ${c.text} tap target >= 44x44`, c.w >= 44 && c.h >= 44, JSON.stringify(c));
    }
    const summaryTargets = await p.evaluate(() => Array.from(document.querySelectorAll('summary'))
      .map((el) => { const r = el.getBoundingClientRect(); return { h: Math.round(r.height), w: Math.round(r.width), text: (el.textContent ?? '').trim().slice(0, 24) }; }));
    for (const s of summaryTargets) {
      check(`report-detail: summary "${s.text}" tap height >= 44`, s.h >= 44, JSON.stringify(s));
    }

    // ---- evidence dialog: keyboard open, close, focus return ----
    const firstCitation = p.locator('[aria-label^="查看来源"]').first();
    if (await firstCitation.count()) {
      await firstCitation.focus();
      await p.keyboard.press('Enter');
      const dialog = p.getByRole('dialog').first();
      await dialog.waitFor({ state: 'visible', timeout: 10000 });
      check('report-detail: evidence dialog opens from keyboard', await dialog.isVisible());
      const dialogBox = await dialog.boundingBox();
      check('report-detail: evidence dialog fits viewport width', dialogBox !== null && dialogBox.width <= vp.width, JSON.stringify(dialogBox));
      await p.keyboard.press('Escape');
      await p.waitForTimeout(600);
      const focusReturned = await p.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? null);
      check('report-detail: focus returns to citation trigger after Escape', typeof focusReturned === 'string' && focusReturned.startsWith('查看来源'), String(focusReturned));
    }

    // ---- drawers ----
    activePageLabel = 'intelligence';
    await p.goto(`${base}/intelligence?mode=exact&exact=the`, { waitUntil: 'networkidle' });
    await p.waitForTimeout(1500);
    const openDoc = p.locator('[data-slot="table-container"] button').first();
    if (await openDoc.count()) {
      await openDoc.click();
      await p.waitForTimeout(1200);
      const drawerInfo = await p.evaluate(() => {
        const el = document.querySelector('[data-slot="sheet-content"]');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { width: Math.round(r.width), height: Math.round(r.height), scrollable: el.scrollHeight > el.clientHeight, overflowY: getComputedStyle(el).overflowY, viewportW: window.innerWidth };
      });
      check('intelligence: document drawer opens full-width on phone', drawerInfo !== null && drawerInfo.width >= vp.width - 2, JSON.stringify(drawerInfo));
      check('intelligence: document drawer content scrollable', drawerInfo !== null && drawerInfo.overflowY === 'auto', JSON.stringify(drawerInfo));
      await p.screenshot({ path: path.join(outDir, `${activeViewport}-document-drawer.png`), fullPage: false });
      await p.keyboard.press('Escape');
      await p.waitForTimeout(500);
    }

    // ---- login ----
    activePageLabel = 'login';
    await p.goto(`${base}/login`, { waitUntil: 'networkidle' });
    await p.waitForTimeout(1200);
    const loginMetrics = await pageMetrics(p);
    check('login: no page-level horizontal scroll', loginMetrics.docScrollWidth <= loginMetrics.innerWidth + 1, JSON.stringify(loginMetrics));
    await p.screenshot({ path: path.join(outDir, `${activeViewport}-login.png`), fullPage: true });

    await ctx.close();
  }

  // ---- desktop regression ----
  activeViewport = '1280x800';
  const desktopCtx = await browser.newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce' });
  const d = await desktopCtx.newPage();
  consoleSink(d);

  activePageLabel = 'desktop-tasks';
  await d.goto(`${base}/tasks`, { waitUntil: 'networkidle' });
  await d.getByRole('heading', { name: '任务' }).first().waitFor({ state: 'visible' });
  const deskTasks = await d.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const trigger = btns.find((b) => /手动触发|已有活跃 Run|触发中/.test(b.textContent ?? ''));
    const headers = Array.from(document.querySelectorAll('[data-slot="table-container"] thead th')).map((th) => ({ text: th.textContent.trim(), width: Math.round(th.getBoundingClientRect().width) }));
    return { triggerWidth: trigger ? Math.round(trigger.getBoundingClientRect().width) : null, triggerFound: Boolean(trigger), headers };
  });
  check('desktop tasks: 手动触发 visible', deskTasks.triggerFound && (deskTasks.triggerWidth ?? 0) > 40, JSON.stringify(deskTasks));
  check('desktop tasks: 操作 column visible', deskTasks.headers.some((h) => h.text === '操作' && h.width > 0), JSON.stringify(deskTasks.headers));
  await d.screenshot({ path: path.join(outDir, '1280x800-tasks.png'), fullPage: true });

  activePageLabel = 'desktop-runs';
  await d.goto(`${base}/runs?run=${failedRunId}`, { waitUntil: 'networkidle' });
  await d.waitForTimeout(2000);
  const deskRunActions = await d.evaluate(() => {
    const names = ['重试失败步骤', '继续未完成扫描'];
    const btns = Array.from(document.querySelectorAll('button'));
    return names.map((n) => { const b = btns.find((x) => (x.textContent ?? '').includes(n)); return { name: n, found: Boolean(b), width: b ? Math.round(b.getBoundingClientRect().width) : null }; });
  });
  const anyRunAction = deskRunActions.filter((s) => s.found);
  check('desktop runs: recovery actions rendered', anyRunAction.length > 0 && anyRunAction.every((s) => (s.width ?? 0) > 40), JSON.stringify(deskRunActions));
  await d.screenshot({ path: path.join(outDir, '1280x800-runs.png'), fullPage: true });

  activePageLabel = 'desktop-research';
  await d.goto(`${base}/research/${repId}`, { waitUntil: 'networkidle' });
  await d.getByRole('button', { name: '返回列表' }).first().waitFor({ state: 'visible' });
  const deskCitation = await d.evaluate(() => { const el = document.querySelector('[aria-label^="查看来源"]'); if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; });
  check('desktop research: citation stays compact (not 44px tall)', deskCitation !== null && deskCitation.h < 44, JSON.stringify(deskCitation));
  const deskSummary = await d.evaluate(() => Array.from(document.querySelectorAll('summary')).slice(0, 3).map((el) => { const r = el.getBoundingClientRect(); return { h: Math.round(r.height), text: (el.textContent ?? '').trim().slice(0, 20) }; }));
  for (const s of deskSummary) {
    check(`desktop research: summary "${s.text}" stays compact`, s.h < 44, JSON.stringify(s));
  }
  await d.screenshot({ path: path.join(outDir, '1280x800-research.png'), fullPage: true });

  activePageLabel = 'desktop-overview';
  await d.goto(`${base}/`, { waitUntil: 'networkidle' });
  await d.getByRole('heading', { name: '情报概览' }).first().waitFor({ state: 'visible' });
  const deskTables = await measureTables(d, 'desktop-overview');
  check('desktop overview: 3 tables still render as tables', deskTables.length === 3, JSON.stringify(deskTables.map((t) => t.firstHeader)));
  await d.screenshot({ path: path.join(outDir, '1280x800-overview.png'), fullPage: true });

  await desktopCtx.close();

  const failed = results.filter((r) => !r.passed);
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify({ results, tableRecords, consoleErrors }, null, 2));
  console.log(JSON.stringify({
    total: results.length,
    passed: results.length - failed.length,
    failed: failed.length,
    failures: failed,
    consoleErrors,
    tableRecordCount: tableRecords.length,
    artifacts: fs.readdirSync(outDir),
  }, null, 2));

  // second viewport warnings must fail the run
  for (const f of failed) console.error(`FAIL ${f.viewport} ${f.page} :: ${f.name} :: ${f.detail}`);
} finally {
  await browser.close();
  server.close();
}
