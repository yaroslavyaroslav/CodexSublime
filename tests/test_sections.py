import unittest

from sections import (
    SectionDocument,
    SectionFormat,
    SectionKey,
    SectionMutation,
    SectionMutationKind,
    parse_sections,
    serialize_section,
)


class SectionGeometryTests(unittest.TestCase):
    def test_parser_ignores_nested_headings_and_fenced_separators(self):
        text = (
            "----------\n\n### Tool call\n\n"
            "```text\n----------\n## not a block\n```\n\n"
            "### nested heading\n\n"
            "----------\n\n## Answer\n\nDone\n\n"
        )

        blocks = parse_sections(text)

        self.assertEqual([block.title for block in blocks], ["Tool call", "Answer"])
        self.assertIn("### nested heading", blocks[0].text)

    def test_format_accepts_host_specific_heading_levels(self):
        section_format = SectionFormat(
            separator="========", heading_levels=frozenset({2})
        )
        rendered = serialize_section("## Answer", "done", section_format=section_format)

        self.assertTrue(rendered.text.startswith("========\n\n## Answer"))
        self.assertEqual(
            parse_sections(rendered.text, section_format)[0].title, "Answer"
        )

    def test_separator_and_guard_are_outside_fold(self):
        rendered = serialize_section("### Command Output", "stdout\n")
        fold = rendered.layout.fold

        self.assertIsNotNone(fold)
        assert fold is not None
        self.assertGreaterEqual(fold.begin, rendered.layout.separator.end)
        self.assertEqual(fold.end, rendered.layout.guard.begin)
        self.assertEqual(rendered.text[rendered.layout.guard.begin :], "\n")

    def test_delta_reduction_returns_a_minimal_buffer_edit(self):
        document = SectionDocument()
        key = SectionKey(namespace="openai", item_id="answer-1")
        document.reduce(
            SectionMutation(
                kind=SectionMutationKind.CREATE,
                key=key,
                header="## Answer",
                body="",
            ),
            buffer_ends_with_newline=True,
        )

        edit = document.reduce(
            SectionMutation(
                kind=SectionMutationKind.APPEND_DELTA,
                key=key,
                header=None,
                body="streamed chunk",
            ),
            buffer_ends_with_newline=True,
        )

        self.assertIsNotNone(edit)
        assert edit is not None
        self.assertLess(edit.end - edit.begin, len(edit.previous.text))
        self.assertIn("streamed chunk", edit.text)


if __name__ == "__main__":
    unittest.main()
