// Run only in this task's isolated directory on the designated test server:
// node --experimental-transform-types --test .trellis/tasks/10-21-pi-trellis-magpie/verification/progress.test.mjs
// Node >= 22.7; no Pi installation, model requests, credentials, or npm deps.
import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const fixture = mkdtempSync(fileURLToPath(new URL('./.progress-fixture-', import.meta.url)));
let projectRoot = fileURLToPath(new URL('.', import.meta.url));
while (!existsSync(join(projectRoot, '.pi', 'extensions', 'trellis', 'index.ts'))) {
  const parent = dirname(projectRoot);
  if (parent === projectRoot) throw new Error('Cannot locate the project Pi extension');
  projectRoot = parent;
}
const source = readFileSync(join(projectRoot, '.pi', 'extensions', 'trellis', 'index.ts'), 'utf8');
const modulePath = join(fixture, 'extension.mts');
// Expose private helpers only in this disposable copy of the actual extension.
writeFileSync(modulePath, source + '\nexport { newRun, applyEvent, finishRun, renderProgressCard, progressKey, runPi, runSubagent };\n');
const api = await import(pathToFileURL(modulePath).href);
const envKeys = ['TRELLIS_PI_CLI_JS', 'PI_CODING_AGENT_SESSION_DIR'];
const originalEnv = Object.fromEntries(envKeys.map(k => [k, process.env[k]]));
const childPath = join(fixture, 'child.mjs');
const counterPath = join(fixture, 'invocations');
process.env.TRELLIS_PI_CLI_JS = childPath;
process.env.PI_CODING_AGENT_SESSION_DIR = join(fixture, 'sessions');
mkdirSync(join(fixture, '.pi', 'agents'), { recursive: true });
writeFileSync(join(fixture, '.pi', 'agents', 'trellis-implement.md'),
  '---\nname: trellis-implement\nmodel: magpie/group/deepseek\n---\nFixture role.\n');
writeFileSync(childPath, `
import { appendFileSync } from 'node:fs';
appendFileSync(${JSON.stringify(counterPath)}, 'spawn\\n');
const event = value => process.stdout.write(JSON.stringify(value) + '\\n');
const message = (text, stopReason = 'stop', errorMessage) => event({
  type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text }], stopReason, errorMessage }
});
const complete = text => {
  message(text);
  event({ type: 'agent_end', willRetry: false });
  event({ type: 'agent_settled', aborted: false });
};
let prompt = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { prompt += chunk; });
process.stdin.on('end', () => {
  event({ type: 'agent_start' });
  if (prompt.includes('[fixture:block]')) {
    complete('BLOCKED: SSH authentication unavailable\\nCompleted: review. Unverified: remote tests. Needed: authorized access.');
  } else if (prompt.includes('[fixture:error]')) {
    message('Preparing verification', 'toolUse');
    message('', 'error', '503 provider unavailable');
    event({ type: 'agent_end', willRetry: false });
    event({ type: 'agent_settled', aborted: false });
  } else if (prompt.includes('[fixture:utf8]')) {
    const bytes = Buffer.from(JSON.stringify({ type: 'message_end', message: {
      role: 'assistant', content: [{ type: 'text', text: '正在检查。' }], stopReason: 'stop'
    } }) + '\\n');
    const split = bytes.indexOf(Buffer.from('正')) + 1;
    process.stdout.write(bytes.subarray(0, split));
    setTimeout(() => {
      process.stdout.write(bytes.subarray(split));
      event({ type: 'agent_settled', aborted: false });
    }, 20);
  } else if (prompt.includes('[fixture:idle]')) {
    process.stderr.write('fixture: waiting for a tool\\n');
    setTimeout(() => complete('Fixture finished.'), 6500);
  } else if (prompt.includes('[fixture:cancel]')) {
    process.stderr.write('fixture: cancellable wait\\n');
    setTimeout(() => complete('Should be cancelled first.'), 60000);
  } else {
    complete('Fixture finished.');
  }
});
`);
after(() => {
  for (const key of envKeys) {
    if (originalEnv[key] === undefined) delete process.env[key];
    else process.env[key] = originalEnv[key];
  }
  rmSync(fixture, { recursive: true, force: true });
});

const run = () => api.newRun('fixture', 'trellis-implement', 'fixture');
const details = r => ({
  kind: 'trellis-subagent-progress', agent: r.agent, mode: 'single',
  startedAt: r.startedAt ?? Date.now(), updatedAt: Date.now(), final: false, runs: [r],
});
const message = (r, text, stopReason = 'stop', errorMessage) => api.applyEvent(r, {
  type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text }], stopReason, errorMessage },
});
const card = r => api.renderProgressCard(details(r), false, 240).join('\n');

test('collapsed card exposes tool failures and silence, without private reasoning or full tool output', () => {
  const r = run();
  api.applyEvent(r, { type: 'agent_start' });
  api.applyEvent(r, { type: 'tool_execution_start', toolCallId: 'ssh', toolName: 'bash', args: { command: 'ssh example.invalid uname -m' } });
  r.lastEventAt = Date.now() - 90_000;
  api.applyEvent(r, { type: 'tool_execution_update', toolCallId: 'ssh', partialResult: { content: [{ type: 'text', text: 'private-full-tool-output' }] } });
  assert.ok(Date.now() - r.lastEventAt < 1000);
  assert.ok(!JSON.stringify(r).includes('private-full-tool-output'));
  api.applyEvent(r, { type: 'tool_execution_end', toolCallId: 'ssh', isError: true, result: { content: [{ type: 'text', text: 'Permission denied (publickey,password).' }] } });
  r.thinkingTail = 'private-reasoning-sentinel';
  r.lastEventAt = Date.now() - 61_000;
  assert.match(card(r), /Permission denied/);
  assert.match(card(r), /No child events for/);
  assert.doesNotMatch(card(r), /private-reasoning-sentinel/);
});

test('public text changes are visible without a usage or tool-counter change', () => {
  const r = run();
  api.applyEvent(r, { type: 'agent_start' });
  const d = details(r);
  const before = api.progressKey(d);
  api.applyEvent(r, { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'Inspecting scheduler.' } });
  assert.notEqual(api.progressKey(d), before);
  assert.match(card(r), /Inspecting scheduler/);
  api.applyEvent(r, { type: 'message_start', message: { role: 'assistant' } });
  api.applyEvent(r, { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'Testing scheduler.' } });
  assert.match(card(r), /Testing scheduler/);
  assert.doesNotMatch(card(r), /Inspecting scheduler/);
});

test('agent_end during retry is not success; successful retry clears transient errors', () => {
  const r = run();
  api.applyEvent(r, { type: 'agent_start' });
  message(r, '', 'error', '503 unavailable');
  api.applyEvent(r, { type: 'agent_end', willRetry: true });
  assert.equal(r.status, 'running');
  assert.equal(r.finishedAt, undefined);
  api.applyEvent(r, { type: 'auto_retry_start', attempt: 1, maxAttempts: 3, delayMs: 2000, errorMessage: '503 unavailable' });
  assert.match(card(r), /retry 1\/3/);
  assert.match(card(r), /503 unavailable/);
  api.applyEvent(r, { type: 'agent_start' });
  message(r, 'Done.');
  api.applyEvent(r, { type: 'auto_retry_end', success: true, attempt: 1 });
  api.applyEvent(r, { type: 'agent_settled', aborted: false });
  assert.equal(api.finishRun(r, 0, '').failed, false);
  assert.equal(r.status, 'succeeded');
});

test('compaction is visible and a terminal retry failure cannot become success', () => {
  const r = run();
  api.applyEvent(r, { type: 'agent_start' });
  api.applyEvent(r, { type: 'compaction_start', reason: 'overflow' });
  assert.match(card(r), /Compacting context/);
  api.applyEvent(r, { type: 'compaction_end', aborted: false });
  assert.doesNotMatch(card(r), /Compacting context/);
  api.applyEvent(r, { type: 'auto_retry_end', success: false, finalError: 'Retries exhausted' });
  assert.equal(api.finishRun(r, 0, '').failed, true);
  assert.match(card(r), /Retries exhausted/);
});

for (const stopReason of ['error', 'aborted', 'length', 'toolUse']) {
  test(`zero exit does not mask ${stopReason}`, () => {
    const r = run();
    message(r, 'Earlier progress.', stopReason, stopReason === 'error' ? 'Provider failed' : undefined);
    const result = api.finishRun(r, 0, '');
    assert.equal(result.failed, true);
    assert.notEqual(r.status, 'succeeded');
    assert.ok(!result.output.startsWith('Earlier progress.'));
  });
}

test('explicit blocker, missing final report, and process failure are actionable failures', () => {
  const blocked = run();
  message(blocked, 'BLOCKED: Missing authorized SSH access.\nCompleted review; remote tests unverified.');
  assert.equal(api.finishRun(blocked, 0, '').failed, true);
  assert.match(card(blocked), /BLOCKED: Missing/);
  assert.equal(api.finishRun(run(), 0, '').failed, true);
  const failed = run();
  message(failed, 'I will check the environment.');
  assert.match(api.finishRun(failed, 1, 'Connection refused').output, /^Connection refused/);
});

test('new diagnostic and command summaries redact common credential fields', () => {
  const r = run();
  api.applyEvent(r, { type: 'tool_execution_start', toolCallId: 'secret', toolName: 'bash', args: { command: 'API_KEY=fixture-secret command' } });
  api.applyEvent(r, { type: 'tool_execution_end', toolCallId: 'secret', isError: true, result: { content: [{ type: 'text', text: 'Authorization: Bearer fixture-token\nPermission denied' }] } });
  assert.doesNotMatch(JSON.stringify(r), /fixture-secret|fixture-token/);
  assert.match(card(r), /Permission denied/);
});

test('actual child returning model error with exit 0 reports failure ahead of earlier commentary', async () => {
  const r = run();
  const result = await api.runPi(fixture, '[fixture:error]', {}, r, () => {});
  assert.equal(result.failed, true);
  assert.equal(r.status, 'failed');
  assert.match(result.output, /^503 provider unavailable/);
});

test('collapsed parallel card retains another child failure after the last child succeeds', () => {
  const failed = run();
  message(failed, 'BLOCKED: Missing SSH access.');
  api.finishRun(failed, 0, '');
  const successful = run();
  message(successful, 'Review completed.');
  api.finishRun(successful, 0, '');
  const d = { ...details(successful), mode: 'parallel', final: true, runs: [failed, successful] };
  assert.match(api.renderProgressCard(d, false, 240).join('\n'), /BLOCKED: Missing SSH access/);
});

test('stream decoder preserves UTF-8 split across subprocess chunks', async () => {
  const r = run();
  const result = await api.runPi(fixture, '[fixture:utf8]', {}, r, () => {});
  assert.equal(result.failed, false);
  assert.equal(result.output, '正在检查。');
});

test('blocked child stops a chain before the next subprocess', async () => {
  const count = () => existsSync(counterPath) ? readFileSync(counterPath, 'utf8').trim().split('\n').length : 0;
  const before = count();
  const result = await api.runSubagent(fixture, {
    agent: 'trellis-implement', mode: 'chain', prompts: ['[fixture:block]', '[fixture:success]'],
  }, null, undefined, undefined, undefined, undefined, {
    modelRegistry: { find: () => ({ id: 'group/deepseek' }) },
    sessionManager: { getSessionId: () => 'fixture-parent' },
  });
  assert.equal(result.failed, true);
  assert.equal(result.details.runs.length, 1);
  assert.equal(count() - before, 1);
  assert.match(result.output, /^BLOCKED:/);
});

test('stderr is live, silent child refreshes, and updates stop after exit', { timeout: 20_000 }, async () => {
  const r = run();
  const samples = [];
  const result = await api.runPi(fixture, '[fixture:idle]', {}, r, () => {
    samples.push({ at: Date.now(), eventAt: r.lastEventAt, stderr: r.stderrTail, status: r.status });
  });
  assert.equal(result.failed, false);
  const waiting = samples.filter(s => s.status === 'running' && s.stderr.includes('waiting for a tool'));
  assert.ok(waiting.length >= 2, 'stderr update and later timer refresh should both be delivered');
  assert.ok(waiting.some(s => s.at - s.eventAt >= 4000), 'refresh must not fabricate a child event');
  const count = samples.length;
  await delay(5500);
  assert.equal(samples.length, count, 'no refreshes after the process exits');
});

test('cancellation returns failure and a cancelled card rather than a success tick', { timeout: 5000 }, async () => {
  const r = run();
  const controller = new AbortController();
  const result = await api.runPi(fixture, '[fixture:cancel]', {}, r, () => {
    if (r.stderrTail.includes('cancellable wait')) controller.abort();
  }, null, controller.signal);
  assert.equal(result.failed, true);
  assert.equal(r.status, 'cancelled');
  const d = { ...details(r), final: true };
  const rendered = api.renderProgressCard(d, false, 240).join('\n');
  assert.match(rendered, /^⊘/);
  assert.match(rendered, /cancelled/i);
});
