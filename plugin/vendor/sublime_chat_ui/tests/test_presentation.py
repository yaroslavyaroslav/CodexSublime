import unittest

from presentation import PanelPresentation, apply_presentation, syntax_resource


class FakeSettings:
    def __init__(self):
        self.values = {}

    def set(self, key, value):
        self.values[key] = value


class FakeView:
    def __init__(self):
        self.syntax = None
        self._settings = FakeSettings()

    def assign_syntax(self, syntax):
        self.syntax = syntax

    def settings(self):
        return self._settings


class PresentationTests(unittest.TestCase):
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

