# Codex Sublime Text plug-in

Chat with the [Codex CLI](https://www.npmjs.com/package/codex) directly from Sublime Text.
The plug-in spins up a `codex proto` subprocess, shows the conversation in a
Markdown panel and lets you execute three simple commands from the Command
Palette.

---

## Installation

1. **Install the Codex CLI** (the plug-in talks to the CLI, it is **not** bundled).

   ```bash
   npm i -g @openai/codex@native   # or any recent version that supports `proto`
   ```

   By default the plug-in looks for the binary at:

   * macOS (Homebrew): `/opt/homebrew/bin/codex`

   If yours lives somewhere else, set the `codex_path` setting (see below).

2. **Copy the plug-in into Sublime Text** (e.g. clone this repo into
   `Packages/User/CodexSublime/`).  Or package-control-install once it is on the
   registry.

3. **Create an OpenAI token** and tell the plug-in about it.

   *Open the menu* → **Preferences › Package Settings › Codex** and put your
   key into the generated `Codex.sublime-settings` file:

   ```jsonc
   {
       // where the CLI lives (override if different)
       "codex_path": "/opt/homebrew/bin/codex",

       // your OpenAI key – REQUIRED
       "token": "sk-…"
   }
   ```

That’s it – hit <kbd>⌘⇧P</kbd> / <kbd>Ctrl ⇧ P</kbd>, type *Codex*, select one of
the commands and start chatting.

---

## Commands (⌘⇧P)

• **Codex: Prompt** – open a small Markdown panel, type a prompt, hit *Super+Enter*.

• **Codex: Open Transcript** – open the conversation buffer in a normal tab.

• **Codex: Reset Chat** – stop the Codex subprocess, clear the transcript and
  invalidate the stored `session_id` so the next prompt starts a brand-new
  session.

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
            "model":            "o3",
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
        }
    }
}
```

---

## Writable paths passed to Codex

The plug-in constructs the `sandbox_policy.permissions` list for each session:

1. `/private/tmp`
2. **`cwd`** – the first project folder (or the current working directory if
   there is none)
3. **All folders** listed in the Sublime project (visible in the sidebar)
4. Any extra paths you add via `settings.codex.permissions`

Those paths are sent to the CLI unchanged; Codex is free to read/write inside
them depending on the selected `sandbox_mode`.

---

## Default configuration sent to the CLI

The first thing the bridge does is send a `configure_session` message:

```jsonc
{
    "id": "<session_id>",
    "op": {
        "type": "configure_session",

        // model / provider
        "model":            "o3",
        "approval_policy":  "on-failure",
        "provider": {
            "name":     "openai",
            "base_url": "https://api.openai.com/v1",
            "wire_api": "responses",
            "env_key":  "OPENAI_API_KEY"
        },

        // sandbox
        "sandbox_policy": {
            "permissions": [
                "/private/tmp",
                "<cwd>",
                "<each project folder>",
                "<any extra permission>"
            ],
            "mode": "read-only"
        },

        "cwd": "<cwd>"
    }
}
```

All values can be overridden per-project as shown above.

Enjoy hacking with Codex inside Sublime Text!  🚀
