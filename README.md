# Codex Sublime Text plug-in

Chat with the [Codex CLI](https://github.com/openai/codex) directly from Sublime Text.

> [!NOTE]
> Version of this package tracks the Codex CLI version it is tested with (for this release: plugin `1.146.1` with codex-cli `0.146.0`).
> To get one, you can download binary to your system from [codex releases](https://github.com/openai/codex/releases) page and set up this plugin's settings to point to that exact binary.
>
> Plugin `1.146.1` requires Sublime Text build 4205 or newer and runs in its
> Python 3.14 plugin host.

## Upgrade Notes

- Markdown and folding: The transcript and input panels now use a bundled Markdown syntax for improved headings and section folding. You can auto‑fold sections by header via `fold_sections` in project or global Codex settings.
The plug-in spins up a `codex app-server` subprocess, shows the conversation in
a Markdown panel, and integrates approvals/sandboxed execution directly in the
Sublime UI.

---

![](static/codex_title.png)

## Features

- Full Codex capabilities
    - Assistant-to-Bash interaction
    - Sandboxing (on macOS and Linux)
    - Model and provider selection
- MCP support (via `~/.codex/config.toml`)[^1]
- Deep Sublime Text integration
    - Multiline input field uses Markdown
    - Selected text is auto-copied into the message with syntax applied
    - Outputs to either the output panel or a separate tab
    - Symbol list included in answers
- Works out of the box[^2].

### Installation

1. **Download Separate Codex instance** (the plug-in talks to the CLI, it is **not** bundled) from [codex releases](https://github.com/openai/codex/releases) matching this plugin release (for `1.146.1`, use codex-cli `0.146.0`).

Point out the downloaded codex binary from within plugin settings:

```jsonc
{
  "codex_path": ["~/some_path/codex"],
}
```

2. Plugin installation
    1. With Package Control
        `Package Control: Install Package` → **Codex**

    2. Manual
        Clone / download into your `Packages` folder (e.g. `~/Library/Application Support/Sublime Text/Packages/Codex`).


3. Sign in with Codex CLI (`codex login`) if you are not already authenticated.

That’s it – hit <kbd>⌘⇧P</kbd> / <kbd>Ctrl ⇧ P</kbd>, type *Codex*, select one of
the commands and start chatting.

---

## Commands (⌘⇧P)
- **Codex: New Message** – open a small Markdown panel, type a prompt, hit *Super+Enter*.
  At the start of the input, use Up/Down to browse earlier prompts; Escape keeps
  the unfinished draft for the next time the panel opens.
- **Codex: Open Transcript Tab** – open the conversation buffer in a normal tab.
- **Codex: Reset Chat** – stop the Codex subprocess, clear the transcript and invalidate the stored `session_id` so the next prompt starts a brand-new session.

---

## Per-project configuration

Every Sublime project can override Codex settings under the usual `settings`
section.  Example:

```jsonc
{
    "folders": [{ "path": "." }],

    "settings": {
        "codex": {
            // will be filled automatically – delete or set null to reset
            "session_id": null,

            // model & provider options
            // leave empty to use the model selected by your Codex account
            "model":            "",
            "provider_name":    "openai",
            "base_url":         "https://api.openai.com/v1",
            "wire_api":         "responses",
            "approval_policy":  "on-failure",

            // sandbox
            "sandbox_mode": "read-only",
            "permissions": [
                // additional writable paths (project folders are added automatically)
                "/Users/me/tmp-extra"
            ]
            ,
            // Auto-fold specific sections in the transcript by their header
            // name (case-insensitive). You can pass a string or a list.
            // Example: fold the model's internal reasoning block
            // (rendered as "## agent_reasoning").
            "fold_sections": ["agent_reasoning"]
        }
    }
}
```

---

## What is sent to Codex

The plugin launches `codex app-server` with CLI `--config` overrides derived
from global/per-project Sublime settings, including:

- `model`
- `sandbox_mode`
- `approval_policy`
- `sandbox_workspace_write.network_access`
- `sandbox_workspace_write.writable_roots`

If a value is not overridden by the plugin, Codex falls back to its normal
global config (`~/.codex/config.toml`).

For `workspace-write`, project folders and optional `settings.codex.permissions`
are propagated as writable roots.

Enjoy hacking with Codex inside Sublime Text!  🚀

## Code sent to the language model

The plugin only sends the code snippets that you explicitly type or select in the input panel to the language model. It never uploads your entire file, buffer, or project automatically. Local configuration (such as sandbox permissions or project folders) is used only by the CLI to enforce file I/O rules and is not included in the prompt context.

However keep in mind that since this plugin and tool it relays on is agentish, any data from within your sandbox area could be sent to a server.

## Suppressing noisy events

If the Codex backend floods the transcript with incremental updates such as
`agent_reasoning_delta`, add them to the `suppress_events` array in your
project-specific `codex` settings:

```jsonc
{
  "suppress_events": ["agent_reasoning_delta"]
}
```

## Auto-folding sections

You can tell the transcript to auto‑fold certain sections by header name. The
match is case-insensitive and can be configured globally or per-project.

- Global (Preferences ▸ Package Settings ▸ Codex ▸ Settings):

  ```jsonc
  {
    // ... other settings ...
    "fold_sections": ["agent_reasoning"]
  }
  ```

- Per project (`.sublime-project` under `settings.codex`):

  ```jsonc
  {
    "settings": {
      "codex": {
        "fold_sections": ["agent_reasoning"]
      }
    }
  }
  ```

Notes
- Folding is scope-based and targets the Markdown `meta.section` for that
  header. Only the section body is folded, so the header line shows with an
  inline ellipsis (row style), e.g.: `## agent_reasoning ...`.
- The fold is applied right after the section is appended. If your syntax
  definition delays section scopes, the plugin waits briefly to target the
  correct section.

## Development

### Shared chat UI

Panel primitives and the fixed Chat Markdown syntax are vendored from the
public [`sublime-chat-ui`](https://github.com/yaroslavyaroslav/sublime-chat-ui)
source repository. Edit that repository, then update this read-only subtree
with:

```bash
./scripts/update_sublime_chat_ui.sh <ref>
```

Set `SUBLIME_CHAT_UI_REPO` to a local clone path to pull unpushed development
branches.

[^1]: https://github.com/openai/codex/blob/main/codex-rs/config.md#mcp_servers
[^2]: If `codex` is installed and authenticated (for example via `codex login`).
