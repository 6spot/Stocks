---
name: trellis-continue
description: "Resume work on the current task. Loads the workflow Phase Index, figures out which phase/step to pick up at, then pulls the step-level detail via get_context.py --mode phase. Use when coming back to an in-progress task and you need to know what to do next."
---

# Continue Current Task

Resume work on the current task — pick up at the right phase/step in `.trellis/workflow.md`.

---

## Step 1: Load Current Context

```bash
python3 ./.trellis/scripts/get_context.py
```

Confirms: current task, git state, recent commits.

## Step 2: Load the Phase Index

```bash
python3 ./.trellis/scripts/get_context.py --mode phase
```

Shows the Phase Index (Plan / Execute / Finish) with routing + skill mapping.

## Step 3: Decide Where You Are

`get_context.py` shows the active task's `status` field. Route by `status` + artifact presence, while preserving execution and follow-up actions already authorized by the user. A generic continue command alone does not grant new permissions, but it must not erase an explicit instruction to execute this or the uniquely resolved next task.

- No active pointer + user names an existing task or authorizes the unique next task → resolve that task and use the routes below; do not ask to create an existing task or renew the same execution approval.
- `status=planning` + no `prd.md` → **1.1** (load `trellis-brainstorm`)
- `status=planning` + `prd.md` only → decide whether the task is lightweight or complex. Lightweight can move to **1.4** review; complex returns to **1.1** to add `design.md` + `implement.md`.
- `status=planning` + complex artifacts complete + dispatch-platform jsonl not curated (empty, or only a legacy `_example` placeholder row) → **1.3**; inline platforms skip this step
- `status=planning` + required artifacts complete + required jsonl curated for dispatch platforms or inline mode → **1.4** (review artifacts, reuse existing explicit execution authorization, then `task.py start`; ask only if authorization or a user-owned decision is missing)
- `status=in_progress` + implementation not started → **2.1**
- `status=in_progress` + implementation done, not yet checked → **2.2**
- `status=in_progress` + check passed → **3.3** (spec update) → **3.4** (commit)
- `status=completed` (rare; usually archived immediately) → archive flow

Phase rules (full detail in `.trellis/workflow.md`):

1. Run steps **in order** within a phase — `[required]` steps must not be skipped
2. `[once]` steps are already done if the required output exists. `prd.md` alone can be enough only for lightweight tasks; complex tasks also need `design.md` and `implement.md`.
3. You may go back to an earlier phase if discoveries require it

## Step 4: Load the Specific Step

Once you know which step to resume at:

Use the actual host platform (`pi` in Pi Coding Agent, `codex` in Codex), independent of the selected model or provider. Replace `<platform>` accordingly:

```bash
python3 ./.trellis/scripts/get_context.py --mode phase --step <X.X> --platform <platform>
```

In Pi, use `--platform pi` and follow the inline workflow: the main session implements and checks the task directly. `task.py start` changes task state and does not change the selected model.

For a request to execute an existing task, the main session reviews task artifacts, preserves explicit authorization already given, then starts that task and implements/checks it directly. Do not ask for approval solely because planning was refined or the previous task was archived. Follow the project testing restrictions. If a prerequisite is unclear, communicate it without beginning implementation. An explicit user request for the main session to perform a particular action remains authoritative.

Follow the loaded instructions. After each `[required]` step completes, move to the next.

---

## Reference

Full workflow and detailed phase steps live in `.trellis/workflow.md`. This command is only an entry point — the canonical guidance is there.
