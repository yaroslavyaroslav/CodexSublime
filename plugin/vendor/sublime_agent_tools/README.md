# sublime-agent-tools

Shared, source-only tools that let AI clients present work inside Sublime Text.
The repository is vendored into host plugins with `git subtree`; it is not a
Package Control dependency.

## Contract

- This package owns the tool catalog, validation, Git reference documents,
  phantom annotations, and reusable skills.
- Host plugins own their model transport, concrete Sublime commands, approval
  UI, transcript semantics, and lifecycle.
- Tools are scoped to the Sublime window and workspace supplied by the host.
- Diff annotations target added or changed current-buffer lines. Deleted-only
  lines are intentionally ignored.

`mdpopups` is used through its public `Phantom` and `PhantomSet` APIs when the
host already provides it. A small built-in minihtml renderer keeps the vendored
package standalone when `mdpopups` is unavailable.

## Verification

```bash
uv run python3 -m unittest discover -s tests -v
```
