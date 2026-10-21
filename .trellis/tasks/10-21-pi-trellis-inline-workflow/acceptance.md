# Acceptance Record

## Result

Implemented the project Pi-only inline workflow and removed the project-local `.opencode/` integration directory, including its agents, plugins, commands, and skills. The shared Trellis CLI retains its generic OpenCode adapter. Pi handles task implementation and checks in the main session. Existing Trellis task/session context injection, workflow state updates, project root scoping, and bash `TRELLIS_CONTEXT_ID` injection remain in the extension.

The shared workflow routes Pi through inline sections while retaining sub-agent routes for other platforms. The project `.opencode/` directory is absent. `.trellis/.template-hashes.json` was left unchanged per the Trellis generated-file rules; its OpenCode entries remain for Trellis update to reconcile. Shared CLI OpenCode support remains. Pi Implement/Check guidance no longer assigns DeepSeek/Qwen groups. The project default model and extension loading in `.pi/settings.json` were left unchanged. No `trellis init` command was run.

V1-02 business and task files were not edited by this task. They remain concurrent dirty work in the shared tree and were not staged, committed, archived, or otherwise modified.

## Review performed

- Read and reviewed the complete Pi extension after removing dispatch-only code.
- Searched the active Pi integration and workflow files for `trellis_subagent`, child `spawn` paths, progress card symbols, and the former role model assignments. No active Pi dispatch references remain. The extension retains `spawnSync` only for the existing bounded call to `.trellis/scripts/get_context.py`.
- Confirmed the `.opencode/` directory is removed. The `.trellis/.template-hashes.json` file remains unchanged, in line with Trellis generated-file guidance; shared CLI adapter files were left intact.
- Reviewed the working-tree status and task diff scope; concurrent V1-02 paths remain untouched by this task.
- Confirmed by source inspection that session/task/workflow context injection and bash context ID injection remain present.

## Validation not run

No runtime, syntax, compiler, lint, format, or test commands were run. The repository permits those checks only in the isolated task directory on `100.120.14.84`; this change did not require such a check for the requested source and documentation review. Pi was not reloaded, so runtime registration behavior remains unverified in a live session.

No `.trellis/spec/` update was needed: this change records project workflow and Pi integration behavior in the existing canonical `.trellis/workflow.md` and `.trellis/model-routing.md` files; it introduces no reusable code-layer convention. The old OpenCode template hash entries remain intentionally for Trellis update to reconcile; they were not edited manually.

## Finalization

No files were staged and no commit, push, or task archive was performed.
