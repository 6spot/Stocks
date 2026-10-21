# Trellis Check Guidance

Pi checks implementation changes in the main session. Use this file only when check guidance is explicitly requested; the project workflow does not dispatch it as a child agent.

## Review

1. Read the task `prd.md`, `design.md` and `implement.md` when present, plus its research notes and relevant project specs.
2. Review the complete task diff against the acceptance criteria.
3. Check behavior, boundary cases, data flow, error handling and documentation affected by the change.
4. Fix in-scope findings directly, preserving concurrent work.
5. Run only the validation permitted by project instructions and report anything unverified.

Do not commit or push unless the user explicitly authorizes it.
