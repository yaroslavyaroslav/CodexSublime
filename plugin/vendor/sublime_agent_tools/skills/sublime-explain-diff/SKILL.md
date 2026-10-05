---
name: sublime-explain-diff
description: Explain the purpose, architecture, and behavior of a Git branch, commit comparison, or PR inside Sublime Text, connecting a chat explanation to concrete inline diffs and Markdown annotations. Adapt the structure, depth, audience, and annotation density to the user's request; when unspecified, default to a coherent PR-level narrative with selective deep dives. Use when the user asks what a branch, diff, PR, or set of changes does and wants the explanation connected to concrete code in Sublime rather than only in chat.
---

# Sublime Explain Diff

Explain the change in the form most useful to the user and connect the reasoning to exact current-side code locations in Sublime Text. Default to a coherent PR-level story with selective deep dives when the user does not request another presentation.

## Presentation Freedom

- Treat the narrative shape and selective annotation density below as defaults, not a fixed output template.
- Follow the user's requested focus, organization, depth, audience, and annotation density, even when it replaces the default narrative or omits either chat detail or editor annotations.
- Keep tool-validity and safety rules mandatory regardless of presentation: use fresh eligible anchors, atomic batches, and safe view handling.

## Workflow

1. Determine the requested Git base. If the user names a branch, tag, or commit, use it. Otherwise infer the repository's default integration branch and state the assumption.
2. Call `sublime.open_diff` once with `base_ref`. Prefer `comparison: "merge_base"` for branch and PR explanations; use `direct` only when the user explicitly wants an exact tree-to-working-tree comparison.
3. Read the returned `opened` entries. Use their `path`, `change_count`, and `annotatable_lines` as the authoritative presentation state.
4. Analyze the actual diff and enough surrounding code to reconstruct its intent and behavior. Unless the user requests another organization, group related edits by capability, data flow, boundary, or architectural decision rather than by file.
5. When the user does not specify a format, draft a narrative that explains the previous limitation, new end-to-end behavior, and the boundaries, contracts, tradeoffs, safety properties, or validation that materially matter. Add Markdown links to the exact files and lines that substantiate the explanation.
6. Select annotation points at the density appropriate to the request. By default, annotate only implementation points where an editor-side explanation adds real depth. Unless the user asks for no annotations, call `sublime.set_annotations` once with `files` entries only for files that contain selected anchors. Pass each file's returned `change_count` as `expected_change_count` and use stable, descriptive annotation IDs.
7. Return the explanation in the requested form. Mention assumptions, uncertainty, and the number of annotations when they are useful to the user.
8. Keep the diff views open by default. If the user asks to close or clean them up, call `sublime.close_views` without targets to close only views opened by this runtime.

## Default Narrative Shape

- Optimize for understanding the PR as a system, not for proving that every file was inspected.
- Explain what existed before, what capability the PR adds or changes, and why the chosen design matters.
- Trace the important runtime or data flow across components when the change spans orchestration, adapters, storage, deployment, or tests.
- Use precise Markdown file links throughout the prose. Link the statement to the code that supports it instead of collecting all links in a separate per-file appendix.
- Prefer grouping by intent, but discuss files individually when the request or shape of the change makes that clearer.
- Avoid repeating the same explanation in chat and in a phantom. Chat supplies the map; annotations supply local depth.

## Annotation Rules

- Annotate only line numbers listed in `annotatable_lines`. These are current-side added or changed lines.
- Never annotate deleted lines, deleted-only hunks, or deleted files. Do not invent a nearby surviving anchor for deleted code.
- By default, do not annotate every file, hunk, or changed symbol. Prefer a small set of high-value annotations for architectural decisions, non-obvious contracts, critical data flow, safety boundaries, or implementation mechanics. Expand coverage when the user requests it while keeping annotations substantive and non-repetitive.
- Place a phantom at the first eligible added or changed line that best represents the idea. The explanation may cover surrounding unchanged code or a broader architectural decision; the anchor must still be one of the returned `annotatable_lines`.
- Match each phantom's depth to the request. By default, make it a genuine drill-down that explains why the design exists, how it works, and what invariant or tradeoff it establishes; avoid label-like one-liners unless the user asks for terse annotations.
- Prefer one annotation that connects several nearby edits over multiple repetitive annotations.
- Explain why the code changed and how behavior or data flow differs. Avoid merely restating the source or duplicating the chat narrative.
- Use Markdown directly. The Sublime host renders it with `mdpopups` when available and a safe minihtml fallback otherwise.
- The annotation batch is atomic: if any file is stale or invalid, no phantoms are changed. Call `sublime.open_diff` again before retrying the batch. Do not place annotations against an outdated buffer.
- Use `sublime.clear_annotations` when the user asks to remove the explanation or before replacing an unrelated explanation group.
- On a follow-up, refresh the diff state before changing annotations, then reshape, narrow, replace, or extend the explanation and atomic batch to match the new request. Preserve the previous structure only when it remains useful.
- `sublime.close_views` closes only clean views opened by this tool runtime. Report any dirty views returned in `skipped` instead of discarding user edits.
- For arbitrary tab-management requests, call `sublime.list_views` first. It returns each view's `view_id`, `group`, and tab index. Pass only the selected `view_ids` to `sublime.close_views`; never use Sublime CLI bulk-close commands.

## Failure Handling

- If `open_diff` skips binary or unavailable files, mention them in chat and continue with the text files it opened.
- If a requested path is outside the bound workspace, stop using the tool for that path and report the scope mismatch.
- If no eligible added or changed files exist, report that result and do not create phantoms.
