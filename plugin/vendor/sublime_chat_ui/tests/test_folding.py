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
        self.folded = [candidate for candidate in self.folded if candidate != region]

    def is_folded(self, region):
        return region in self.folded


def apply_edit(view, edit):
    view.text = view.text[: edit.begin] + edit.text + view.text[edit.end :]


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


if __name__ == "__main__":
    unittest.main()
