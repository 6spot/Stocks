# Trellis Implementation Guidance

Pi handles implementation in the main session. Use this file only when implementation guidance is explicitly requested; the project workflow does not dispatch it as a child agent.

## Before editing

1. Resolve the current task using the injected Trellis context or `python3 ./.trellis/scripts/task.py current --source`.
2. Read the task `prd.md`, `design.md` and `implement.md` when present, plus its research notes.
3. Load `trellis-before-dev` and read the relevant project specs.
4. Inspect the existing implementation and tests before changing code.

## Implementation

- Follow the task acceptance criteria and project instructions.
- Keep changes within the authorized scope and preserve concurrent work.
- Run validation only where the project permits it; report checks that could not be run.
- Do not commit or push unless the user explicitly authorizes it.
