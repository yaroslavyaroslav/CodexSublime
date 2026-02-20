# CodexSublime Agent Notes

This file is a short, stable guide for future agent sessions in this repo.
Keep long incident history in `SESSION_NOTES.md`.

## Scope and Goal

- This plugin integrates Sublime Text with `codex` CLI.
- Current backend is `codex app-server` (not `proto`).
- Do not add features unless explicitly requested; preserve existing UX.

## High-Impact Rules

- Verify which package copy Sublime is actually loading before debugging logic.
  - Preferred setup is symlinked package:
    - `~/Library/Application Support/Sublime Text/Packages/Codex -> <repo>`
  - Remove duplicate installed archives from:
    - `~/Library/Application Support/Sublime Text/Installed Packages/Codex*`
- Keep `main.py` minimal and stable (imports + command registration). Avoid
  temporary debug wrappers unless actively diagnosing load failures.
- Approval flow must be handled from server requests, not notification clones.
- Keep transcript user-facing; suppress noisy infra/delta events by default.
- Debug trace logs must respect `log_level = "debug"`.

## app-server Integration Contract

- Bridge startup uses `codex app-server` with JSON-RPC bootstrap:
  - `initialize` -> `initialized` -> `resumeConversation|newConversation` -> `addConversationListener`.
- User messages are sent via `sendUserMessage`.
- Approval requests can arrive in both legacy and v2 methods:
  - `execCommandApproval`, `applyPatchApproval`
  - `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`
- Map plugin approval decisions to v2 API as:
  - `approved` -> `accept`
  - `approved_for_session` -> `acceptForSession`
  - `denied` -> `decline`
  - `abort` -> `cancel`

## Windows Safety

- Only set `start_new_session` on non-Windows.
- Serialize `sandbox_workspace_write.writable_roots` with JSON-safe encoding
  (avoid raw path quoting issues with backslashes).
- Keep Windows process tree cleanup via `taskkill /T /F`.

## Release/Docs Hygiene

- Keep README aligned with actual command palette entries.
- Current versioning convention for this repo release cycle:
  - plugin `1.x.y` tracks codex-cli `0.x.y`.
- Add release note files under `messages/<version>.md` and register in `messages.json`.

