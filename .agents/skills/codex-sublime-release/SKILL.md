---
name: codex-sublime-release
description: Prepare and publish CodexSublime releases with the repository's local feature-to-develop-to-master workflow, Codex CLI-aligned versioning, release notes, annotated tags, validation, pushes, and optional GitHub CLI account switching. Use whenever releasing or preparing a release of yaroslavyaroslav/CodexSublime, choosing its next 1.x.y version, synchronizing develop and master for a release, tagging a release, or correcting that release flow.
---

# CodexSublime Release

Release `yaroslavyaroslav/CodexSublime` locally without pull requests. Treat `develop` as the integration branch and `master` as the only release branch.

## Preserve repository state

- Read `AGENTS.md`, inspect `git status --short --branch`, branches, remotes, recent graph, and tags before changing anything.
- Stop if the worktree or index contains unrelated user changes that the release would disturb. Never clean, reset, or overwrite them.
- Fetch `origin`, including tags, before calculating the version or merging.
- Use ordinary local commits and merges. Do not open a PR for feature integration, release preparation, or branch synchronization; this is a single-developer repository.

## Calculate the version

1. Read the latest stable public Codex CLI release from the official `openai/codex` releases source. Do not use the locally installed `codex` version as the authority. Exclude drafts and prereleases unless the user explicitly requests a prerelease.
2. For a stable CLI version `0.N.*`, use plugin release train `1.N.P`:
   - Keep the first component fixed at `1`.
   - Set `N` from the latest public Codex CLI minor.
   - Use patch `P = 0` for the first plugin release after `N` becomes the latest Codex CLI minor.
   - While the same `N` remains latest, increment the highest existing plugin patch: `1.N.0`, `1.N.1`, and so on.
3. Check local and remote tags before selecting the version. Never reuse an existing tag silently.
4. Record both the exact stable Codex CLI version used for compatibility testing and the calculated plugin version. Keep README compatibility text aligned with the exact CLI version.

Example: when the latest stable Codex CLI is `0.147.0` and there is no `1.147.*` release, choose `1.147.0`; another plugin release before a newer Codex CLI minor appears is `1.147.1`.

## Integrate changes locally

Use this branch flow when work was done on a feature branch:

1. Validate and commit the intended feature changes on the feature branch.
2. Update local `develop` from `origin/develop`, then merge the feature branch into `develop` locally. Preserve history with a normal merge when the branches have diverged.
3. Add release notes either on `develop` before the next merge or directly on `master` afterward. In either case, ensure all release files are committed and present on `master` before tagging.
4. Update local `master` from `origin/master`, then merge `develop` into `master` locally.

Do not invent a feature merge when the releasable changes already live on `develop`. Do not tag from `develop`, a feature branch, or a detached HEAD.

## Prepare release files

- Add `messages/<version>.md`.
- Register the exact version and path in `messages.json`.
- Update README compatibility references when the tracked Codex CLI version changes.
- Describe actual user-visible changes and the exact tested Codex CLI version. Do not claim validation that was not performed.
- Commit the release files on `develop` or `master`. They must be reachable from the final `master` release commit.

## Validate before tagging

Run the smallest relevant checks first, then the repository's applicable release checks. At minimum, verify:

```sh
python3 -m unittest discover -s tests
python3 -m unittest discover -s plugin/vendor/sublime_chat_ui/tests
python3 -m compileall -q main.py plugin tests
python3 -m json.tool messages.json
git diff --check
```

Also parse any modified Sublime JSON files and run a live Sublime smoke check for behavior changed by the release. Do not mark the release complete unless the affected plugin behavior has been run successfully and verified.

Before tagging, confirm:

- `HEAD` is `master` and the worktree/index is clean.
- The intended `develop` commit is an ancestor of `master`.
- Release notes and README compatibility text are correct on `master`.
- The chosen tag does not exist locally or on `origin`.

## Tag and push

1. Create an annotated tag on the final `master` commit. Set both the tag name and tag message to the exact version, with no prefix or extra wording:

   ```sh
   git tag -a 1.147.0 -m 1.147.0
   ```

   Substitute the calculated version.
2. Push the intended branch refs and the tag directly to `origin`; use an atomic push when supported. Do not create a pull request. Do not create a GitHub Release object unless the user explicitly asks for one.
3. Verify the remote `develop`, `master`, and peeled annotated-tag targets after pushing. Confirm the tag resolves to the final remote `master` commit.

## Synchronize master-only release changes

After tagging, compare `master` and `develop`:

- If release notes or any other release changes were committed only on `master`, merge `master` back into `develop` locally and push `develop`.
- If `master` contains no changes absent from `develop`, do not create a pointless merge-back commit.

Reverify the remote branch tips after any merge-back.

## Handle GitHub CLI identity

Git operations alone do not require `gh`. If any GitHub CLI command is necessary:

1. Capture the exact currently active GitHub account before switching.
2. Switch the active `github.com` account to `yaroslavyaroslav`.
3. Verify the active login is `yaroslavyaroslav` before performing the GitHub operation.
4. Restore the exact previously active account in a guaranteed cleanup step, even when the GitHub operation fails.
5. Verify restoration and report both the release operation and restoration result.

Never leave `yaroslavyaroslav` active merely because the release work succeeded. If the previous identity cannot be determined or restored, stop and report the blocker instead of guessing.

## Report completion

Report the plugin version, exact Codex CLI version, validation performed, final `develop` and `master` commit IDs, annotated tag target, pushed refs, whether a master-to-develop merge-back was needed, and GitHub CLI identity restoration when `gh` was used.
