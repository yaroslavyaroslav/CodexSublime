---
name: sublime-explain-diff
description: Explain the purpose, architecture, and end-to-end behavior of a Git branch, commit comparison, or PR as a coherent narrative, while opening relevant files as Sublime Text inline diffs and adding selective deep-dive Markdown annotations at important implementation points. Use when the user asks what a branch, diff, PR, or set of changes does and wants the explanation connected to concrete code in Sublime rather than only in chat.
---

# Sublime Explain Diff

Explain the change as one coherent story in chat, then make the most important implementation details inspectable at exact current-side code locations in Sublime Text.

## Workflow

1. Determine the requested Git base. If the user names a branch, tag, or commit, use it. Otherwise infer the repository's default integration branch and state the assumption.
2. Call `sublime.open_diff` once with `base_ref`. Prefer `comparison: "merge_base"` for branch and PR explanations; use `direct` only when the user explicitly wants an exact tree-to-working-tree comparison.
3. Read the returned `opened` entries. Use their `path`, `change_count`, and `annotatable_lines` as the authoritative presentation state.
4. Analyze the actual diff and enough surrounding code to reconstruct the PR's intent and runtime behavior. Group related edits by capability, data flow, boundary, or architectural decision rather than by file.
5. Draft the chat explanation as a narrative: start with the problem or previous limitation, explain the new end-to-end behavior, then cover the important boundaries, contracts, tradeoffs, safety properties, and validation. Add Markdown links to the exact files and lines that substantiate each part of the story. Do not default to a file inventory.
6. Select only the implementation points where an editor-side explanation adds real depth. Call `sublime.set_annotations` once with `files` entries only for files that contain those high-value anchors. Pass each file's returned `change_count` as `expected_change_count` and use stable, descriptive annotation IDs.
7. Return the narrative report and briefly state how many deep-dive annotations were placed. Mention assumptions or uncertainty where they affect the explanation.
8. Keep the diff views open by default. If the user asks to close or clean them up, call `sublime.close_views` without targets to close only views opened by this runtime.

## Narrative Shape

- Optimize for understanding the PR as a system, not for proving that every file was inspected.
- Explain what existed before, what capability the PR adds or changes, and why the chosen design matters.
- Trace the important runtime or data flow across components when the change spans orchestration, adapters, storage, deployment, or tests.
- Use precise Markdown file links throughout the prose. Link the statement to the code that supports it instead of collecting all links in a separate per-file appendix.
- Discuss files individually only when the user explicitly asks for a file-by-file inventory or when one file has a distinct responsibility that does not fit the larger flow.
- Avoid repeating the same explanation in chat and in a phantom. Chat supplies the map; annotations supply local depth.

## Annotation Rules

- Annotate only line numbers listed in `annotatable_lines`. These are current-side added or changed lines.
- Never annotate deleted lines, deleted-only hunks, or deleted files. Do not invent a nearby surviving anchor for deleted code.
- Do not annotate every file, hunk, or changed symbol. Prefer a small set of high-value annotations for architectural decisions, non-obvious contracts, critical data flow, safety boundaries, or implementation mechanics that deserve a closer look.
- Place a phantom at the first eligible added or changed line that best represents the idea. The explanation may cover surrounding unchanged code or a broader architectural decision; the anchor must still be one of the returned `annotatable_lines`.
- Make each phantom a genuine drill-down: use a descriptive opening sentence and enough detail to explain why the design exists, how it works, and what invariant or tradeoff it establishes. A short paragraph or a few focused bullets is appropriate; avoid label-like one-liners.
- Prefer one annotation that connects several nearby edits over multiple repetitive annotations.
- Explain why the code changed and how behavior or data flow differs. Avoid merely restating the source or duplicating the chat narrative.
- Use Markdown directly. The Sublime host renders it with `mdpopups` when available and a safe minihtml fallback otherwise.
- The annotation batch is atomic: if any file is stale or invalid, no phantoms are changed. Call `sublime.open_diff` again before retrying the batch. Do not place annotations against an outdated buffer.
- Use `sublime.clear_annotations` when the user asks to remove the explanation or before replacing an unrelated explanation group.
- On a follow-up asking to go deeper, refresh the diff state and replace or extend the existing explanation with a new atomic batch. Preserve the narrative structure and add depth only where the follow-up warrants it.
- `sublime.close_views` closes only clean views opened by this tool runtime. Report any dirty views returned in `skipped` instead of discarding user edits.
- For arbitrary tab-management requests, call `sublime.list_views` first. It returns each view's `view_id`, `group`, and tab index. Pass only the selected `view_ids` to `sublime.close_views`; never use Sublime CLI bulk-close commands.

## Failure Handling

- If `open_diff` skips binary or unavailable files, mention them in chat and continue with the text files it opened.
- If a requested path is outside the bound workspace, stop using the tool for that path and report the scope mismatch.
- If no eligible added or changed files exist, report that result and do not create phantoms.
