import unittest

from folding import FoldController, SectionRuntime
from sections import SectionKey, SectionMutation, SectionMutationKind, parse_sections


class FakeRegion:
    def __init__(self, begin, end):
        self._begin = begin
        self._end = end

    def begin(self):
        return self._begin

    def end(self):
        return self._end

    def __eq__(self, other):
        return isinstance(other, FakeRegion) and (self.begin(), self.end()) == (
            other.begin(),
            other.end(),
        )


class FakeView:
    def __init__(self, text=""):
        self.text = text
        self.folded = []
        self.unfolded = []
        self._read_only = True

    def id(self):
        return 1

    def buffer_id(self):
        return 1

    def size(self):
        return len(self.text)

    def substr(self, value):
        if isinstance(value, int):
            return self.text[value]
        return self.text[value.begin() : value.end()]

    def fold(self, region):
        if region not in self.folded:
            self.folded.append(region)

    def unfold(self, region):
        self.unfolded.append(region)
        self.folded = [candidate for candidate in self.folded if candidate != region]

    def is_folded(self, region):
        return region in self.folded


def apply_edit(view, edit):
    view.text = view.text[: edit.begin] + edit.text + view.text[edit.end :]


class DriftedFoldView(FakeView):
    """Model a Sublime fold whose stored geometry no longer matches exactly."""

    def unfold(self, region):
        self.unfolded.append(region)
        self.folded = [
            candidate
            for candidate in self.folded
            if candidate.end() <= region.begin() or candidate.begin() >= region.end()
        ]


class PostEditDriftedFoldView(DriftedFoldView):
    """Model Sublime retaining a shortened fold after the buffer edit."""

    def __init__(self, text=""):
        super().__init__(text)
        self.drift_after_edit = False


def apply_edit_with_post_edit_drift(view, edit):
    apply_edit(view, edit)
    if not view.drift_after_edit or edit.block.fold_span is None:
        return
    span = edit.block.fold_span
    view.folded = [FakeRegion(span.begin, span.end - 20)]


def apply_edit_with_following_fold_drift(view, edit):
    delta = len(edit.text) - (edit.end - edit.begin)
    following_folds = []
    for region in view.folded:
        if region.begin() < edit.end:
            following_folds.append(region)
            continue
        shifted_begin = region.begin() + delta
        shifted_end = max(shifted_begin + 1, region.end() + delta - 20)
        following_folds.append(FakeRegion(shifted_begin, shifted_end))
    apply_edit(view, edit)
    view.folded = following_folds


class FoldingTests(unittest.TestCase):
    def test_manual_open_override_survives_policy_reapplication(self):
        block = parse_sections("----------\n\n## Answer\n\nDone\n\n")[0]
        view = FakeView(block.text)
        controller = FoldController()

        controller.apply(view, [block], {"answer"}, FakeRegion)
        view.folded.clear()
        controller.reconcile(view, [block], FakeRegion)
        controller.apply(view, [block], {"answer"}, FakeRegion)

        self.assertEqual(view.folded, [])

    def test_active_section_stays_open_until_finalized(self):
        view = FakeView()
        runtime = SectionRuntime(
            region_factory=FakeRegion,
            edit_applier=apply_edit,
            views_for_buffer=lambda current: [current],
        )
        key = SectionKey(namespace="openai", item_id="answer-1")
        runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.CREATE,
                key=key,
                header="## Answer",
                body="streaming",
            ),
            {"answer"},
            fold_active=False,
        )
        self.assertEqual(view.folded, [])

        block = runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.FINALIZE,
                key=key,
                header=None,
                body="",
            ),
            {"answer"},
            fold_active=False,
        )

        self.assertEqual(len(view.folded), 1)
        self.assertNotIn("----------", view.substr(view.folded[0]))
        self.assertEqual(block.title, "Answer")

    def test_live_update_replaces_drifted_fold_without_forcing_section_open(self):
        view = DriftedFoldView()
        runtime = SectionRuntime(
            region_factory=FakeRegion,
            edit_applier=apply_edit,
            views_for_buffer=lambda current: [current],
        )
        key = SectionKey(namespace="codex", item_id="command-1")
        started = runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.CREATE,
                key=key,
                header="### Command Call",
                body="```bash\necho ok\n```",
            ),
            {"command call", "command output"},
        )
        assert started.fold_span is not None
        view.folded = [
            FakeRegion(started.fold_span.begin, started.fold_span.end - 1)
        ]

        completed = runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.FINALIZE,
                key=key,
                header="### Command Output",
                body="```text\nok\n```",
            ),
            {"command call", "command output"},
        )

        assert completed.fold_span is not None
        self.assertEqual(
            view.folded,
            [FakeRegion(completed.fold_span.begin, completed.fold_span.end)],
        )
        self.assertEqual(
            view.unfolded,
            [
                FakeRegion(started.fold_span.begin, started.fold_span.end),
                FakeRegion(completed.fold_span.begin, completed.fold_span.end),
            ],
        )

    def test_live_update_replaces_fold_fragment_left_by_buffer_edit(self):
        view = PostEditDriftedFoldView()
        runtime = SectionRuntime(
            region_factory=FakeRegion,
            edit_applier=apply_edit_with_post_edit_drift,
            views_for_buffer=lambda current: [current],
        )
        key = SectionKey(namespace="codex", item_id="command-2")
        started = runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.CREATE,
                key=key,
                header="### Command Call",
                body="```bash\nsed -n 1,300p script.sh\n```",
            ),
            {"command call", "command output"},
        )
        view.drift_after_edit = True

        completed = runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.FINALIZE,
                key=key,
                header="### Command Output",
                body="```text\n" + ("output line\n" * 300) + "```",
            ),
            {"command call", "command output"},
        )

        assert started.fold_span is not None
        assert completed.fold_span is not None
        self.assertEqual(
            view.folded,
            [FakeRegion(completed.fold_span.begin, completed.fold_span.end)],
        )
        self.assertEqual(
            view.unfolded[-2:],
            [
                FakeRegion(started.fold_span.begin, started.fold_span.end),
                FakeRegion(completed.fold_span.begin, completed.fold_span.end),
            ],
        )

    def test_post_edit_cleanup_preserves_manual_open_override(self):
        view = PostEditDriftedFoldView()
        runtime = SectionRuntime(
            region_factory=FakeRegion,
            edit_applier=apply_edit_with_post_edit_drift,
            views_for_buffer=lambda current: [current],
        )
        key = SectionKey(namespace="codex", item_id="command-3")
        runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.CREATE,
                key=key,
                header="### Command Call",
                body="```bash\necho ok\n```",
            ),
            {"command call", "command output"},
        )
        view.folded.clear()
        runtime.reconcile(view)
        view.drift_after_edit = True

        runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.FINALIZE,
                key=key,
                header="### Command Output",
                body="```text\n" + ("output line\n" * 300) + "```",
            ),
            {"command call", "command output"},
        )

        self.assertEqual(view.folded, [])

    def test_earlier_live_update_repairs_shifted_following_fold(self):
        view = DriftedFoldView()
        runtime = SectionRuntime(
            region_factory=FakeRegion,
            edit_applier=apply_edit_with_following_fold_drift,
            views_for_buffer=lambda current: [current],
        )
        agent_key = SectionKey(namespace="codex", item_id="agent-1")
        command_key = SectionKey(namespace="codex", item_id="command-4")
        runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.CREATE,
                key=agent_key,
                header="## agent_message",
                body="before",
            ),
            {"command output"},
        )
        runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.FINALIZE,
                key=command_key,
                header="### Command Output",
                body="```text\n" + ("output line\n" * 100) + "```",
            ),
            {"command output"},
        )

        runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.APPEND_DELTA,
                key=agent_key,
                header=None,
                body=" after",
            ),
            {"command output"},
        )

        command = runtime.session_for(view).document.by_key[command_key]
        assert command.fold_span is not None
        self.assertEqual(
            view.folded,
            [FakeRegion(command.fold_span.begin, command.fold_span.end)],
        )

    def test_earlier_live_update_preserves_manual_open_following_section(self):
        view = DriftedFoldView()
        runtime = SectionRuntime(
            region_factory=FakeRegion,
            edit_applier=apply_edit_with_following_fold_drift,
            views_for_buffer=lambda current: [current],
        )
        agent_key = SectionKey(namespace="codex", item_id="agent-2")
        command_key = SectionKey(namespace="codex", item_id="command-5")
        runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.CREATE,
                key=agent_key,
                header="## agent_message",
                body="before",
            ),
            {"command output"},
        )
        runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.FINALIZE,
                key=command_key,
                header="### Command Output",
                body="```text\n" + ("output line\n" * 100) + "```",
            ),
            {"command output"},
        )
        view.folded.clear()
        runtime.reconcile(view)

        runtime.apply_mutation(
            view,
            SectionMutation(
                kind=SectionMutationKind.APPEND_DELTA,
                key=agent_key,
                header=None,
                body=" after",
            ),
            {"command output"},
        )

        self.assertEqual(view.folded, [])


if __name__ == "__main__":
    unittest.main()
