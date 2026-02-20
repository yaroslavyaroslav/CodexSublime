# Session Notes (Migration to app-server + Stabilization)

Detailed notes from the migration/debug session. Keep this as operational memory;
keep `AGENTS.md` short and stable.

## Intent of the Session

- Migrate plugin backend from old Codex protocol flow to `codex app-server`.
- Keep frontend behavior and existing feature set.
- Fix regressions around approvals, output rendering, and platform behavior.

## Major Changes Applied

### 1) Bridge migrated to app-server

- Reworked `plugin/codex_bridge.py` to:
  - spawn `codex app-server`
  - run JSON-RPC bootstrap
  - resume/create conversation and attach listener
  - send user messages via `sendUserMessage`
  - handle legacy + v2 approval request methods

### 2) Approval fixes

- Fixed v2 approval keying (`itemId:approvalId`) to avoid collisions.
- Added response path for both legacy and v2 approval protocols.
- Suppressed duplicate approval notifications from `codex/event/*` path and
  used server-request path as source of truth (required for valid request IDs).
- Added explicit transcript entries showing local approval choice:
  - `exec_approval_result`
  - `apply_patch_approval_result`

### 3) Transcript noise cleanup

- Suppressed infra/delta noise such as:
  - `mcp_startup_update`, `mcp_startup_complete`
  - `item_started`, `item_completed`
  - `agent_message_content_delta`, `agent_message_delta`
  - `reasoning_content_delta`
  - `user_message` (plugin already echoes user prompt)
- Updated defaults in `Codex.sublime-settings` and rendering fallback logic.

### 4) Callback lifecycle bug and fix

- Regression observed: conversation stalled after assistant preface message when
  approval came later in same turn.
- Root cause: callback cleanup happened on `agent_message` too early.
- Fix:
  - do not clear active callback on `agent_message`
  - clear stale active callback when a new `user_input` starts (prevents leaks)

### 5) Windows hardening

- `start_new_session` is now set only on non-Windows.
- `writable_roots` override now serialized safely (JSON), preventing Windows
  path escaping issues in `--config`.

### 6) Docs and release metadata

- README updated to reflect:
  - app-server architecture (not proto)
  - auth via Codex session (`codex login`) as primary path
  - actual current command palette entries
  - current release mapping: plugin `1.104.0` <-> codex-cli `0.104.0`
- Added release note `messages/1.104.0.md`.
- Registered in `messages.json`.

## Important Pitfalls Encountered

### A) Duplicate package copies in Sublime

Symptom:
- edits appear ignored; no expected debug logs; behavior seems "old".

Root cause:
- both symlinked package and installed archive copy existed.

Check list:
- Ensure symlink exists:
  - `~/Library/Application Support/Sublime Text/Packages/Codex -> repo`
- Remove installed duplicates:
  - `~/Library/Application Support/Sublime Text/Installed Packages/Codex*`

### B) Silent submit failure

Symptom:
- sending prompt appears to do nothing.

Root cause:
- exception path in submit used `session_id` before assignment.

Fix:
- initialize `session_id` before bridge creation try/except in submit command.

### C) Misleading "approval not working"

Symptom:
- duplicate approval blocks or missing approval reaction.

Root cause:
- approval surfaced from both notification and server-request channels.
- only server-request path carries response correlation id.

Fix:
- ignore approval notifications, process only request channel.

### D) Command not firing confusion

Symptom:
- empty output; no bridge logs.

Root cause:
- user was testing another neighbor package instance by mistake.

Resolution:
- confirm active package path and command traces before deeper backend debugging.

## Known Behavioral Notes

- `Codex: New Message` opens input panel.
- Send is bound to `Super/Ctrl + Enter` in input panel context.
- `Codex: Send Message` was added temporarily for debugging and later removed.

## Suggested Debug Procedure (if something breaks again)

1. Verify loaded package path and duplicates first.
2. Confirm command path is hit (submit entry log).
3. Confirm bridge process spawn and bootstrap sequence.
4. Check approval request path:
   - server request received
   - pending map populated
   - response sent
5. Confirm transcript suppression doesn’t hide required user-facing events.

