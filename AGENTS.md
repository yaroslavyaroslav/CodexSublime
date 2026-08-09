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
- Never use the Ctrl-backtick shortcut to open the Sublime Text console; Ghostty
  intercepts it as a global shortcut. Use the Sublime menu or command API.
- Approval flow must be handled from server requests, not notification clones.
- Keep transcript user-facing; suppress noisy infra/delta events by default.
- Debug trace logs must respect `log_level = "debug"`.

## Vendored Subtree Workflow

- Treat every directory below `plugin/vendor/` as read-only in this repository.
  Its standalone source repository is the only place where vendored code may be
  authored and committed.
- Current subtree ownership:
  - `plugin/vendor/sublime_chat_ui` comes from the sibling
    `../sublime-chat-ui` repository.
  - `plugin/vendor/sublime_agent_tools` comes from the sibling
    `../sublime-agent-tools` repository.
- Never directly edit, stage, or commit files below either vendor prefix in
  CodexSublime. A byte-identical manual copy is still the wrong workflow.
- Never create a mixed commit containing both CodexSublime files and vendored
  subtree files. Keep host integration changes outside `plugin/vendor/` in
  separate CodexSublime commits.
- For a change spanning a subtree and this host repository, use this order:
  1. Make and verify the commits in the standalone source repository, in their
     intended chronological order.
  2. Commit CodexSublime-only integration changes separately.
  3. From a clean CodexSublime worktree, import the committed source ref with
     `git subtree pull --prefix=<prefix> <source-repo> <source-ref> --squash`.
- The current local sync forms are:
  - `git subtree pull --prefix=plugin/vendor/sublime_chat_ui ../sublime-chat-ui feat/system-markdown-wrapper --squash`
  - `git subtree pull --prefix=plugin/vendor/sublime_agent_tools ../sublime-agent-tools master --squash`
  Confirm the intended source ref before running either command; do not guess
  if the source repository has moved to another integration branch.
- Do not cherry-pick standalone source commits directly onto CodexSublime and
  do not run `git add` on a vendor prefix. Only a real `git subtree` operation
  should create CodexSublime commits that touch vendored paths. The resulting
  squash and merge commits are expected host history.
- Before committing or pushing CodexSublime changes, verify that no ordinary
  first-parent commit directly changes a vendored path:
  - `git diff --cached --name-only -- plugin/vendor` must be empty unless a
    subtree operation is actively being completed.
  - `git log --first-parent --no-merges <base>..HEAD -- plugin/vendor` must be
    empty.
  - For each updated subtree, compare `git rev-parse HEAD:<prefix>` with
    `git -C <source-repo> rev-parse '<source-ref>^{tree}'`.
- If a local direct or mixed vendor commit is discovered before push, rewrite
  it instead of adding another corrective vendor commit: move its vendor
  changes into the standalone source repository in their original order,
  preserve only the host-file changes as host commits, then rebuild the vendor
  update with `git subtree pull --squash` and confirm the final tree is
  unchanged.

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
