import unittest

from presentation import (
    PanelPresentation,
    append_markdown_section,
    apply_presentation,
    syntax_resource,
)


class FakeSettings:
    def __init__(self):
        self.values = {}

    def set(self, key, value):
        self.values[key] = value


class FakeView:
    def __init__(self, content=""):
        self.syntax = None
        self._settings = FakeSettings()
        self.content = content

    def assign_syntax(self, syntax):
        self.syntax = syntax

    def settings(self):
        return self._settings

    def size(self):
        return len(self.content)

    def substr(self, point):
        return self.content[point]

    def run_command(self, command, args=None):
        if command == "append":
            self.content += args["characters"]


class PresentationTests(unittest.TestCase):
    def test_appends_hard_bounded_markdown_section(self):
        view = FakeView()

        header_start = append_markdown_section(view, "## Answer\n\n", "body\n")

        self.assertEqual(len("----------\n\n"), header_start)
        self.assertEqual("----------\n\n## Answer\n\nbody\n", view.content)

    def test_section_header_starts_on_a_new_line(self):
        view = FakeView("tail")

        header_start = append_markdown_section(view, "## Answer\n\n")

        self.assertEqual(len("tail\n----------\n\n"), header_start)
        self.assertEqual("tail\n----------\n\n## Answer\n\n", view.content)

    def test_headerless_append_does_not_create_a_section_boundary(self):
        view = FakeView("tail\n")

        append_markdown_section(view, "", "continuation")

        self.assertEqual("tail\ncontinuation", view.content)

    def test_syntax_resource_is_relative_to_vendored_package(self):
        self.assertEqual(
            "Packages/Codex/plugin/vendor/sublime_chat_ui/Syntaxes/ChatMarkdown.sublime-syntax",
            syntax_resource("Codex.plugin.vendor.sublime_chat_ui"),
        )

    def test_only_explicit_settings_are_applied(self):
        view = FakeView()
        apply_presentation(
            view,
            PanelPresentation(gutter=True, line_numbers=False),
            "Packages/Test/ChatMarkdown.sublime-syntax",
        )

        self.assertEqual("Packages/Test/ChatMarkdown.sublime-syntax", view.syntax)
        self.assertEqual({"gutter": True, "line_numbers": False}, view.settings().values)


if __name__ == "__main__":
    unittest.main()
