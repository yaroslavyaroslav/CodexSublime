---
name: sublime-explain-diff
description: Explain a Git branch or commit comparison inside Sublime Text by opening each relevant file as an inline diff and placing concise Markdown phantoms on added or changed current-side lines. Use when the user asks what a branch, diff, PR, or set of changes does and wants the explanation presented in Sublime rather than only in chat.
---

# Sublime Explain Diff

Explain the change in chat and make the same reasoning inspectable at the exact current-side code locations in Sublime Text.

## Workflow

1. Determine the requested Git base. If the user names a branch, tag, or commit, use it. Otherwise infer the repository's default integration branch and state the assumption.
2. Call `sublime.open_diff` once with `base_ref`. Prefer `comparison: "merge_base"` for branch and PR explanations; use `direct` only when the user explicitly wants an exact tree-to-working-tree comparison.
3. Read the returned `opened` entries. Use their `path`, `change_count`, and `annotatable_lines` as the authoritative presentation state.
4. Analyze the actual diff and surrounding code. Group related edits by intent rather than describing every syntactic change.
5. Call `sublime.set_annotations` once with a `files` entry for every opened file that has meaningful explanations. Pass each file's returned `change_count` as `expected_change_count` and use stable, descriptive annotation IDs.
6. Return a concise chat report covering the overall purpose, per-file changes, and any assumptions or uncertainty.
7. Keep the diff views open by default. If the user asks to close or clean them up, call `sublime.close_views` without targets to close only views opened by this runtime.

## Annotation Rules

- Annotate only line numbers listed in `annotatable_lines`. These are current-side added or changed lines.
- Never annotate deleted lines, deleted-only hunks, or deleted files. Do not invent a nearby surviving anchor for deleted code.
- Place one phantom per meaningful change block, usually at the first added or changed line that expresses the block's intent.
- Explain why the code changed and how behavior or data flow differs. Avoid merely restating the source.
- Keep each phantom short: a compact heading or opening sentence followed by at most a small paragraph or a few bullets.
- Use Markdown directly. The Sublime host renders it with `mdpopups` when available and a safe minihtml fallback otherwise.
- The annotation batch is atomic: if any file is stale or invalid, no phantoms are changed. Call `sublime.open_diff` again before retrying the batch. Do not place annotations against an outdated buffer.
- Use `sublime.clear_annotations` when the user asks to remove the explanation or before replacing an unrelated explanation group.
- `sublime.close_views` closes only clean views opened by this tool runtime. Report any dirty views returned in `skipped` instead of discarding user edits.
- For arbitrary tab-management requests, call `sublime.list_views` first. It returns each view's `view_id`, `group`, and tab index. Pass only the selected `view_ids` to `sublime.close_views`; never use Sublime CLI bulk-close commands.

## Failure Handling

- If `open_diff` skips binary or unavailable files, mention them in chat and continue with the text files it opened.
- If a requested path is outside the bound workspace, stop using the tool for that path and report the scope mismatch.
- If no eligible added or changed files exist, report that result and do not create phantoms.
