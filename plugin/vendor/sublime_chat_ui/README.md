# sublime-chat-ui

Shared, source-only UI primitives for Yaroslav's Sublime Text chat plugins.

This repository is not a Package Control dependency. Its contents are vendored
into each host plugin, so every released `.sublime-package` remains standalone.

## Contract

- The shared package owns panel presentation, prompt history, generic content
  operations, Markdown formatting helpers, typed section geometry and folding,
  and `ChatMarkdown.sublime-syntax`.
- Host plugins own concrete `TextCommand`, `WindowCommand`, and `EventListener`
  subclasses, command names, backend integrations, and event-to-section adapters.
- Vendored copies are read-only. Make changes here, tag them, then update each
  host with `git subtree pull --squash`.

## Verification

```bash
uv run python3 -m unittest discover -s tests -v
```
