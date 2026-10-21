# Pi Project Workflow: Main Session and Model Routing

Pi handles Trellis work in the main session. The project extension does not register `trellis_subagent`, start child Pi processes, show subagent progress cards, or provide the `Alt+O` subagent shortcut. Trellis tasks, task lifecycle, project specifications, and Pi session context injection remain enabled.

## Model settings

The project default remains the Magpie OpenAI group in [.pi/settings.json](../.pi/settings.json). Choose another model for the main session with Pi's `/model` command when needed. The project does not assign separate Implement or Check model groups; those roles are guidance for the main session, not child processes.

The project uses the existing Magpie Pi Provider and group configuration. This file does not manage Magpie providers, credentials, accounts, or group membership. Do not change global Pi or Magpie settings as part of project workflow changes.

## Pi workflow

- Use `/trellis-start` to load session context and select the authorized task, or `/trellis-continue` to resume it.
- Review the task artifacts and project specifications, then follow `.trellis/workflow.md`.
- Implement and check directly in the main session. Task state changes do not switch the active model.
- Preserve task and session context injection. The project extension continues to refresh task, workflow, and session context and inject `TRELLIS_CONTEXT_ID` into bash commands.
- Follow repository validation rules. In this project, execution checks are restricted to the isolated task directory on `100.120.14.84`; if access is unavailable, report the relevant checks as unverified and do not run them locally.
- Only commit, push, or archive when authorized by the current task and user instructions.

## Reinitializing Trellis

Do not use `trellis init --force` to remove the former Pi dispatch behavior. Trellis initialization templates can include dispatch functionality, while force initialization can overwrite project workflow and specification files. Keep project tasks and specifications and make targeted changes to project-owned Pi integration instead.

## Other platforms

The repository's project-local `.opencode/` integration has been removed, including its agents, plugins, commands, and skills. The shared Trellis CLI still has generic OpenCode adapter support, but this repository no longer provides OpenCode-specific project files. Other platform integrations continue to use their existing workflows. `.trellis/.template-hashes.json` still contains the prior OpenCode template records; the Trellis generated-file guidance forbids hand-editing that manifest, so stale-record handling is left to `trellis update` and has not been runtime-verified.
