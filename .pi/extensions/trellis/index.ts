import {
  existsSync,
  readFileSync,
  realpathSync,
  statSync,
} from "node:fs";
import { createHash, randomBytes } from "node:crypto";
import {
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
} from "node:path";
import { spawnSync } from "node:child_process";
import { isUtf8 } from "node:buffer";

// ── Types ──────────────────────────────────────────────────────────────
type JsonObject = Record<string, unknown>;
interface PiExtensionContext {
  cwd?: string;
  sessionManager?: {
    getSessionId?: () => string;
    getSessionFile?: () => string | undefined;
  };
  ui?: {
    notify?: (msg: string, type?: "info" | "warning" | "error") => void;
  };
}
// ── Constants ─────────────────────────────────────────────────────────
const TRELLIS_AGENT_JSONL: Record<string, string> = {
  "trellis-implement": "implement.jsonl",
  implement: "implement.jsonl",
  "trellis-check": "check.jsonl",
  check: "check.jsonl",
};
const SESSION_OVERVIEW_TIMEOUT_MS = 1500;
const FIRST_REPLY_NOTICE = `<first-reply-notice>
On the first visible assistant reply in this session, briefly acknowledge that Trellis SessionStart context loaded.
Choose the acknowledgment language in this order:
1. Use the language of the user's current request (the user message that triggered this reply).
2. If that request has no clear natural language, use an explicitly established project communication language.
3. If neither provides a language, output the language-neutral fallback exactly: \`Trellis SessionStart ✓\`.
Continue directly with the user's request after the acknowledgment.
The acknowledgment must not alter the language used for the remainder of the response.
This notice is one-shot: do not repeat it after the first visible assistant reply in this session.
</first-reply-notice>`;

// ── Utilities ─────────────────────────────────────────────────────────
function isObj(v: unknown): v is JsonObject {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v.trim() : null;
}
function hash(s: string) {
  return createHash("sha256").update(s).digest("hex").slice(0, 24);
}
function readText(p: string) {
  try {
    return readFileSync(p, "utf-8");
  } catch {
    return "";
  }
}
function exists(p: string) {
  try {
    return statSync(p).isFile();
  } catch {
    return false;
  }
}
function shellQuote(v: string) {
  return `'${v.replace(/'/g, `'\\''`)}'`;
}
function callStr(
  cb: (() => string | undefined) | undefined,
  receiver?: unknown,
): string | null {
  if (!cb) return null;
  try {
    return str(cb.call(receiver));
  } catch {
    return null;
  }
}
function lookupStr(data: unknown, keys: string[]): string | null {
  if (!isObj(data)) return null;
  for (const k of keys) {
    const v = str(data[k]);
    if (v) return v;
  }
  for (const nk of [
    "input",
    "properties",
    "event",
    "hook_input",
    "hookInput",
  ]) {
    const nested = data[nk];
    const v = lookupStr(nested, keys);
    if (v) return v;
  }
  return null;
}
function cmdHasTrellisCtx(cmd: string) {
  const t = cmd.trimStart();
  return (
    /^export\s+TRELLIS_CONTEXT_ID=/.test(t) ||
    /^TRELLIS_CONTEXT_ID=/.test(t) ||
    /^env\s+.*TRELLIS_CONTEXT_ID=/.test(t)
  );
}
// ── Context Injection Limits (issue #441) ───────────────────────────────
//
// Context injection behavior mirrors the shared-hooks Python hook.
// Keep the limits and truncation notices consistent with that implementation.
interface ContextInjectionLimits {
  max_file_bytes: number;
  max_artifact_bytes: number;
  max_total_bytes: number;
}
const DEFAULT_CONTEXT_INJECTION_LIMITS: ContextInjectionLimits = {
  max_file_bytes: 32768,
  max_artifact_bytes: 65536,
  max_total_bytes: 131072,
};

function truncateUtf8(buf: Buffer, cap: number): Buffer {
  if (cap <= 0 || buf.length <= cap) return buf;
  let i = cap;
  // Back off over continuation bytes (10xxxxxx) to find the lead byte.
  while (i > 0 && (buf[i - 1]! & 0xc0) === 0x80) i--;
  if (i === 0) return Buffer.alloc(0);
  const lead = buf[i - 1]!;
  if (lead & 0x80) {
    let seqLen = 1;
    if ((lead & 0xe0) === 0xc0) seqLen = 2;
    else if ((lead & 0xf0) === 0xe0) seqLen = 3;
    else if ((lead & 0xf8) === 0xf0) seqLen = 4;
    // Drop the lead byte too if its full sequence didn't fit.
    if (i - 1 + seqLen > cap) i--;
  }
  return buf.subarray(0, i);
}

function stripInlineComment(value: string): string {
  let inQuote: string | null = null;
  for (let idx = 0; idx < value.length; idx++) {
    const ch = value[idx]!;
    if (inQuote) {
      if (ch === inQuote) inQuote = null;
      continue;
    }
    if (ch === '"' || ch === "'") {
      inQuote = ch;
      continue;
    }
    if (ch === "#" && (idx === 0 || /\s/.test(value[idx - 1]!)))
      return value.slice(0, idx);
  }
  return value;
}
function unquoteYaml(s: string): string {
  if (s.length >= 2 && s[0] === s[s.length - 1] && (s[0] === '"' || s[0] === "'"))
    return s.slice(1, -1);
  return s;
}

/** Line-based parser for ONLY the `context_injection:` block of
 * `.trellis/config.yaml`. Not a general YAML parser — mirrors
 * `common.config.get_context_injection_limits()` semantics for this
 * section only (missing keys keep the default; invalid/negative values
 * fall back to the default for that key). */
function readContextInjectionLimits(repoRoot: string): ContextInjectionLimits {
  const limits: ContextInjectionLimits = { ...DEFAULT_CONTEXT_INJECTION_LIMITS };
  const text = readText(join(repoRoot, ".trellis", "config.yaml"));
  if (!text) return limits;

  let inSection = false;
  let sectionIndent = -1;
  for (const rawLine of text.split(/\r?\n/)) {
    const trimmed = rawLine.trim();
    if (!inSection) {
      if (/^context_injection\s*:\s*(#.*)?$/.test(trimmed)) {
        inSection = true;
        sectionIndent = rawLine.length - rawLine.trimStart().length;
      }
      continue;
    }
    if (!trimmed || trimmed.startsWith("#")) continue;
    const indent = rawLine.length - rawLine.trimStart().length;
    if (indent <= sectionIndent) break;
    const m = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$/);
    if (!m) continue;
    const key = m[1]!;
    if (!(key in limits)) continue;
    const raw = unquoteYaml(stripInlineComment(m[2]!).trim()).trim();
    if (!/^-?\d+$/.test(raw)) continue; // invalid -> keep default
    const value = parseInt(raw, 10);
    if (value < 0) continue; // negative -> keep default
    (limits as unknown as Record<string, number>)[key] = value;
  }
  return limits;
}

class ContextBudget {
  used = 0;
  constructor(private maxTotalBytes: number) {}
  hasRoom(size: number): boolean {
    if (this.maxTotalBytes <= 0) return true;
    return this.used + size <= this.maxTotalBytes;
  }
  add(size: number): void {
    this.used += size;
  }
}

function truncateNotice(path: string, cap: number): string {
  return `\n[Trellis: truncated at ${cap} bytes — read ${path} for the full content]`;
}
function isBinaryContent(data: Buffer): boolean {
  return data.includes(0) || !isUtf8(data);
}
function binaryNotice(path: string, size: number, reason: string): string {
  return `[Trellis: not inlined (binary file) — ${path} (${size} bytes): ${reason}]`;
}
function indexNotice(path: string, size: number, reason: string): string {
  return `[Trellis: not inlined (total context limit reached) — ${path} (${size} bytes): ${reason}]`;
}
function budgetedBlock(
  budget: ContextBudget,
  header: string,
  plainPath: string,
  content: string,
  reason: string,
  sizeForIndex: number,
): string {
  const block = `=== ${header} ===\n${content}`;
  const blockBytes = Buffer.byteLength(block, "utf-8");
  if (!budget.hasRoom(blockBytes)) {
    const notice = indexNotice(plainPath, sizeForIndex, reason);
    budget.add(Buffer.byteLength(notice, "utf-8"));
    return notice;
  }
  budget.add(blockBytes);
  return block;
}
function readFileBytes(basePath: string, filePath: string): Buffer | null {
  const full = join(basePath, filePath);
  try {
    if (!statSync(full).isFile()) return null;
  } catch {
    return null;
  }
  try {
    return readFileSync(full);
  } catch {
    return null;
  }
}
function materializeFile(
  basePath: string,
  filePath: string,
  reason: string,
  limits: ContextInjectionLimits,
  budget: ContextBudget,
): string | null {
  const data = readFileBytes(basePath, filePath);
  if (data === null) return null;
  const size = data.length;
  if (isBinaryContent(data)) {
    const notice = binaryNotice(filePath, size, reason);
    budget.add(Buffer.byteLength(notice, "utf-8"));
    return notice;
  }
  const cap = limits.max_file_bytes;
  const truncated = truncateUtf8(data, cap);
  let content = truncated.toString("utf-8");
  if (truncated.length < size) content += truncateNotice(filePath, cap);
  return budgetedBlock(budget, filePath, filePath, content, reason, size);
}
function materializeArtifact(
  basePath: string,
  filePath: string,
  headerLabel: string,
  reason: string,
  limits: ContextInjectionLimits,
  budget: ContextBudget,
): string | null {
  const data = readFileBytes(basePath, filePath);
  if (data === null) return null;
  const size = data.length;
  const cap = limits.max_artifact_bytes;
  const truncated = truncateUtf8(data, cap);
  let content = truncated.toString("utf-8");
  if (truncated.length < size) content += truncateNotice(filePath, cap);
  return budgetedBlock(budget, headerLabel, filePath, content, reason, size);
}
interface JsonlEntry {
  file: string;
  type: string;
  reason: string;
}
function readJsonlEntries(basePath: string, jsonlPath: string): JsonlEntry[] {
  const text = readText(join(basePath, jsonlPath));
  if (!text) return [];
  const entries: JsonlEntry[] = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t) continue;
    try {
      const item = JSON.parse(t) as JsonObject;
      const filePath =
        (typeof item.file === "string" && item.file) ||
        (typeof item.path === "string" && item.path) ||
        "";
      if (!filePath) continue;
      entries.push({
        file: filePath,
        type: typeof item.type === "string" ? item.type : "file",
        reason: (typeof item.reason === "string" && item.reason) || "-",
      });
    } catch {}
  }
  return entries;
}

// ── Trellis Context ────────────────────────────────────────────────────
function findRoot(start: string): string {
  let c = resolve(start);
  while (true) {
    // Only a directory with `.trellis/` is a Trellis project root. A bare
    // `.pi` can be pi's global config (`~/.pi`) or an unrelated project, so
    // accepting it here made root resolution stop too early (e.g. on `~`).
    // Also reject a regular file named `.trellis` — the marker must be a
    // directory.
    const marker = join(c, ".trellis");
    if (existsSync(marker) && statSync(marker).isDirectory()) return c;
    const p = dirname(c);
    if (p === c) return resolve(start);
    c = p;
  }
}
// Resolve the project root from the session working directory when available
// (pi's ExtensionContext.cwd), falling back to the pi host process cwd.
// process.cwd() is the host's launch directory and can differ from the
// session cwd (pi-web / RPC / multi-project hosts), which made Trellis context
// files resolve against the wrong project.
function resolveRoot(ctx?: PiExtensionContext): string {
  return findRoot(ctx?.cwd ?? process.cwd());
}
// Cache key scoping per-session state by both the session key and the
// resolved project root. With dynamic root resolution a session can observe
// different ctx.cwd values over its lifetime (pi-web / RPC / project
// switching); keying caches by the session key alone would leak one
// project's startup/task context into another.
function cacheKey(k: string | null, ctx?: PiExtensionContext): string {
  return `${k ?? "default"}::${resolveRoot(ctx)}`;
}
function contextKey(input?: unknown, ctx?: PiExtensionContext): string | null {
  const sessionId =
    callStr(ctx?.sessionManager?.getSessionId, ctx?.sessionManager) ??
    str(process.env.PI_SESSION_ID) ??
    str(process.env.PI_SESSIONID) ??
    lookupStr(input, ["session_id", "sessionId", "sessionID"]);
  if (sessionId) {
    const normalized = sessionId.replace(/[^A-Za-z0-9._-]+/g, "_");
    if (!normalized) return `pi_${hash(sessionId)}`;
    return `pi_${normalized}${normalized === sessionId ? "" : `_${hash(sessionId)}`}`;
  }
  const transcriptPath =
    callStr(ctx?.sessionManager?.getSessionFile, ctx?.sessionManager) ??
    lookupStr(input, ["transcript_path", "transcriptPath", "transcript"]);
  if (transcriptPath) return `pi_transcript_${hash(transcriptPath)}`;
  return null;
}

/**
 * Return `candidate` when it lands inside `root`, else null.
 *
 * A session pointer is not always something the user typed. `task.py` now
 * refuses to store a ref that leaves the project, but a session file written
 * before that fix can still hold one, and `trellis update` does not rewrite
 * session files — so a poisoned pointer outlives the upgrade that closed the
 * writer. Both sides are resolved so a task directory symlinked outside is
 * refused too, but the original `candidate` is returned on success: callers do
 * `relative(root, dir)`, and handing them a realpath would break that whenever
 * `root` itself sits behind a symlink (`/tmp` does on macOS).
 */
function containInRoot(root: string, candidate: string): string | null {
  try {
    const rel = relative(realpathSync(root), realpathSync(candidate));
    if (rel !== "" && (rel.startsWith("..") || isAbsolute(rel))) return null;
    return candidate;
  } catch {
    return null;
  }
}

function readTaskDir(root: string, key: string | null): string | null {
  if (!key) return null;
  try {
    const ctx = JSON.parse(
      readText(join(root, ".trellis", ".runtime", "sessions", `${key}.json`)),
    ) as JsonObject;
    let ref = str(ctx.current_task);
    if (!ref) return null;
    ref = ref;
    ref = ref.replace(/\\/g, "/").replace(/^\.\//, "");
    if (ref.startsWith("tasks/")) ref = `.trellis/${ref}`;
    const candidate = ref.startsWith(".trellis/")
      ? join(root, ref)
      : isAbsolute(ref)
        ? ref
        : join(root, ".trellis", "tasks", ref);
    return containInRoot(root, candidate);
  } catch {
    return null;
  }
}

// ── Workflow State Breadcrumb ─────────────────────────────────────────
const WF_RE =
  /\[workflow-state:([A-Za-z0-9_-]+)\]\s*\n([\s\S]*?)\n\s*\[\/workflow-state:\1\]/g;
function workflowBreadcrumb(root: string, key: string | null): string {
  const wf = readText(join(root, ".trellis", "workflow.md"));
  if (!wf) return "";
  const templates: Record<string, string> = {};
  for (const m of wf.matchAll(WF_RE)) {
    const s = m[1] ?? "",
      b = (m[2] ?? "").trim();
    if (s && b) templates[s] = b;
  }
  const dir = readTaskDir(root, key);
  let header = "Status: no_task",
    lookup = "no_task";
  if (dir) {
    try {
      const d = JSON.parse(readText(join(dir, "task.json"))) as JsonObject;
      const status = str(d.status) ?? "";
      const id = str(d.id) ?? dir.split(/[\\/]/).pop() ?? "";
      if (status) {
        header = `Task: ${id} (${status})`;
        lookup = status;
      }
    } catch {}
  }
  const body = templates[lookup] ?? "Refer to workflow.md for current step.";
  return `<workflow-state>\n${header}\n${body}\n</workflow-state>`;
}

// ── Session Overview ───────────────────────────────────────────────────
function runContextScript(root: string, key: string | null, args: string[]): string {
  const script = join(root, ".trellis", "scripts", "get_context.py");
  if (!exists(script)) return "";
  try {
    const py = process.platform === "win32" ? "python" : "python3";
    const result = spawnSync(py, [script, ...args], {
      cwd: root,
      env: key ? { ...process.env, TRELLIS_CONTEXT_ID: key } : process.env,
      encoding: "utf-8",
      timeout: SESSION_OVERVIEW_TIMEOUT_MS,
      windowsHide: true,
    });
    if (result.status !== 0) return "";
    const stdout = (result.stdout ?? "").trim();
    return stdout;
  } catch {
    return "";
  }
}

function sessionOverview(root: string, key: string | null): string {
  const stdout = runContextScript(root, key, []);
  return stdout ? `<session-overview>\n${stdout}\n</session-overview>` : "";
}

function workflowOverview(root: string, key: string | null): string {
  const stdout = runContextScript(root, key, [
    "--mode",
    "phase",
    "--platform",
    "pi",
  ]);
  return stdout ? `<trellis-workflow>\n${stdout}\n</trellis-workflow>` : "";
}

function buildStartupContext(
  root: string,
  key: string | null,
  overview: string,
): string {
  const workflow = workflowOverview(root, key);
  return [
    "<session-context>\nTrellis compact SessionStart context. Use it to orient the session; load details on demand.\n</session-context>",
    FIRST_REPLY_NOTICE,
    overview,
    workflow,
    "<ready>\nUse the current workflow state to decide whether to create, continue, or skip a Trellis task.\n</ready>",
  ]
    .filter(Boolean)
    .join("\n\n");
}

function buildContext(root: string, agent: string, key: string | null): string {
  const dir = readTaskDir(root, key);
  if (!dir)
    return "No active Trellis task found. Read .trellis/ before proceeding.";
  const relTaskDir = relative(root, dir).replace(/\\/g, "/");
  const limits = readContextInjectionLimits(root);
  const budget = new ContextBudget(limits.max_total_bytes);

  // 1. Curated spec/research files from {agent}.jsonl (same order, budget
  //    processed first, matching Python's get_agent_context()).
  const jsonlName = TRELLIS_AGENT_JSONL[agent] ?? "";
  const specBlocks: string[] = [];
  if (jsonlName) {
    for (const entry of readJsonlEntries(dir, jsonlName)) {
      if (entry.type === "directory") continue;
      const block = materializeFile(root, entry.file, entry.reason, limits, budget);
      if (block) specBlocks.push(block);
    }
  }
  const spec = specBlocks.join("\n\n");

  // 2-4. Task artifacts, in order: prd.md -> design.md -> implement.md.
  const prd = materializeArtifact(
    root,
    `${relTaskDir}/prd.md`,
    `${relTaskDir}/prd.md (Requirements)`,
    "Requirements document",
    limits,
    budget,
  );
  const design = materializeArtifact(
    root,
    `${relTaskDir}/design.md`,
    `${relTaskDir}/design.md (Technical Design)`,
    "Technical design document",
    limits,
    budget,
  );
  const impl = materializeArtifact(
    root,
    `${relTaskDir}/implement.md`,
    `${relTaskDir}/implement.md (Execution Plan)`,
    "Execution plan document",
    limits,
    budget,
  );

  // prd/design/impl already carry their own "=== path (label) ===" header
  // (from materializeArtifact) — no extra "### x.md" wrapper needed, that
  // would just double the header.
  return [
    `## Trellis Task Context`,
    `Task directory: ${dir}`,
    "",
    prd ?? `(missing) ${relTaskDir}/prd.md`,
    design ? "\n" + design : "",
    impl ? "\n" + impl : "",
    spec ? "\n### Curated Spec / Research Context\n" + spec : "",
  ].join("\n");
}

// ── Extension ──────────────────────────────────────────────────────────
export default function trellisExtension(pi: {
  on?: (
    event: string,
    handler: (event: unknown, ctx?: PiExtensionContext) => unknown,
  ) => void;
}): void {
  // Process-level fallback; call sites with a session context re-resolve via
  // resolveRoot(ctx) so the active project (session cwd) is used instead.
  const root = resolveRoot();
  const procKey = `pi_process_${hash([root, process.pid, Date.now(), randomBytes(8).toString("hex")].join(":"))}`;
  let curKey: string | null = null;

  const getKey = (input?: unknown, ctx?: PiExtensionContext) => {
    const k = contextKey(input, ctx) ?? curKey ?? procKey;
    curKey = k;
    return k;
  };

  // Per-turn cache avoids repeated context-script calls.
  let turnCache: {
    key: string;
    ts: number;
    wf: string;
    ov: string;
  } | null = null;
  const getTurnCtx = (k: string | null, ctx?: PiExtensionContext) => {
    const now = Date.now();
    const ck = cacheKey(k, ctx);
    if (turnCache && turnCache.key === ck && now - turnCache.ts < 1500)
      return turnCache;
    const r = resolveRoot(ctx);
    turnCache = {
      key: ck,
      ts: now,
      wf: workflowBreadcrumb(r, k),
      ov: sessionOverview(r, k),
    };
    return turnCache;
  };
  // Provider prefix caches invalidate from byte 0 whenever the system prompt
  // changes, so everything injected into systemPrompt is memoized per context
  // key and stays byte-identical for the life of the process. Volatile state
  // travels through persisted custom messages instead (append-only history).
  const startupCtxCache = new Map<string, string>();
  const getStartupCtx = (
    k: string | null,
    turn: { ov: string },
    ctx?: PiExtensionContext,
  ): string => {
    const key = cacheKey(k, ctx);
    let startup = startupCtxCache.get(key);
    if (startup === undefined) {
      startup = buildStartupContext(resolveRoot(ctx), k, turn.ov);
      startupCtxCache.set(key, startup);
    }
    return startup;
  };
  const taskCtxSnapshot = new Map<string, string>();
  const lastSentTaskCtx = new Map<string, string>();
  const lastSentRuntimeCtx = new Map<string, string>();
  // Session-level "most recently persisted" project root. The root-scoped
  // lastSent* maps suppress re-emission for an unchanged root, but when a
  // session switches projects (A -> B -> A) the latest persisted update
  // would otherwise stay B's — and its <trellis-task-context-update>
  // explicitly supersedes the system-prompt context. Re-assert the current
  // root's task/runtime context on every root transition so the agent never
  // keeps following the previous project's instructions.
  const lastPersistedRoot = new Map<string, string>();

  // Events
  pi.on?.("session_start", (event, ctx) => {
    getKey(event, ctx);
    ctx?.ui?.notify?.(
      "Trellis project context is available. Use /trellis-start to bootstrap or /trellis-continue to resume.",
      "info",
    );
  });
  pi.on?.("tool_call", (event, ctx) => {
    const k = getKey(event, ctx);
    const ev = event as { toolName?: string; input?: JsonObject };
    if (
      ev.toolName === "bash" &&
      isObj(ev.input) &&
      typeof ev.input.command === "string" &&
      !cmdHasTrellisCtx(ev.input.command)
    )
      ev.input.command = `export TRELLIS_CONTEXT_ID=${shellQuote(k)}; ${ev.input.command}`;
  });
  pi.on?.("before_agent_start", (event, ctx) => {
    const k = getKey(event, ctx);
    const key = cacheKey(k, ctx);
    const cur = (event as { systemPrompt?: string }).systemPrompt ?? "";
    const root = resolveRoot(ctx);
    const turn = getTurnCtx(k, ctx);
    const startup = getStartupCtx(k, turn, ctx);
    // Task context is snapshotted into systemPrompt once; later on-disk
    // changes are delivered as persisted messages so the prefix stays stable.
    const freshTaskCtx = buildContext(root, "trellis-implement", k);
    let taskCtx = taskCtxSnapshot.get(key);
    if (taskCtx === undefined) {
      taskCtx = freshTaskCtx;
      taskCtxSnapshot.set(key, taskCtx);
      lastSentTaskCtx.set(key, freshTaskCtx);
    }
    const updates: string[] = [];
    const runtimeContext = [turn.wf, turn.ov].filter(Boolean).join("\n\n");
    // Re-assert the current root's context on project switches: when the
    // session returns to an unchanged root, the root-scoped lastSent* maps
    // alone would leave the previous project's persisted update as the most
    // recent one in history.
    const prevRoot = lastPersistedRoot.get(k ?? "default");
    const switchedRoot = prevRoot !== undefined && prevRoot !== root;
    if (
      runtimeContext &&
      (runtimeContext !== lastSentRuntimeCtx.get(key) || switchedRoot)
    ) {
      lastSentRuntimeCtx.set(key, runtimeContext);
      updates.push(runtimeContext);
    }
    if (
      freshTaskCtx !== lastSentTaskCtx.get(key) ||
      switchedRoot
    ) {
      lastSentTaskCtx.set(key, freshTaskCtx);
      updates.push(
        "<trellis-task-context-update>\nTask context changed on disk. This supersedes the Trellis Task Context in the system prompt.\n\n" +
          freshTaskCtx +
          "\n</trellis-task-context-update>",
      );
    }
    if (updates.length > 0) lastPersistedRoot.set(k ?? "default", root);
    const content = updates.join("\n\n");
    return {
      message: content
        ? {
            customType: "trellis-runtime-context",
            content,
            display: false,
          }
        : undefined,
      systemPrompt: [cur, startup, taskCtx].filter(Boolean).join("\n\n"),
    };
  });
  pi.on?.("context", (event, ctx) => {
    getKey(event, ctx);
  });
}
